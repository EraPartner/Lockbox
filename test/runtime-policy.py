#!/usr/bin/env python3
"""Disposable in-container runtime fixtures. No credentials or internet needed."""

import argparse
import errno
import json
import os
from pathlib import Path
import pwd
import re
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time


ALLOWED = "api.anthropic.com"
OTHER_ALLOWED = "api.openai.com"
DENIED = "lockbox-denied.invalid"
MARKER = b"lockbox-local-tls-control"


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, timeout=15, **kwargs)


def drop_user(user):
    account = pwd.getpwnam(user)

    def drop():
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)

    return drop


def child(args, user="dev"):
    return run(
        [sys.executable, __file__] + args,
        preexec_fn=drop_user(user),
        env=dict(os.environ, BASH_ENV=""),
    )


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print("PASS: " + message, flush=True)


def counter(table, chain, fragments):
    result = run([table + "-save", "-c"])
    if result.returncode:
        raise AssertionError("cannot read firewall counters")
    matches = []
    for line in result.stdout.decode().splitlines():
        if f"-A {chain} " in line and all(item in line for item in fragments):
            match = re.match(r"\[(\d+):\d+\] ", line)
            if match:
                matches.append(int(match.group(1)))
    if len(matches) != 1:
        raise AssertionError("expected one firewall counter rule")
    return matches[0]


def packet(address, port, protocol):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    kind = socket.SOCK_DGRAM if protocol == "udp" else socket.SOCK_STREAM
    with socket.socket(family, kind) as connection:
        connection.settimeout(0.6)
        try:
            if kind == socket.SOCK_DGRAM:
                connection.sendto(b"lockbox-runtime-probe", (address, port))
            else:
                connection.connect((address, port))
                return 3  # A supposedly denied TCP connection completed.
        except OSError as error:
            if error.errno in (
                errno.ENETUNREACH,
                errno.EHOSTUNREACH,
                errno.EADDRNOTAVAIL,
            ):
                return 2
    return 0


def packet_checks():
    checks = [
        (
            "direct IPv4",
            "iptables",
            "EGRESS_DENY",
            ["-j DROP"],
            "192.0.2.1",
            443,
            "tcp",
            "dev",
        ),
        (
            "metadata IPv4 even for proxy UID",
            "iptables",
            "OUTPUT",
            ["-d 169.254.169.254/32", "-j DROP"],
            "169.254.169.254",
            80,
            "tcp",
            "proxy",
        ),
    ]
    for table, address in (("iptables", "127.0.0.1"), ("ip6tables", "::1")):
        for protocol in ("udp", "tcp"):
            checks.append(
                (
                    f"{protocol.upper()} loopback DNS {address}",
                    table,
                    "OUTPUT",
                    ["-o lo", f"-p {protocol}", "--dport 53", "-j DROP"],
                    address,
                    53,
                    protocol,
                    "dev",
                )
            )
    checks += [
        ("direct IPv6", "ip6tables", "OUTPUT", [], "2001:db8::1", 443, "udp", "dev"),
        (
            "metadata IPv6 even for proxy UID",
            "ip6tables",
            "OUTPUT",
            ["-d fd00:ec2::254/128", "-j DROP"],
            "fd00:ec2::254",
            80,
            "udp",
            "proxy",
        ),
    ]
    for label, table, chain, fragments, address, port, protocol, user in checks:
        # The IPv6 policy counter is reported on the chain header by -L, rather
        # than as an iptables-save rule. All explicit DROP rules use -save.
        policy = label == "direct IPv6"

        def read_counter():
            if not policy:
                return counter(table, chain, fragments)
            result = run([table, "-L", "OUTPUT", "-v", "-n", "-x"])
            match = re.search(rb"policy DROP (\d+) packets", result.stdout)
            if result.returncode or not match:
                raise AssertionError("IPv6 OUTPUT is not default-DROP")
            return int(match.group(1))

        before = read_counter()
        result = child(["packet", address, str(port), protocol], user)
        if result.returncode == 2 and ":" in address and address != "::1":
            print(f"SKIP: {label}: no usable IPv6 route", flush=True)
            continue
        require(
            result.returncode == 0 and read_counter() > before,
            label + " hits its DROP counter",
        )


def generated_hello(name):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    outgoing = ssl.MemoryBIO()
    connection = context.wrap_bio(ssl.MemoryBIO(), outgoing, server_hostname=name)
    try:
        connection.do_handshake()
    except ssl.SSLWantReadError:
        pass
    return outgoing.read()


def add_extension(raw, kind):
    body = raw[9:]
    offset = 34
    offset += 1 + body[offset]
    offset += 2 + int.from_bytes(body[offset : offset + 2], "big")
    offset += 1 + body[offset]
    length = int.from_bytes(body[offset : offset + 2], "big")
    # A dummy encrypted_client_hello payload is enough to prove extension
    # rejection; no encrypted material or real endpoint is involved.
    extra = struct.pack("!HH", kind, 1) + b"\x00"
    body = (
        body[:offset]
        + struct.pack("!H", length + len(extra))
        + body[offset + 2 :]
        + extra
    )
    hello = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + struct.pack("!H", len(hello)) + hello


def connect_proxy():
    connection = socket.create_connection(("127.0.0.1", 3128), timeout=4)
    connection.settimeout(4)
    connection.sendall(
        f"CONNECT {ALLOWED}:443 HTTP/1.1\r\nHost: {ALLOWED}:443\r\n\r\n".encode()
    )
    response = bytearray()
    while not response.endswith(b"\r\n\r\n") and len(response) < 16384:
        chunk = connection.recv(1)
        if not chunk:
            raise AssertionError("proxy closed before CONNECT response")
        response.extend(chunk)
    if response.split(b"\r\n", 1)[0].split()[1:2] != [b"200"]:
        raise AssertionError("allowed CONNECT did not return 200")
    return connection


def tls_client(case):
    with connect_proxy() as connection:
        if case.startswith("valid-"):
            version = (
                ssl.TLSVersion.TLSv1_2 if case == "valid-12" else ssl.TLSVersion.TLSv1_3
            )
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE  # Disposable self-signed fixture only.
            context.minimum_version = version
            context.maximum_version = version
            with context.wrap_socket(connection, server_hostname=ALLOWED) as tls:
                tls.sendall(b"GET / HTTP/1.1\r\nHost: local-fixture\r\n\r\n")
                response = bytearray()
                while MARKER not in response and len(response) < 8192:
                    chunk = tls.recv(1024)
                    if not chunk:
                        break
                    response.extend(chunk)
                return 0 if MARKER in response else 1
        name = (
            None
            if case == "missing"
            else (
                DENIED
                if case == "disallowed"
                else OTHER_ALLOWED
                if case == "mismatched"
                else ALLOWED
            )
        )
        hello = generated_hello(name)
        if case in ("ech", "ech-inner"):
            hello = add_extension(hello, 0xFE0D if case == "ech" else 0xFD00)
        if case == "fragmented":
            payload = hello[5:]
            hello = b"".join(
                b"\x16\x03\x01" + struct.pack("!H", len(piece)) + piece
                for piece in (payload[:2], payload[2:17], payload[17:])
            )
        connection.sendall(hello)
        try:
            response = connection.recv(8192)
        except (ConnectionResetError, BrokenPipeError):
            response = b""
        # Timeout is a test failure, never evidence of local proxy denial.
        return 0 if (bool(response) if case == "fragmented" else not response) else 1


class LocalTLS:
    def __init__(self, context):
        self.context = context
        self.count = 0
        self.lock = threading.Lock()
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(16)
        self.listener.settimeout(0.2)
        self.stopped = threading.Event()
        self.worker = threading.Thread(target=self.accept, daemon=True)
        self.worker.start()

    def accept(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(
                target=self.handle, args=(connection,), daemon=True
            ).start()

    def handle(self, connection):
        with connection:
            connection.settimeout(5)
            try:
                if connection.recv(1, socket.MSG_PEEK):
                    with self.lock:
                        self.count += 1
                with self.context.wrap_socket(connection, server_side=True) as tls:
                    tls.recv(8192)
                    tls.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Length: "
                        + str(len(MARKER)).encode()
                        + b"\r\nConnection: close\r\n\r\n"
                        + MARKER
                    )
            except (OSError, ssl.SSLError):
                pass

    def observed(self):
        with self.lock:
            return self.count

    def close(self):
        self.stopped.set()
        self.listener.close()
        self.worker.join(timeout=1)


def tls_checks():
    hosts = Path("/etc/hosts")
    original = hosts.read_bytes()
    with tempfile.TemporaryDirectory(prefix="lockbox-local-tls-") as directory:
        certificate = Path(directory) / "certificate"
        key = Path(directory) / "key"
        result = run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=lockbox-runtime-fixture",
                "-keyout",
                str(key),
                "-out",
                str(certificate),
            ]
        )
        require(
            result.returncode == 0,
            "disposable TLS certificate generated inside container",
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        server = LocalTLS(context)
        uid = str(pwd.getpwnam("proxy").pw_uid)
        redirect = [
            "OUTPUT",
            "-d",
            "127.0.0.1/32",
            "-p",
            "tcp",
            "--dport",
            "443",
            "-m",
            "owner",
            "--uid-owner",
            uid,
            "-j",
            "REDIRECT",
            "--to-ports",
            str(server.listener.getsockname()[1]),
        ]
        installed = False
        try:
            result = run(["iptables", "-t", "nat", "-I"] + redirect)
            require(
                result.returncode == 0,
                "local proxy-only TLS fixture redirect installed",
            )
            installed = True
            with hosts.open("ab") as stream:
                stream.write(f"\n127.0.0.1 {ALLOWED} {OTHER_ALLOWED}\n".encode())
            require(
                run(
                    ["pkill", "-HUP", "-x", "squid"], preexec_fn=drop_user("proxy")
                ).returncode
                == 0,
                "proxy reloads local fixture hosts",
            )
            time.sleep(1)
            for case in (
                "valid-12",
                "valid-13",
                "fragmented",
                "missing",
                "disallowed",
                "mismatched",
                "ech",
                "ech-inner",
            ):
                before = server.observed()
                result = child(["tls-client", case])
                time.sleep(0.5)
                positive = case.startswith("valid-") or case == "fragmented"
                observed = server.observed() > before
                require(
                    result.returncode == 0 and observed == positive,
                    f"TLS {case}: {'splices to local origin' if positive else 'terminates before origin receives ClientHello'}",
                )
        finally:
            restored = True
            try:
                hosts.write_bytes(original)
            except OSError:
                restored = False
            if installed:
                restored = (
                    run(["iptables", "-t", "nat", "-D"] + redirect).returncode == 0
                    and restored
                )
            restored = (
                run(
                    ["pkill", "-HUP", "-x", "squid"], preexec_fn=drop_user("proxy")
                ).returncode
                == 0
                and restored
            )
            server.close()
            require(restored, "TLS fixture restored hosts and removed its redirect")


def mount_checks(workspace):
    require(os.getuid() == pwd.getpwnam("dev").pw_uid, "mount probes run as dev")
    root = Path(workspace)
    control = root / "ordinary-worktree-file"
    control.write_text("dev write control passed\n")
    require(
        control.read_text() == "dev write control passed\n",
        "ordinary worktree is writable",
    )
    for relative in (
        ".git/config",
        ".git/hooks/probe",
        ".githooks/probe",
        ".devcontainer/probe",
        "nested/.devcontainer/probe",
    ):
        denied = False
        try:
            (root / relative).write_text("must not reach host\n")
        except OSError as error:
            denied = error.errno in (errno.EROFS, errno.EACCES, errno.EPERM)
        require(denied, relative + " refuses dev writes")


def login_checks(provider):
    require(
        os.getuid() == pwd.getpwnam("dev").pw_uid,
        "native login-status probe runs as dev",
    )
    if provider == "claude":
        result = run(["claude", "auth", "status"])
        try:
            logged_out = json.loads(result.stdout).get("loggedIn") is False
        except (ValueError, AttributeError):
            logged_out = False
    else:
        result = run(["codex", "login", "status"])
        logged_out = b"not logged in" in (result.stdout + result.stderr).lower()
    require(
        result.returncode == 1 and logged_out,
        provider + " native status reports logged out in fresh container",
    )


def claude_layout():
    require(
        os.getuid() == pwd.getpwnam("dev").pw_uid,
        "Claude config layout probe runs as dev",
    )
    with tempfile.TemporaryDirectory(prefix="lockbox-claude-layout-") as directory:
        result = run(
            [
                "claude",
                "mcp",
                "add",
                "--scope",
                "user",
                "--transport",
                "http",
                "lockbox-layout-probe",
                "https://127.0.0.1:9/mcp",
            ],
            env=dict(os.environ, CLAUDE_CONFIG_DIR=directory),
        )
        state = Path(directory) / ".claude.json"
        require(
            result.returncode == 0 and state.is_file() and state.stat().st_size > 0,
            "Claude global configuration stays inside CLAUDE_CONFIG_DIR",
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=("network", "packet", "tls-client", "mounts", "login", "claude-layout"),
    )
    parser.add_argument("arguments", nargs="*")
    args = parser.parse_args()
    if args.mode == "packet":
        return packet(args.arguments[0], int(args.arguments[1]), args.arguments[2])
    if args.mode == "tls-client":
        return tls_client(args.arguments[0])
    if args.mode == "mounts":
        mount_checks(args.arguments[0])
    elif args.mode == "login":
        login_checks(args.arguments[0])
    elif args.mode == "claude-layout":
        claude_layout()
    else:
        require(os.getuid() == 0, "network fixture orchestrator is container root")
        packet_checks()
        tls_checks()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as error:
        print("FAIL: " + str(error), file=sys.stderr)
        sys.exit(1)
    except (OSError, subprocess.TimeoutExpired):
        print(
            "FAIL: runtime policy fixture did not establish its required control",
            file=sys.stderr,
        )
        sys.exit(1)

#!/usr/bin/env python3
"""Squid external ACL: require visible, valid SNI and reject ECH before splicing.

Input is Squid's base64 %>handshake and optionally its %>rd hostname. Squid
may append an unused %DATA field, represented by '-'. No TLS data is logged.
"""

import base64
import binascii
import ipaddress
import re
import sys
from urllib.parse import unquote_to_bytes


MAX_RAW_BYTES = 131072
MAX_HELLO_BYTES = 65536
MAX_RECORDS = 64
MAX_LINE_BYTES = ((MAX_RAW_BYTES + 2) // 3) * 4 + 1024
ECH_EXTENSIONS = {0xFE0D, 0xFD00}
DNS_LABEL = re.compile(rb"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\Z")


class InvalidHello(ValueError):
    """Malformed or unsupported handshake; the only safe result is denial."""


class Reader:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def take(self, length):
        end = self.offset + length
        if length < 0 or end > len(self.data):
            raise InvalidHello()
        result = self.data[self.offset : end]
        self.offset = end
        return result

    def number(self, width):
        return int.from_bytes(self.take(width), "big")

    def vector(self, width):
        return self.take(self.number(width))

    def done(self):
        if self.offset != len(self.data):
            raise InvalidHello()


def hostname(value):
    """Accept RFC 6066 DNS names, including ASCII IDNA labels, but not IPs."""
    if not value or len(value) > 253:
        raise InvalidHello()
    if any(not DNS_LABEL.fullmatch(label) for label in value.split(b".")):
        raise InvalidHello()
    try:
        ipaddress.ip_address(value.decode("ascii"))
    except ValueError:
        return value.lower()
    raise InvalidHello()


def clienthello_sni(raw):
    """Parse bounded TLS records containing exactly one complete ClientHello."""
    if not raw or len(raw) > MAX_RAW_BYTES:
        raise InvalidHello()
    records = Reader(raw)
    handshake = bytearray()
    count = 0
    while records.offset < len(raw):
        count += 1
        if count > MAX_RECORDS or records.number(1) != 22:
            raise InvalidHello()
        if records.take(2) not in (b"\x03\x01", b"\x03\x02", b"\x03\x03"):
            raise InvalidHello()
        record = records.vector(2)
        if not record or len(record) > 16384:
            raise InvalidHello()
        handshake.extend(record)
        if len(handshake) > MAX_HELLO_BYTES + 4:
            raise InvalidHello()
        if len(handshake) >= 4:
            if handshake[0] != 1:
                raise InvalidHello()
            wanted = int.from_bytes(handshake[1:4], "big") + 4
            if wanted > MAX_HELLO_BYTES + 4 or len(handshake) > wanted:
                raise InvalidHello()
            if len(handshake) == wanted:
                records.done()
                break
    message = Reader(bytes(handshake))
    if message.number(1) != 1:
        raise InvalidHello()
    hello = Reader(message.vector(3))
    message.done()
    if hello.take(2) not in (b"\x03\x01", b"\x03\x02", b"\x03\x03"):
        raise InvalidHello()
    hello.take(32)
    if len(hello.vector(1)) > 32:
        raise InvalidHello()
    ciphers = hello.vector(2)
    if not ciphers or len(ciphers) % 2:
        raise InvalidHello()
    compression = hello.vector(1)
    if not compression or 0 not in compression:
        raise InvalidHello()
    extensions = Reader(hello.vector(2))
    hello.done()
    seen = set()
    sni = None
    while extensions.offset < len(extensions.data):
        kind = extensions.number(2)
        data = extensions.vector(2)
        if kind in seen or kind in ECH_EXTENSIONS:
            raise InvalidHello()
        seen.add(kind)
        if kind == 0:
            extension = Reader(data)
            names = Reader(extension.vector(2))
            extension.done()
            # RFC 6066 currently defines only host_name(0). Require exactly one.
            if names.number(1) != 0:
                raise InvalidHello()
            sni = hostname(names.vector(2))
            names.done()
    if sni is None:
        raise InvalidHello()
    return sni


def accept_line(line):
    if len(line) > MAX_LINE_BYTES:
        return False
    fields = line.split()
    if len(fields) == 3 and fields[-1] == b"-":
        fields.pop()
    if len(fields) not in (1, 2):
        return False
    try:
        raw = base64.b64decode(unquote_to_bytes(fields[0]), validate=True)
        sni = clienthello_sni(raw)
        if len(fields) == 2 and sni != hostname(unquote_to_bytes(fields[1])):
            return False
    except (InvalidHello, ValueError, binascii.Error):
        return False
    return True


def serve(source, destination):
    while True:
        line = source.readline(MAX_LINE_BYTES + 1)
        if not line:
            return
        oversized = len(line) > MAX_LINE_BYTES
        if oversized and not line.endswith(b"\n"):
            # Drain this request without retaining its contents or desynchronizing
            # subsequent helper requests. Emit exactly one denial for the line.
            while line and not line.endswith(b"\n"):
                line = source.readline(MAX_LINE_BYTES + 1)
        accepted = not oversized and accept_line(line)
        destination.write(b"OK\n" if accepted else b"ERR\n")
        destination.flush()


if __name__ == "__main__":
    try:
        serve(sys.stdin.buffer, sys.stdout.buffer)
    except OSError:
        # An unusable helper must never manufacture an acceptance or disclose data.
        sys.exit(1)

# ─── vendored by LockBox v0.1.0 · canonical sha256:adc277ee9379b8d575e84384199bf3d86881f68d9e7a5349f2ecbe98456e1fb8 ───
# Generated from the canonical source by LockBox/sync.sh — DO NOT EDIT HERE.
# Edit LockBox/clienthello-policy.py and re-run ./sync.sh.

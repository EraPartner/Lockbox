#!/usr/bin/env python3
"""Opt-in Apple Container policy tests using the actual generic launcher.

Run LOCKBOX_NATIVE_LAUNCHER_TEST=1 python3 test/test-native-launcher.py.
Requires an already built lockbox-generic:egress-test image. No login, host
authentication, SSH, image build, or real repository Git mutation is used.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "lockbox-generic:egress-test"
ENABLED = os.environ.get("LOCKBOX_NATIVE_LAUNCHER_TEST") == "1"


@unittest.skipUnless(ENABLED, "opt in to native Apple Container integration")
class NativeLauncherTests(unittest.TestCase):
    def setUp(self):
        self.runtime = shutil.which("container")
        self.assertIsNotNone(self.runtime, "Apple Container is required")
        self.command("system", "status")
        self.command("image", "inspect", IMAGE)
        self.tmp = tempfile.TemporaryDirectory(prefix="lockbox-native-launcher-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.prefix = "lockbox-native-" + uuid.uuid4().hex[:12]
        self.names = []
        self.volumes = []
        self.addCleanup(self.cleanup_resources)
        self.fixture = self.base / "fixture"
        self.dc = self.fixture / "sandbox/.devcontainer"
        shutil.copytree(ROOT / "sandbox/.devcontainer", self.dc)
        helpers = self.fixture / ".devcontainer"
        helpers.mkdir()
        for name in (
            "agent-config.py",
            "agent-config-launcher.sh",
            "launch-lock.py",
            "claude-guard.mjs",
        ):
            shutil.copy2(ROOT / ".devcontainer" / name, helpers / name)
        # Only fixture paths, resource names and the prebuilt image differ. All
        # lifecycle, readiness, importer and pin verification logic stays real.
        for path in (
            self.dc / "bin/dev",
            self.dc / "launcher-common.sh",
            helpers / "agent-config-launcher.sh",
        ):
            contents = path.read_text().replace("$HOME", "$LOCKBOX_TEST_HOME")
            if path.name == "dev":
                self.assertEqual(contents.count('IMAGE="dev-sandbox:latest"'), 1)
                contents = contents.replace(
                    'IMAGE="dev-sandbox:latest"', f'IMAGE="{IMAGE}"'
                )
                contents = contents.replace("dev-sandbox-$HASH", self.prefix + "-$HASH")
                contents = contents.replace(
                    "dev-sandbox-codex-$HASH", self.prefix + "-codex-$HASH"
                )
                for suffix in ("$AGENT", "config", "local"):
                    contents = contents.replace(
                        f"dev-sandbox-{suffix}-$HASH",
                        f"{self.prefix}-{suffix}-$HASH",
                    )
            path.write_text(contents)
        self.home = self.base / "synthetic-home"
        self.home.mkdir()
        self.source = self.base / "authored-source"
        self.source.mkdir()
        self.shim_dir = self.base / "bin"
        self.shim_dir.mkdir()
        self.log = self.base / "runtime.jsonl"
        self.fail_stop = self.base / "fail-stop-once"
        shim = self.shim_dir / "container"
        shim.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "with open(os.environ['LOCKBOX_TEST_RUNTIME_LOG'], 'a') as log:\n"
            "    log.write(json.dumps(args) + '\\n')\n"
            "if args[:1] == ['build']:\n"
            "    sys.exit('fixture refuses image builds')\n"
            "if args[:1] == ['stop']:\n"
            "    flag = pathlib.Path(os.environ['LOCKBOX_TEST_FAIL_STOP'])\n"
            "    if flag.exists():\n"
            "        flag.unlink()\n"
            "        sys.exit('injected one-time native stop failure')\n"
            "os.execv(os.environ['LOCKBOX_TEST_CONTAINER'], [os.environ['LOCKBOX_TEST_CONTAINER'], *args])\n"
        )
        shim.chmod(0o755)
        # Pass only required host environment. In particular, no credentials,
        # host startup hooks, proxy settings or SSH_AUTH_SOCK reach the fixture.
        self.env = {
            "PATH": ":".join(
                (
                    str(self.shim_dir),
                    str(Path(sys.executable).parent),
                    "/opt/homebrew/bin",
                    "/usr/local/bin",
                    "/usr/bin",
                    "/bin",
                    "/usr/sbin",
                    "/sbin",
                )
            ),
            "LOCKBOX_TEST_HOME": str(self.home),
            "LOCKBOX_TEST_CONTAINER": self.runtime,
            "LOCKBOX_TEST_RUNTIME_LOG": str(self.log),
            "LOCKBOX_TEST_FAIL_STOP": str(self.fail_stop),
            "LOCKBOX_CONFIG_SOURCE": str(self.source),
            "LOCKBOX_CONFIG_IMPORT_NATIVE": "0",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "DEV_SANDBOX_SHELL": "1",
            "DEV_SANDBOX_STOP_ON_EXIT": "0",
        }
        self.trusted = self.dc / "allowlist.txt"
        self.original_allowlist = self.trusted.read_text()

    def command(self, *args, success=True):
        result = subprocess.run(
            [self.runtime, *args], capture_output=True, text=True, timeout=90
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def cleanup_resources(self):
        failures = []
        for name in self.names:
            result = self.command("rm", "-f", name, success=False)
            if result.returncode:
                failures.append(result.stderr)
        existing = self.command("volume", "ls", "--format", "json", success=False)
        if existing.returncode:
            failures.append(existing.stderr)
        else:
            present = {
                item["configuration"]["name"] for item in json.loads(existing.stdout)
            }
            for name in self.volumes:
                if name in present:
                    result = self.command("volume", "delete", name, success=False)
                    if result.returncode:
                        failures.append(result.stderr)
        self.assertFalse(failures, "disposable cleanup failed: " + "\n".join(failures))

    def launch(self, provider, repo, *, success=True):
        self.log.write_text("")
        result = subprocess.run(
            [str(self.dc / "bin" / ("dev-" + provider))],
            cwd=repo,
            env=self.env,
            input="exit\n",
            capture_output=True,
            text=True,
            timeout=180,
        )
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        if success and result.returncode:
            # Native bind-mount failures can occur even when inspect records
            # the requested mount. Include narrowly scoped runtime evidence.
            name = next(
                (
                    call[call.index("--name") + 1]
                    for call in calls
                    if call[:1] == ["run"]
                ),
                None,
            )
            details = ""
            if name is not None:
                probe = self.command(
                    "exec",
                    name,
                    "bash",
                    "-c",
                    "stat -c '%u %g %a %n' /home/dev/.agent-stage-root /home/dev/.agent-stage-root/config 2>&1; cat /proc/mounts",
                    success=False,
                )
                details = probe.stdout + probe.stderr
            self.fail(result.stderr + result.stdout + details)
        else:
            if not success:
                self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        return result, calls

    def generation(self, name):
        result = self.command(
            "exec",
            "--user",
            "dev",
            "-e",
            "BASH_ENV=",
            name,
            "/opt/python/bin/python3",
            "-c",
            "from pathlib import Path; print(Path('/proc/sys/kernel/random/boot_id').read_text().strip() + ':' + Path('/proc/1/stat').read_text().rsplit(') ', 1)[1].split()[19])",
        )
        return result.stdout.strip()

    def assert_confirmation(self, stage, name):
        digest = hashlib.sha256((stage / "allowlist.txt").read_bytes()).hexdigest()
        self.assertEqual(
            (stage / "allowlist.applied").read_text().strip(),
            f"lockbox-allowlist-v1 {self.generation(name)} {digest}",
        )
        mounted = self.command(
            "exec",
            "--user",
            "dev",
            "-e",
            "BASH_ENV=",
            name,
            "sha256sum",
            "/etc/squid/allowlist.txt",
        ).stdout.split()[0]
        self.assertEqual(mounted, digest)

    def assert_transition(self, calls, name):
        stop = calls.index(["stop", name])
        start = calls.index(["start", name])
        self.assertLess(stop, start)
        self.assertFalse(any(call[:1] == ["run"] for call in calls))

    def assert_profile_cannot_bypass_readiness(self, name):
        # Exercise the actual readiness helper, with only its local port changed
        # to a verified closed port and its retry loop shortened. The container
        # profile must not turn a failed trusted check into a successful launch.
        closed = self.command(
            "exec",
            "--user",
            "dev",
            "-e",
            "BASH_ENV=",
            name,
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            "(exec 3<>/dev/tcp/127.0.0.1/1) 2>/dev/null",
            success=False,
        )
        self.assertNotEqual(
            closed.returncode, 0, "fixture loopback port must be closed"
        )
        profile = "/home/dev/.bash_profile"
        self.command(
            "exec",
            "--user",
            "dev",
            "-e",
            "BASH_ENV=",
            name,
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            f"test ! -e {profile} && printf 'exit 0\\n' > {profile}",
        )
        try:
            vulnerable = self.command(
                "exec",
                "--user",
                "dev",
                "-e",
                "BASH_ENV=",
                name,
                "/bin/bash",
                "-lc",
                "exit 1",
            )
            self.assertEqual(
                vulnerable.returncode, 0, "legacy shell must demonstrate the bypass"
            )
            helper = self.fixture / ".devcontainer/agent-config-launcher.sh"
            modified = (
                helper.read_text()
                .replace("127.0.0.1/3128", "127.0.0.1/1")
                .replace("seq 1 150", "seq 1 1")
            )
            self.assertNotEqual(modified, helper.read_text())
            probe = self.base / "readiness-probe.sh"
            probe.write_text(modified)
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    'source "$1"; lockbox_wait_proxy "$2"',
                    "probe",
                    str(probe),
                    name,
                ],
                env=self.env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertNotEqual(
                result.returncode, 0, "private shell profile bypassed trusted readiness"
            )
            self.assertIn("egress proxy did not become ready", result.stderr)
        finally:
            self.command(
                "exec",
                "--user",
                "dev",
                "-e",
                "BASH_ENV=",
                name,
                "bash",
                "--noprofile",
                "--norc",
                "-c",
                f"rm {profile}",
            )

    def test_actual_launcher_policy_transitions_for_both_providers(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                self.trusted.write_text(self.original_allowlist)
                repo = self.base / (provider + "-repo")
                (repo / ".git/hooks").mkdir(parents=True)
                (repo / ".git/objects").mkdir()
                (repo / ".git/refs/heads").mkdir(parents=True)
                (repo / ".git/HEAD").write_text("ref: refs/heads/main\n")
                (repo / ".git/config").write_text(
                    "[core]\n    repositoryformatversion = 0\n"
                )
                digest = hashlib.sha1(str(repo).encode()).hexdigest()[:12]
                name = (
                    self.prefix
                    + ("-codex" if provider == "codex" else "")
                    + "-"
                    + digest
                )
                self.names.append(name)
                self.volumes.extend(
                    f"{self.prefix}-{part}-{digest}"
                    for part in (provider, "config", "local")
                )
                stage = self.home / ".claude-sandbox/stage" / name

                _, calls = self.launch(provider, repo)
                self.assertTrue(any(call[:1] == ["run"] for call in calls))
                self.assert_confirmation(stage, name)
                fresh = self.generation(name)

                _, calls = self.launch(provider, repo)
                self.assertFalse(
                    any(call[:1] in (["run"], ["start"], ["stop"]) for call in calls)
                )
                self.assertEqual(self.generation(name), fresh)
                self.assert_confirmation(stage, name)
                self.assert_profile_cannot_bypass_readiness(name)

                (stage / "allowlist.applied").write_text("invalid confirmation\n")
                _, calls = self.launch(provider, repo)
                self.assert_transition(calls, name)
                self.assertNotEqual(self.generation(name), fresh)
                self.assert_confirmation(stage, name)

                before_failure = self.generation(name)
                unchanged_bytes = (stage / "allowlist.txt").read_bytes()
                # Narrow a host trusted by the fixture, never the real source.
                retired = "registry.npmjs.org"
                self.assertIn(retired, self.trusted.read_text().splitlines())
                self.trusted.write_text(
                    "\n".join(
                        line
                        for line in self.trusted.read_text().splitlines()
                        if line != retired
                    )
                    + "\n"
                )
                self.fail_stop.write_text("fail the next exact stop only\n")
                failed, calls = self.launch(provider, repo, success=False)
                self.assertIn("container stop failed", failed.stderr)
                self.assertFalse((stage / "allowlist.applied").exists())
                self.assertEqual(
                    (stage / "allowlist.txt").read_bytes(), unchanged_bytes
                )
                self.assertEqual(self.generation(name), before_failure)
                self.assertIn(["stop", name], calls)
                self.assertNotIn(["start", name], calls)

                _, calls = self.launch(provider, repo)
                self.assert_transition(calls, name)
                self.assertNotEqual(self.generation(name), before_failure)
                self.assert_confirmation(stage, name)
                self.assertNotIn(
                    retired, (stage / "allowlist.txt").read_text().splitlines()
                )
                # Headless untrusted overlay cannot re-add the retired host.
                (repo / ".sandbox-allowlist.txt").write_text(retired + "\n")
                narrowed = self.generation(name)
                result, calls = self.launch(provider, repo)
                self.assertIn("IGNORING the overlay", result.stderr)
                self.assertEqual(self.generation(name), narrowed)
                self.assertFalse(
                    any(call[:1] in (["start"], ["stop"]) for call in calls)
                )
                # Squid can serve a TLS error page using its own fixture
                # certificate. Read that denial and require HTTP 403; a mere
                # certificate error, timeout or successful origin response
                # would not prove that the narrowed policy took effect.
                denied = self.command(
                    "exec",
                    "--user",
                    "dev",
                    "-e",
                    "BASH_ENV=",
                    name,
                    "curl",
                    "-sS",
                    "--insecure",
                    "--fail",
                    "--max-time",
                    "10",
                    "-x",
                    "http://127.0.0.1:3128",
                    "-o",
                    "/dev/null",
                    "-w",
                    "%{http_code}",
                    "https://" + retired,
                    success=False,
                )
                self.assertNotEqual(denied.returncode, 0, denied.stdout)
                self.assertEqual(denied.stdout, "403", denied.stderr)
                print(
                    f"{provider}: fresh boot, warm reuse, invalid confirmation restart, stop failure/retry, and retired overlay denial passed",
                    flush=True,
                )
                self.command("stop", name)


if __name__ == "__main__":
    unittest.main()

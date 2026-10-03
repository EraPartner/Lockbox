#!/usr/bin/env python3
"""Exercise sync failures in a disposable tree, without Git or container writes."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in (
            "sync.sh",
            "paths.sh",
            "VERSION",
            "base-allowlist.txt",
            "vendored-files.txt",
            "init-firewall.sh",
            "squid.conf",
            "launcher-common.sh",
            "claude-stage-items.txt",
            "clienthello-policy.py",
            "claude-guard.mjs",
        ):
            shutil.copy2(ROOT / name, self.root / name)
        for name in (".devcontainer", "sandbox/.devcontainer"):
            (self.root / name).mkdir(parents=True)

    def run_sync(self, *args, extra_env=None):
        return subprocess.run(
            ["bash", str(self.root / "sync.sh"), *args],
            env={
                **os.environ,
                "EGRESS_SELF_ONLY": "1",
                "EGRESS_REPO": str(self.root),
                **(extra_env or {}),
            },
            capture_output=True,
            text=True,
        )

    def test_both_targets_checked(self):
        self.assertEqual(self.run_sync().returncode, 0)
        result = self.run_sync("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in (".devcontainer", "sandbox/.devcontainer"):
            target = self.root / name / "squid.conf"
            original = target.read_bytes()
            target.write_text("drift\n")
            self.assertNotEqual(self.run_sync("--check").returncode, 0)
            target.write_bytes(original)

    def test_firewall_embeds_exact_companion_and_tracks_its_drift(self):
        result = self.run_sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        firewall = self.root / ".devcontainer/init-firewall.sh"
        body = firewall.read_text()
        embedded = body.split("<<'LOCKBOX_CLIENTHELLO_SOURCE'\n", 1)[1].split(
            "\nLOCKBOX_CLIENTHELLO_SOURCE\n", 1
        )[0]
        self.assertEqual(
            embedded + "\n", (self.root / "clienthello-policy.py").read_text()
        )
        self.assertEqual(subprocess.run(["bash", "-n", str(firewall)]).returncode, 0)
        with (self.root / "clienthello-policy.py").open("a") as stream:
            stream.write("# changed companion\n")
        result = self.run_sync("--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("init-firewall.sh", result.stderr)

    def test_companion_install_failure_refuses_readiness_sentinel(self):
        source = (ROOT / "init-firewall.sh").read_text()
        verification = source.split(
            "# --- Verify the lock took, then drop the sentinel ---", 1
        )[1]
        sentinel = self.root / "sentinel"
        result = subprocess.run(
            [
                "bash",
                "-c",
                """
SENTINEL="$1"
PROXY_USER=proxy
iptables() { [[ "$1" != -S ]] || echo '-P OUTPUT DROP'; }
ip6tables() { echo '-P OUTPUT DROP'; }
lockbox_install_clienthello_policy() { return 1; }
"""
                + verification,
                "fixture",
                str(sentinel),
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(sentinel.exists())

    def test_missing_source_refused_before_any_target_write(self):
        (self.root / "squid.conf").unlink()
        for args in ((), ("--check",)):
            result = self.run_sync(*args)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("canonical input: squid.conf", result.stderr)
            self.assertFalse((self.root / ".devcontainer/init-firewall.sh").exists())

    def test_target_must_be_managed_and_only_selected_target_changes(self):
        self.assertNotEqual(self.run_sync("--target=/unmanaged").returncode, 0)
        result = self.run_sync(f"--target={self.root}/.devcontainer")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / ".devcontainer/squid.conf").is_file())
        self.assertFalse((self.root / "sandbox/.devcontainer/squid.conf").exists())

    def test_selected_sandbox_keeps_its_registered_label(self):
        result = self.run_sync(f"--target={self.root}/sandbox/.devcontainer")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sandbox/allowlist.extra.txt",
                      (self.root / "sandbox/.devcontainer/allowlist.txt").read_text())

    def test_fresh_clone_defaults_to_repository_targets(self):
        result = self.run_sync(extra_env={"EGRESS_SELF_ONLY": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 synced, 0 skipped", result.stdout)
        self.assertIn("LockBox/allowlist.extra.txt",
                      (self.root / ".devcontainer/allowlist.txt").read_text())

    def test_local_fleet_data_preserves_spaces_and_stable_labels(self):
        target = self.root / "alternate checkout/.devcontainer"
        target.mkdir(parents=True)
        (self.root / "paths.local.tsv").write_text(f"{target}\tshared-project\n")
        result = self.run_sync(extra_env={"EGRESS_SELF_ONLY": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("shared-project/allowlist.extra.txt", (target / "allowlist.txt").read_text())
        self.assertEqual(self.run_sync("--check", extra_env={"EGRESS_SELF_ONLY": "0"}).returncode, 0)

    def test_ci_ignores_local_fleet_data(self):
        (self.root / "paths.local.tsv").write_text("invalid entry\n")
        self.assertEqual(self.run_sync().returncode, 0)

    def test_bad_local_configuration_fails_before_writes(self):
        for entry in ("relative/.devcontainer\n", f"{self.root}/.devcontainer\n",
                      "/missing/project/.devcontainer\tbad/label\n"):
            with self.subTest(entry=entry):
                (self.root / "paths.local.tsv").write_text(entry)
                result = self.run_sync(extra_env={"EGRESS_SELF_ONLY": "0"})
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / ".devcontainer/squid.conf").exists())

    def test_missing_explicit_fleet_file_is_an_error(self):
        result = self.run_sync(extra_env={"EGRESS_SELF_ONLY": "0",
                                          "EGRESS_FLEET_FILE": str(self.root / "missing.tsv")})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fleet file is missing", result.stderr)

    def test_missing_registered_target_remains_an_error(self):
        (self.root / "paths.local.tsv").write_text("/missing/project/.devcontainer\n")
        result = self.run_sync("--check", extra_env={"EGRESS_SELF_ONLY": "0"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("1 missing target", result.stderr)


if __name__ == "__main__":
    unittest.main()

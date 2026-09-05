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
        ):
            shutil.copy2(ROOT / name, self.root / name)
        for name in (".devcontainer", "sandbox/.devcontainer"):
            (self.root / name).mkdir(parents=True)

    def run_sync(self, *args):
        return subprocess.run(
            ["bash", str(self.root / "sync.sh"), *args],
            env={**os.environ, "EGRESS_SELF_ONLY": "1", "EGRESS_REPO": str(self.root)},
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


if __name__ == "__main__":
    unittest.main()

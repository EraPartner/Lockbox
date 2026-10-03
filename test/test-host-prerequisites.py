#!/usr/bin/env python3
"""Verify the host policy runtime fails clearly before container operations."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HostPrerequisitesTests(unittest.TestCase):
    def check_node(self, node_source=None):
        with tempfile.TemporaryDirectory() as temporary:
            bin_dir = Path(temporary)
            if node_source is not None:
                node = bin_dir / "node"
                node.write_text(node_source)
                node.chmod(0o755)
            return subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    'source "$1"; sandbox_require_host_node',
                    "host-prerequisite-test",
                    str(ROOT / "launcher-common.sh"),
                ],
                env={**os.environ, "PATH": str(bin_dir)},
                capture_output=True,
                text=True,
            )

    def test_missing_or_unusable_node_has_actionable_diagnostic(self):
        for source in (None, "#!/bin/sh\nexit 1\n"):
            with self.subTest(source=source):
                result = self.check_node(source)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("host Node.js 18 or newer", result.stderr)
                self.assertNotIn("command not found", result.stderr)

    def test_installed_runtime_passes(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is not installed")
        result = self.check_node(f'#!/bin/sh\nexec "{node}" "$@"\n')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_launchers_preflight_before_staging_and_build(self):
        for relative in (".devcontainer/bin/agent", "sandbox/.devcontainer/bin/dev"):
            with self.subTest(launcher=relative):
                source = (ROOT / relative).read_text()
                preflight = source.index("\nsandbox_require_host_node\n")
                self.assertLess(preflight, source.index('mkdir -p "$STAGE_DIR"'))
                self.assertLess(preflight, source.index("container build -"))


if __name__ == "__main__":
    unittest.main()

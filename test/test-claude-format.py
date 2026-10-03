#!/usr/bin/env python3
"""Verify that Rust formatting leaves child modules and failed input intact."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

FORMATTER = Path(__file__).resolve().parents[1] / "claude-explicit-format.sh"


@unittest.skipUnless(
    shutil.which("rustfmt"), "rustfmt unavailable; Rust behavior not verified"
)
class RustFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        # Git metadata belongs only to this disposable synthetic worktree.
        subprocess.run(
            [
                "git",
                "-c",
                "init.defaultBranch=fixture",
                "init",
                "--quiet",
                str(self.project),
            ],
            check=True,
            capture_output=True,
        )

    def test_only_requested_file_is_formatted(self):
        outside = self.base / "outside.rs"
        sibling = self.project / "sibling.rs"
        child_source = 'pub fn fixture(){println!("fixture");}\n'
        outside.write_text(child_source)
        sibling.write_text(child_source)
        main = self.project / "main.rs"
        main.write_text(
            '#[path = "../outside.rs"]\nmod outside;\nmod sibling;\nfn main(){outside::fixture();sibling::fixture();}\n'
        )
        main.chmod(0o640)
        result = subprocess.run(
            ["bash", str(FORMATTER), str(main)], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fn main() {", main.read_text())
        self.assertEqual(main.stat().st_mode & 0o777, 0o640)
        self.assertEqual(outside.read_text(), child_source)
        self.assertEqual(sibling.read_text(), child_source)

    def test_config_is_discovered_from_source_directory(self):
        (self.project / "rustfmt.toml").write_text("hard_tabs = true\n")
        main = self.project / "main.rs"
        main.write_text('fn main(){println!("fixture");}\n')
        result = subprocess.run(
            ["bash", str(FORMATTER), str(main)],
            cwd=self.base,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("\tprintln!", main.read_text())

    def test_failed_rustfmt_keeps_original(self):
        main = self.project / "main.rs"
        source = "fn main( { invalid fixture\n"
        main.write_text(source)
        result = subprocess.run(
            ["bash", str(FORMATTER), str(main)], capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(main.read_text(), source)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Exercise permission monitoring with isolated synthetic policy and state."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PostEditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        (self.project / "src").mkdir(parents=True)
        (self.project / ".claude").mkdir()
        hooks = self.base / "hooks"
        hooks.mkdir()
        self.hook = hooks / "post-edit.mjs"
        shutil.copyfile(ROOT / "claude-post-edit.mjs", self.hook)
        (hooks / "watched-projects.json").write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "name": "fixture",
                            "root": str(self.project),
                            "autoFormat": True,
                            "monitorPermissions": True,
                        }
                    ]
                }
            )
        )
        managed = self.base / "managed"
        managed.mkdir()
        formatter = managed / "explicit-format"
        formatter.write_text('#!/bin/sh\nprintf "%s\\n" "$2" >> "$FORMAT_LOG"\n')
        formatter.chmod(0o700)
        self.log = self.base / "format.log"
        self.state = self.base / "state"

    def invoke(self, file):
        env = dict(
            os.environ,
            CODEX_PERMISSION_WATCH_STATE_DIR=str(self.state),
            FORMAT_LOG=str(self.log),
        )
        result = subprocess.run(
            ["node", str(self.hook)],
            input=json.dumps(
                {
                    "cwd": str(self.project / "src"),
                    "tool_input": {"file_path": str(file)},
                }
            ),
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stderr, "")
        return result.stdout

    def settings(self, name, rules):
        path = self.project / ".claude" / name
        path.write_text(json.dumps({"permissions": {"allow": rules}}))
        return path

    def test_nested_cwd_monitors_both_files_with_independent_state(self):
        rule = "Read(private-fixture-value)"
        shared = self.settings("settings.json", [rule])
        private = self.settings("settings.local.json", [rule])
        for file in (shared, private):
            output = self.invoke(file)
            self.assertIn("grew by 1 rule(s)", output)
            self.assertNotIn(rule, output)
            self.assertNotIn(str(file), output)
            self.assertEqual(self.invoke(file), "")
        states = list(self.state.glob("*.json"))
        self.assertEqual(len(states), 2)
        for state in states:
            self.assertNotIn(rule, state.read_text())
        private = self.settings(
            "settings.local.json", [rule, "Bash(private-other-value)"]
        )
        self.assertIn("grew by 1 rule(s)", self.invoke(private))
        self.assertEqual(self.invoke(shared), "")

    def test_parent_relative_edit_is_formatted_from_nested_cwd(self):
        file = self.project / "root.md"
        file.write_text("fixture\n")
        self.assertEqual(self.invoke("../root.md"), "")
        self.assertEqual(self.log.read_text().strip(), str(file.resolve()))

    def test_symlink_escape_is_not_monitored_or_formatted(self):
        outside = self.base / "outside.json"
        outside.write_text(
            json.dumps({"permissions": {"allow": ["Read(outside-value)"]}})
        )
        link = self.project / ".claude" / "settings.local.json"
        link.symlink_to(outside)
        self.assertEqual(self.invoke(link), "")
        self.assertFalse(self.log.exists())
        self.assertFalse(self.state.exists())

    def test_invalid_private_settings_report_without_content(self):
        file = self.project / ".claude" / "settings.local.json"
        file.write_text("invalid private fixture payload")
        output = self.invoke(file)
        self.assertIn("could not verify", output)
        self.assertNotIn("invalid private fixture payload", output)
        self.assertNotIn(str(file), output)


if __name__ == "__main__":
    unittest.main()

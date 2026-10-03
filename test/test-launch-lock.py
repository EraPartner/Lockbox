#!/usr/bin/env python3
"""Prove startup serialization, early release, and kernel crash recovery."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".devcontainer/launch-lock.py"


class StartupLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name).resolve()
        self.lock = self.directory / ".startup-lock"
        self.children = []
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=3)

    def launch(self, name, body, lock=None):
        script = self.directory / (name + ".sh")
        script.write_text("#!/bin/bash\nset -eu\n" + body)
        child = subprocess.Popen(
            [sys.executable, str(HELPER), str(lock or self.lock), str(script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={
                key: value
                for key, value in os.environ.items()
                if key not in ("BASH_ENV", "ENV")
            },
        )
        self.children.append(child)
        return child

    def wait_file(self, path):
        deadline = time.monotonic() + 3
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(path.exists(), "launcher did not reach the expected phase")

    def test_startups_serialize_and_release_without_waiting_for_session(self):
        first_ready = self.directory / "first-ready"
        first_session = self.directory / "first-session"
        second_ready = self.directory / "second-ready"
        first = self.launch(
            "first",
            f"touch '{first_ready}'\nread -r proceed\nexec 9>&-\ntouch '{first_session}'\nread -r finish\n",
        )
        self.wait_file(first_ready)
        second = self.launch("second", f"touch '{second_ready}'\n")
        time.sleep(0.2)
        self.assertFalse(second_ready.exists())
        first.stdin.write("proceed\n")
        first.stdin.flush()
        self.wait_file(first_session)
        self.wait_file(second_ready)
        self.assertIsNone(first.poll(), "first session should still be active")
        self.assertEqual(second.wait(timeout=3), 0)
        first.stdin.write("finish\n")
        first.stdin.flush()
        self.assertEqual(first.wait(timeout=3), 0)

    def test_process_death_releases_lock(self):
        first_ready = self.directory / "first-ready"
        second_ready = self.directory / "second-ready"
        first = self.launch("first", f"touch '{first_ready}'\nread -r finish\n")
        self.wait_file(first_ready)
        first.kill()
        first.wait(timeout=3)
        second = self.launch("second", f"touch '{second_ready}'\n")
        self.wait_file(second_ready)
        self.assertEqual(second.wait(timeout=3), 0)

    def test_lock_aliases_are_refused_without_modifying_target(self):
        target = self.directory / "private-state"
        target.write_text("private unchanged bytes")
        for alias in ("symlink", "hardlink"):
            with self.subTest(alias=alias):
                lock = self.directory / alias
                if alias == "symlink":
                    lock.symlink_to(target)
                else:
                    os.link(target, lock)
                child = self.launch(alias, "exit 0\n", lock=lock)
                self.assertNotEqual(child.wait(timeout=3), 0)
                self.assertEqual(target.read_text(), "private unchanged bytes")

    def test_symlinked_parent_is_refused(self):
        parent = self.directory / "alias"
        parent.symlink_to(self.directory, target_is_directory=True)
        child = self.launch("aliased-parent", "exit 0\n", lock=parent / "lock")
        self.assertNotEqual(child.wait(timeout=3), 0)
        self.assertFalse((self.directory / "lock").exists())


if __name__ == "__main__":
    unittest.main()

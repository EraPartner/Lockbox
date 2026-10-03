#!/usr/bin/env python3
"""Exercise the actual full bundle verifier against isolated policy fixtures."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "usr/local/share/claude-guard/guard.mjs",
    "usr/local/share/claude-guard/hooks/post-edit.mjs",
    "usr/local/share/claude-guard/managed/explicit-format",
    "usr/local/share/claude-guard/hooks/watched-projects.json",
    "usr/local/share/claude-guard/verify-policy.sh",
    "usr/local/share/claude-guard/install-policy.sh",
]
SETTINGS = "etc/claude-code/managed-settings.json"
CODEX = "etc/codex/requirements.toml"
MANIFEST = "usr/local/share/claude-guard/policy.sha256"


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for src, dst in zip(
            ("claude-guard.mjs", "claude-post-edit.mjs", "claude-explicit-format.sh", None, "claude-policy-verify.sh", "claude-policy-install.sh"), FILES
        ):
            path = self.root / dst
            path.parent.mkdir(parents=True, exist_ok=True)
            if src:
                shutil.copy2(ROOT / src, path)
            else:
                path.write_bytes(self.policy("Vision", "/workspaces/Vision"))
        settings = self.root / SETTINGS
        settings.parent.mkdir(parents=True)
        settings.write_bytes(subprocess.check_output(["node", str(ROOT / "claude-guard.mjs"), "--managed-settings"]))
        codex = self.root / CODEX
        codex.parent.mkdir(parents=True)
        codex.write_bytes(subprocess.check_output(["node", str(ROOT / "claude-guard.mjs"), "--codex-requirements"]))
        self.identity = hashlib.sha256("".join(self.digest(p) + "\n" for p in FILES).encode()).hexdigest()
        self.manifest()

    def digest(self, name):
        return hashlib.sha256((self.root / name).read_bytes()).hexdigest()

    def manifest(self):
        (self.root / MANIFEST).write_text("".join(self.digest(p) + "  " + p + "\n" for p in [*FILES, SETTINGS, CODEX]))

    def verify(self):
        return subprocess.run(["bash", str(ROOT / "claude-policy-verify.sh"), "--root", str(self.root), self.identity], capture_output=True, text=True)

    def policy(self, profile, root):
        return subprocess.check_output(["node", str(ROOT / "claude-guard.mjs"), "--watcher-policy", profile, root])

    def test_complete_bundle_passes(self):
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_dependency_drift_is_rejected_even_with_forged_manifest(self):
        for name in FILES:
            with self.subTest(file=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b"\n// drift\n")
                self.assertNotEqual(self.verify().returncode, 0)
                self.manifest()
                self.assertNotEqual(self.verify().returncode, 0)
                path.write_bytes(original)
                self.manifest()

    def test_missing_or_symlinked_dependency_is_rejected(self):
        for name in FILES:
            with self.subTest(file=name):
                path = self.root / name
                saved = path.with_name(path.name + ".saved")
                path.rename(saved)
                self.assertNotEqual(self.verify().returncode, 0)
                path.symlink_to(saved)
                self.assertNotEqual(self.verify().returncode, 0)
                path.unlink()
                saved.rename(path)

    def test_forged_settings_manifest_does_not_bypass_regeneration(self):
        for name in (SETTINGS, CODEX):
            with self.subTest(file=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_text('{}\n')
                self.manifest()
                self.assertNotEqual(self.verify().returncode, 0)
                path.write_bytes(original)
                self.manifest()

    def test_project_opt_ins_and_generic_scope(self):
        for profile, root, auto, monitor in (
            ("Vision", "/workspaces/Vision", True, True),
            ("Watchman", "/workspaces/Watchman", True, True),
            ("LockBox", "/workspaces/LockBox", True, False),
            ("VaultLens", "/workspaces/VaultLens", True, False),
            ("Brain", "/workspaces/VaultLens", False, True),
            ("Brain", "/workspaces/project", False, True),
            ("generic", "/workspaces/project", False, False),
            ("Napoleon-relay", "/workspaces/Napoleon-relay", False, False),
        ):
            with self.subTest(profile=profile):
                project = json.loads(self.policy(profile, root))["projects"][0]
                self.assertEqual((project["root"], project["autoFormat"], project["monitorPermissions"]), (root, auto, monitor))
        for candidate, expected in (("/Users/computer/Code/Vision", "Vision"), ("/Users/computer/Code/Vision/subdir", "generic"), ("/unreviewed/project", "generic")):
            selected = subprocess.check_output(["node", str(ROOT / "claude-guard.mjs"), "--profile-for-root", candidate], text=True).strip()
            self.assertEqual(selected, expected)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Creation metadata is checked on the host before a container can execute."""
import copy
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
DIGEST = "sha256:" + "1" * 64
IMAGE = [{"configuration": {"descriptor": {"digest": DIGEST}}}]
CONTAINER = [{"configuration": {"id": "synthetic", "image": {"descriptor": {"digest": DIGEST}}, "initProcess": {"environment": ["SANDBOX_AGENT=claude", "SANDBOX_LAUNCH_POLICY=policy-v1"]}}}]

class ImagePolicyTests(unittest.TestCase):
    def inspect(self, descriptor, status=0, mode="image"):
        program = '''source "$1"
container() { printf '%s' "$TEST_DESCRIPTOR"; return "$TEST_STATUS"; }
if [[ "$TEST_MODE" == image ]]; then
  sandbox_image_policy_id synthetic:latest || exit 1
  printf '%s\\n%s\\n' "$IMAGE_POLICY_ID" "$IMAGE_DIGEST"
else
  IMAGE_DIGEST="$TEST_DIGEST"
  sandbox_require_container_policy synthetic claude policy-v1 || exit 1
fi
'''
        return subprocess.run(
            ["bash", "-c", program, "image-policy", str(ROOT / "launcher-common.sh")],
            env={**os.environ, "TEST_DESCRIPTOR": descriptor, "TEST_STATUS": str(status), "TEST_MODE": mode, "TEST_DIGEST": DIGEST},
            capture_output=True, text=True,
        )

    def test_unchanged_inspect_content_has_stable_identity(self):
        first = self.inspect(json.dumps(IMAGE))
        reordered = self.inspect(json.dumps(IMAGE, sort_keys=True, indent=2))
        other = copy.deepcopy(IMAGE)
        other[0]["configuration"]["descriptor"]["digest"] = "sha256:" + "2" * 64
        changed = self.inspect(json.dumps(other))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, reordered.stdout)
        self.assertNotEqual(first.stdout, changed.stdout)
        self.assertRegex(first.stdout.splitlines()[0], r"^[0-9a-f]{64}$")
        self.assertEqual(first.stdout.splitlines()[1], DIGEST)

    def test_failed_or_invalid_inspection_has_no_identity(self):
        for descriptor, status in ((json.dumps(IMAGE), 1), ("broken", 0), ("null", 0), ("[]", 0), ("{}", 0), (json.dumps({"digest": DIGEST}), 0), (json.dumps(IMAGE * 2), 0), (json.dumps(IMAGE) + json.dumps(IMAGE), 0), ('"tag"', 0)):
            with self.subTest(descriptor=descriptor, status=status):
                result = self.inspect(descriptor, status)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")

    def test_container_metadata_requires_exact_unique_creation_identity(self):
        valid = self.inspect(json.dumps(CONTAINER), mode="container")
        self.assertEqual(valid.returncode, 0, valid.stderr)
        invalid = [None, {}, [], CONTAINER * 2]
        for field in ("id", "image", "initProcess"):
            obj = copy.deepcopy(CONTAINER)
            obj[0]["configuration"].pop(field)
            invalid.append(obj)
        for key, value in (("id", "other"), ("image", {"descriptor": {"digest": "sha256:" + "2" * 64}})):
            obj = copy.deepcopy(CONTAINER)
            obj[0]["configuration"][key] = value
            invalid.append(obj)
        for environment in (None, "SANDBOX_AGENT=claude", [], [1], ["SANDBOX_AGENT=codex", "SANDBOX_LAUNCH_POLICY=policy-v1"], ["SANDBOX_AGENT=claude", "SANDBOX_LAUNCH_POLICY=stale"], CONTAINER[0]["configuration"]["initProcess"]["environment"] + ["SANDBOX_AGENT=claude"], CONTAINER[0]["configuration"]["initProcess"]["environment"] + ["SANDBOX_LAUNCH_POLICY=policy-v1"]):
            obj = copy.deepcopy(CONTAINER)
            obj[0]["configuration"]["initProcess"]["environment"] = environment
            invalid.append(obj)
        for descriptor in invalid:
            with self.subTest(descriptor=descriptor):
                self.assertNotEqual(self.inspect(json.dumps(descriptor), mode="container").returncode, 0)
        self.assertNotEqual(self.inspect(json.dumps(CONTAINER), status=1, mode="container").returncode, 0)
        self.assertNotEqual(self.inspect(json.dumps(CONTAINER) + json.dumps(CONTAINER), mode="container").returncode, 0)

if __name__ == "__main__":
    unittest.main()

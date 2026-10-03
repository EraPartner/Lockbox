#!/usr/bin/env python3
"""Exercise doctor network and credential decisions without network or auth."""

import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DoctorChecks(unittest.TestCase):
    def run_check(self, program, *args):
        return subprocess.run(
            [
                "bash",
                "-c",
                "failed=0\npass() { :; }\nok() { :; }\nfail() { failed=1; }\nbad() { failed=1; }\n"
                + program
                + '\nexit "$failed"\n',
                "fixture",
                *args,
            ],
            env={"PATH": os.environ["PATH"], "BASH_ENV": ""},
            capture_output=True,
            text=True,
        ).returncode

    def test_both_doctors_reject_proxy_transport_failure(self):
        scoped = (
            (ROOT / ".devcontainer/bin/doctor")
            .read_text()
            .split("# Allowed provider egress through the proxy?\n")[1]
            .split("# Direct egress should be blocked.")[0]
        )
        generic = (
            (ROOT / "sandbox/.devcontainer/bin/doctor")
            .read_text()
            .split(
                'if [[ "$AGENT" == codex ]]; then provider_host=api.openai.com; else provider_host=api.anthropic.com; fi'
            )[1]
            .split("# Direct egress should be blocked")[0]
        )
        for program in (scoped, generic):
            for provider in ("claude", "codex"):
                for code, status, expected in (
                    ("000", "7", 1),
                    ("", "7", 1),
                    ("401", "0", 0),
                    ("200", "0", 0),
                ):
                    with self.subTest(provider=provider, code=code):
                        self.assertEqual(
                            self.run_check(
                                'AGENT=$1\nprovider_host=fixture.invalid\nTEST_CODE=$2\nTEST_STATUS=$3\ncurl() { printf "%s" "$TEST_CODE"; return "$TEST_STATUS"; }\n'
                                + program,
                                provider,
                                code,
                                status,
                            ),
                            expected,
                        )

    def test_generic_direct_egress_success_fails(self):
        program = (
            (ROOT / "sandbox/.devcontainer/bin/doctor")
            .read_text()
            .split(
                "# Direct egress should be blocked independently of proxy availability.\n"
            )[1]
            .split("if curl -sS --max-time 8")[0]
        )
        for status, expected in (("0", 1), ("7", 0)):
            self.assertEqual(
                self.run_check(
                    'TEST_STATUS=$1\ncurl() { return "$TEST_STATUS"; }\n' + program,
                    status,
                ),
                expected,
            )

    def test_generic_push_token_presence_fails(self):
        program = (
            (ROOT / "sandbox/.devcontainer/bin/doctor")
            .read_text()
            .split("# Editing sandboxes must not receive Git push credentials.\n")[1]
            .split('echo "== Trust model =="')[0]
        )
        for name in ("GH_TOKEN", "GITHUB_TOKEN"):
            for present in (False, True):
                with self.subTest(name=name, present=present):
                    value = "synthetic" if present else ""
                    self.assertEqual(
                        self.run_check(name + '="$1"\n' + program, value), int(present)
                    )


if __name__ == "__main__":
    unittest.main()

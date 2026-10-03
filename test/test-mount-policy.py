#!/usr/bin/env python3
"""Exercise the actual mount planner without containers or Git mutations."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BASH = "/bin/bash"
DRIVER = r"""
set -euo pipefail
source "$POLICY_TEST_LIBRARY"
git() {
  if [[ "${3:-}" == config ]]; then
    [[ "${POLICY_TEST_INCLUDE_FAILURE:-0}" == 0 ]] || return 3
    if [[ -n "${POLICY_TEST_INCLUDE_MALFORMED:-}" ]]; then
      printf '%s' "$POLICY_TEST_INCLUDE_MALFORMED"; return 0
    fi
    [[ -n "${POLICY_TEST_INCLUDE_ORIGIN:-}" ]] || return 1
    printf '%s\0%s\n%s\0' "$POLICY_TEST_INCLUDE_ORIGIN" include.path "$POLICY_TEST_INCLUDE_PATH"
    if [[ -n "${POLICY_TEST_SECOND_ORIGIN:-}" ]]; then
      printf '%s\0%s\n%s\0' "$POLICY_TEST_SECOND_ORIGIN" 'includeif.gitdir:repo.path' "$POLICY_TEST_SECOND_PATH"
    fi
    return 0
  fi
  [[ "$*" == "-C $POLICY_TEST_REPO rev-parse --git-path hooks" ]] || return 99
  if [[ "${POLICY_TEST_REAL_GIT:-0}" == 1 ]]; then
    command git -C "$POLICY_TEST_ROOT" -c "core.hooksPath=$POLICY_TEST_HOOKS" rev-parse --git-path hooks
  else
    [[ "${POLICY_TEST_GIT_FAILURE:-0}" == 0 ]] || return 3
    printf '%s\n' "$POLICY_TEST_HOOKS"
  fi
}
if [[ "${POLICY_TEST_MKDIR_FAILURE:-0}" == 1 ]]; then mkdir() { return 1; }; fi
if [[ "${POLICY_TEST_FIND_FAILURE:-0}" == 1 ]]; then find() { return 1; }; fi
if [[ "${POLICY_TEST_DISCOVERY_FAILURE_AFTER_GIT:-0}" == 1 ]]; then
  discovery_calls=0
  find() {
    if [[ "${2:-}" == -name ]]; then
      discovery_calls=$((discovery_calls + 1))
      (( discovery_calls < 2 )) || return 1
    fi
    command find "$@"
  }
fi
sandbox_git_ro_mounts "$POLICY_TEST_REPO" /workspaces/project
sandbox_devcontainer_ro_mounts "$POLICY_TEST_REPO" /workspaces/project
sandbox_ro_mount_policy_id
printf '%s\0' "$RO_MOUNT_POLICY_ID"
mounts=(${GIT_RO_MOUNTS[@]+"${GIT_RO_MOUNTS[@]}"} ${DEVCONTAINER_RO_MOUNTS[@]+"${DEVCONTAINER_RO_MOUNTS[@]}"})
if (( ${#mounts[@]} > 0 )); then printf '%s\0' "${mounts[@]}"; fi
"""


class MountPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lockbox-mount-policy-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "repo with spaces"
        self.repo.mkdir()
        (self.repo / ".git/hooks").mkdir(parents=True)
        self.home = self.base / "home"
        self.home.mkdir()

    def run_policy(self, hooks=".githooks", *, success=True, **overrides):
        env = os.environ.copy()
        env.update(
            HOME=str(self.home),
            POLICY_TEST_LIBRARY=str(ROOT / "launcher-common.sh"),
            POLICY_TEST_ROOT=str(ROOT),
            POLICY_TEST_REPO=str(self.repo),
            POLICY_TEST_HOOKS=hooks,
        )
        env.update(
            {f"POLICY_TEST_{key}": str(value) for key, value in overrides.items()}
        )
        result = subprocess.run([BASH, "-c", DRIVER], env=env, capture_output=True)
        if not success:
            self.assertNotEqual(result.returncode, 0, result.stdout.decode())
            self.assertIn(b"refusing unsafe read-only mount policy", result.stderr)
            return result
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        parts = result.stdout.decode().split("\0")[:-1]
        self.assertRegex(parts[0], r"^[0-9a-f]{64}$")
        flags = parts[1:]
        self.assertEqual(len(flags) % 2, 0)
        self.assertTrue(all(flag == "-v" for flag in flags[::2]))
        return parts[0], flags[1::2]

    def test_absolute_and_traversal_aliases_protect_same_directory(self):
        hooks = self.repo / ".githooks"
        hooks.mkdir()
        values = [
            ".githooks",
            str(hooks),
            ".git/../.githooks",
            f"../{self.repo.name}/.githooks",
            "././.githooks",
            ".githooks/",
        ]
        plans = [self.run_policy(value) for value in values]
        self.assertTrue(all(plan == plans[0] for plan in plans))
        self.assertIn(f"{hooks}:/workspaces/project/.githooks:ro", plans[0][1])

    def test_ordinary_name_containing_dots(self):
        hooks = self.repo / "hooks..dir"
        hooks.mkdir()
        _, mounts = self.run_policy("hooks..dir")
        self.assertIn(f"{hooks}:/workspaces/project/hooks..dir:ro", mounts)

    def test_default_hooks_already_covered(self):
        _, mounts = self.run_policy(".git/hooks")
        self.assertEqual(mounts, [f"{self.repo}/.git:/workspaces/project/.git:ro"])

    def test_external_hooks_not_mounted(self):
        for value in [
            "/dev/null",
            str(self.base / "external-hooks"),
            "../external-hooks",
        ]:
            with self.subTest(value=value):
                _, mounts = self.run_policy(value)
                self.assertEqual(len(mounts), 1)

    def test_git_effective_empty_and_tilde_paths(self):
        self.run_policy("", success=False, REAL_GIT=1)
        _, mounts = self.run_policy("~/.githooks", REAL_GIT=1)
        self.assertEqual(len(mounts), 1)

    def test_root_hooks_fail_closed(self):
        for value in [".", "./", str(self.repo), ".git/..", ".githooks/.."]:
            with self.subTest(value=value):
                self.run_policy(value, success=False)

    def test_missing_nested_hooks_use_empty_readonly_source(self):
        _, mounts = self.run_policy("missing/nested/hooks")
        empty = self.home / ".claude-sandbox/empty-hooks"
        self.assertEqual(list(empty.iterdir()), [])
        self.assertIn(f"{empty}:/workspaces/project/missing/nested/hooks:ro", mounts)
        self.assertFalse((self.repo / "missing").exists())

    def test_missing_hook_placeholder_failure_aborts(self):
        self.run_policy(success=False, MKDIR_FAILURE=1)

    def test_existing_placeholder_must_be_empty(self):
        empty = self.home / ".claude-sandbox/empty-hooks"
        empty.mkdir(parents=True)
        (empty / "pre-commit").write_text("untrusted")
        self.run_policy(success=False)

    def test_git_query_and_tree_scan_failure_abort(self):
        self.run_policy(success=False, GIT_FAILURE=1)
        self.run_policy(success=False, FIND_FAILURE=1)

    def test_symlink_hooks_and_dotdot_alias_refused(self):
        (self.repo / "real-hooks").mkdir()
        (self.repo / "alias").symlink_to("real-hooks", target_is_directory=True)
        self.run_policy("alias", success=False)
        self.run_policy("alias/../real-hooks", success=False)

    def test_dangling_symlink_refused(self):
        (self.repo / ".githooks").symlink_to("absent", target_is_directory=True)
        self.run_policy(success=False)

    def test_external_symlink_back_into_workspace_refused(self):
        (self.repo / ".githooks").mkdir()
        alias = self.base / "outside-alias"
        alias.symlink_to(self.repo / ".githooks", target_is_directory=True)
        self.run_policy(str(alias), success=False)

    def test_hook_symlink_and_hardlink_files_refused(self):
        hooks = self.repo / ".githooks"
        hooks.mkdir()
        writable = self.repo / "writable-script"
        writable.write_text("untrusted")
        protected = hooks / "pre-commit"
        protected.symlink_to(writable)
        self.run_policy(success=False)
        protected.unlink()
        os.link(writable, protected)
        self.run_policy(success=False)

    def test_metadata_file_symlink_and_commondir_refused(self):
        metadata = self.repo / ".git"
        original = self.repo / "actual-metadata"
        metadata.rename(original)
        metadata.write_text("gitdir: actual-metadata\n")
        self.run_policy(success=False)
        metadata.unlink()
        metadata.symlink_to(original, target_is_directory=True)
        self.run_policy(success=False)
        metadata.unlink()
        original.rename(metadata)
        (metadata / "commondir").write_text("../another-git-dir\n")
        self.run_policy(success=False)

    def test_non_directory_hooks_and_bad_mount_delimiters_refused(self):
        (self.repo / "hook-file").write_text("untrusted")
        for value in [
            "hook-file",
            "hook-file/hooks",
            "bad:path",
            "bad\npath",
            "bad\n",
            "bad\tpath",
            "",
        ]:
            with self.subTest(value=value):
                self.run_policy(value, success=False)

    def test_nongit_workspace_and_absent_devcontainers(self):
        (self.repo / ".git/hooks").rmdir()
        (self.repo / ".git").rmdir()
        _, mounts = self.run_policy()
        self.assertEqual(mounts, [])
        self.assertFalse((self.repo / ".devcontainer").exists())
        self.assertFalse((self.repo / "sandbox").exists())

    def test_both_launcher_trees_and_policy_identity(self):
        (self.repo / ".githooks").mkdir()
        initial, _ = self.run_policy()
        (self.repo / ".devcontainer").mkdir()
        top, mounts = self.run_policy()
        self.assertNotEqual(initial, top)
        self.assertIn(
            f"{self.repo}/.devcontainer:/workspaces/project/.devcontainer:ro", mounts
        )
        (self.repo / "sandbox/.devcontainer").mkdir(parents=True)
        nested, mounts = self.run_policy()
        self.assertNotEqual(top, nested)
        self.assertIn(
            f"{self.repo}/sandbox/.devcontainer:/workspaces/project/sandbox/.devcontainer:ro",
            mounts,
        )
        self.assertEqual(nested, self.run_policy()[0])
        (self.repo / "other-hooks").mkdir()
        self.assertNotEqual(nested, self.run_policy("other-hooks")[0])

    def test_launcher_symlink_and_hardlink_refused(self):
        launcher = self.repo / ".devcontainer"
        launcher.mkdir()
        writable = self.repo / "host-launcher"
        writable.write_text("untrusted")
        os.link(writable, launcher / "agent")
        self.run_policy(success=False)

    def test_workspace_config_include_refused_even_when_missing(self):
        for value in ["../settings.config", str(self.repo / "settings.config")]:
            with self.subTest(value=value):
                self.run_policy(
                    success=False, INCLUDE_ORIGIN="file:.git/config", INCLUDE_PATH=value
                )

    def test_protected_and_external_config_includes_supported(self):
        for value in ["included.config", str(self.base / "external.config")]:
            with self.subTest(value=value):
                self.run_policy(INCLUDE_ORIGIN="file:.git/config", INCLUDE_PATH=value)

    def test_nested_includes_resolve_against_each_origin(self):
        self.run_policy(
            success=False,
            INCLUDE_ORIGIN="file:.git/config",
            INCLUDE_PATH="included.config",
            SECOND_ORIGIN="file:.git/included.config",
            SECOND_PATH="../writable.config",
        )
        self.run_policy(
            INCLUDE_ORIGIN="file:.git/config",
            INCLUDE_PATH="included.config",
            SECOND_ORIGIN=f"file:{self.base}/external.config",
            SECOND_PATH="external-second.config",
        )

    def test_include_errors_and_unexpected_origins_refused(self):
        self.run_policy(success=False, INCLUDE_FAILURE=1)
        self.run_policy(success=False, INCLUDE_MALFORMED="truncated")
        self.run_policy(
            success=False, INCLUDE_ORIGIN="command line:", INCLUDE_PATH="/dev/null"
        )

    def test_nested_git_directories_refused_in_clone_and_nongit_target(self):
        (self.repo / "projects/other/.git").mkdir(parents=True)
        self.run_policy(success=False)
        (self.repo / ".git/hooks").rmdir()
        (self.repo / ".git").rmdir()
        self.run_policy(success=False)

    def test_nested_git_files_and_symlinks_refused(self):
        nested = self.repo / "submodule/.git"
        nested.parent.mkdir()
        nested.write_text("gitdir: ../.git/modules/submodule\n")
        self.run_policy(success=False)
        nested.unlink()
        nested.symlink_to("../../outside-metadata", target_is_directory=True)
        self.run_policy(success=False)

    def test_root_metadata_pruned_from_boundary_discovery(self):
        (self.repo / ".git/administration/.git").mkdir(parents=True)
        (self.repo / ".git/administration/.devcontainer").mkdir()
        _, mounts = self.run_policy(".git/hooks")
        self.assertEqual(mounts, [f"{self.repo}/.git:/workspaces/project/.git:ro"])

    def test_arbitrary_nested_launcher_trees_sorted_and_hashed(self):
        z = self.repo / "z project/deep/.devcontainer"
        a = self.repo / "a project/.devcontainer"
        z.mkdir(parents=True)
        a.mkdir(parents=True)
        first, mounts = self.run_policy(".git/hooks")
        self.assertEqual(
            mounts[1:],
            [
                f"{a}:/workspaces/project/a project/.devcontainer:ro",
                f"{z}:/workspaces/project/z project/deep/.devcontainer:ro",
            ],
        )
        # Changing directory enumeration order must not force a stale-plan hit.
        a.rmdir()
        z.rmdir()
        a.mkdir()
        z.mkdir()
        self.assertEqual(first, self.run_policy(".git/hooks")[0])
        (self.repo / "another/deep/.devcontainer").mkdir(parents=True)
        self.assertNotEqual(first, self.run_policy(".git/hooks")[0])

    def test_symlinked_and_nondirectory_launcher_paths_refused(self):
        parent = self.repo / "arbitrary/nested"
        parent.mkdir(parents=True)
        launcher = parent / ".devcontainer"
        launcher.symlink_to(self.base / "outside-launcher", target_is_directory=True)
        self.run_policy(success=False)
        launcher.unlink()
        launcher.write_text("not a directory")
        self.run_policy(success=False)

    def test_discovered_launcher_path_controls_refused_without_trimming(self):
        for name in ["trailing\n", "colon:path"]:
            with self.subTest(name=name):
                launcher = self.repo / name / ".devcontainer"
                launcher.mkdir(parents=True)
                self.run_policy(success=False)
                launcher.rmdir()
                launcher.parent.rmdir()

    def test_launcher_discovery_failure_after_git_plan_aborts(self):
        self.run_policy(".git/hooks", success=False, DISCOVERY_FAILURE_AFTER_GIT=1)

    def test_unrelated_directory_symlinks_are_not_followed(self):
        outside = self.base / "outside"
        (outside / ".git").mkdir(parents=True)
        (outside / ".devcontainer").mkdir()
        (self.repo / "external-link").symlink_to(outside, target_is_directory=True)
        _, mounts = self.run_policy(".git/hooks")
        self.assertEqual(len(mounts), 1)


if __name__ == "__main__":
    unittest.main()

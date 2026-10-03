#!/usr/bin/env python3
"""Exercise both providers with synthetic authored and private configuration.

Requires Python 3.11+ for the production importer's tomllib dependency. No real
host home, authentication file, container, or network is used.
"""

import importlib.util
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / ".devcontainer/agent-config.py"
spec = importlib.util.spec_from_file_location("agent_config", TOOL)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class ConfigTests(unittest.TestCase):
    def test_missing_authored_source_warns_without_importing_native_by_default(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                output = io.StringIO()
                with redirect_stderr(output):
                    tool.stage(
                        provider,
                        self.staged(provider),
                        self.root / "missing",
                        self.home,
                    )
                self.assertIn(
                    "authored configuration source is missing", output.getvalue()
                )
                self.assertNotIn(str(self.home), output.getvalue())
                instruction = "CLAUDE.md" if provider == "claude" else "AGENTS.md"
                self.assertFalse(
                    (self.staged(provider) / "files" / instruction).exists()
                )
                self.assertFalse((self.staged(provider) / "files/agents").exists())

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "synthetic-home"
        self.source = self.root / "authored"
        self.source.mkdir()
        self.home.mkdir()
        self.write(self.source / "AGENTS.md", "Shared instructions\n")
        self.write(self.home / ".agents/skills/shared/SKILL.md", "Shared skill v1\n")
        self.write(self.source / "skills/authored/SKILL.md", "Authored skill\n")
        self.write_json(
            self.source / "mcp.json",
            {
                "worker": {
                    "command": "python3",
                    "args": ["-m", "synthetic"],
                    "env_vars": ["WORKER_TOKEN"],
                },
                "remote": {
                    "type": "http",
                    "url": "https://synthetic.example/mcp",
                    "bearer_token_env_var": "REMOTE_TOKEN",
                    "env_http_headers": {"X-Tenant": "TENANT_ID"},
                },
            },
        )
        for provider in ("claude", "codex"):
            native = self.home / ("." + provider)
            native.mkdir()
            self.write(native / "agents/reviewer.md", "Synthetic reviewer\n")
            self.native_value(
                provider,
                {
                    "model": "host-model",
                    "projects": {"host-only": {}},
                    "hooks": {"blocked": []},
                },
            )
            (self.root / (provider + "-private")).mkdir()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def write_json(self, path, value):
        self.write(path, json.dumps(value))

    def native_value(self, provider, value):
        native = self.home / ("." + provider)
        if provider == "claude":
            self.write_json(native / "settings.json", value)
        else:
            self.write(native / "config.toml", tool.toml(value))

    def staged(self, provider):
        return self.root / (provider + "-stage")

    def private(self, provider):
        return self.root / (provider + "-private")

    def stage(self, provider):
        tool.stage(
            provider, self.staged(provider), self.source, self.home, import_native=True
        )

    def prepare(self, provider, refresh=False):
        with redirect_stdout(io.StringIO()):
            tool.prepare(
                provider, self.private(provider), self.staged(provider), refresh
            )

    def values(self, provider, config=None):
        config = config or self.private(provider)
        if provider == "claude":
            return tool.read_json(config / "settings.json"), tool.read_json(
                config / ".claude.json"
            )["mcpServers"]
        value = tomllib.loads((config / "config.toml").read_text())
        return value, value["mcp_servers"]

    def contents(self, tree):
        return {
            str(p.relative_to(tree)): p.read_bytes()
            for p in tree.rglob("*")
            if p.is_file()
            and not any(
                part.startswith(".lockbox-config-") and part != ".lockbox-config-ready"
                for part in p.relative_to(tree).parts
            )
        }

    def test_nested_plugin_configs_keep_their_native_content(self):
        files = {
            "plugins/example/settings.json": '{"pluginOption": true}\n',
            "plugins/example/config.toml": "plugin_option = true\n",
        }
        for rel, content in files.items():
            self.write(self.home / ".claude" / rel, content)
        self.stage("claude")
        self.prepare("claude")
        for rel, content in files.items():
            self.assertEqual((self.private("claude") / rel).read_text(), content)
            self.write(self.private("claude") / rel, "private plugin edits\n")
        (self.private("claude") / ".lockbox-config-ready").unlink()
        self.prepare("claude")
        for rel in files:
            self.assertEqual(
                (self.private("claude") / rel).read_text(), "private plugin edits\n"
            )
        self.prepare("claude", refresh=True)
        for rel, content in files.items():
            self.assertEqual((self.private("claude") / rel).read_text(), content)

    def test_explicit_source_is_default_and_native_import_is_opt_in(self):
        self.native_value("claude", {"api_key": "synthetic-private-value"})
        for provider in ("claude", "codex"):
            tool.stage(provider, self.staged(provider), self.source, self.home)
            tree = self.staged(provider) / "files"
            self.assertFalse((tree / "agents/reviewer.md").exists())
            self.assertFalse((tree / "skills/shared/SKILL.md").exists())
            self.assertTrue((tree / "skills/authored/SKILL.md").exists())
        with self.assertRaises(ValueError):
            self.stage("claude")

    def test_refresh_replaces_mcp_transport_and_removes_retired_definition(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                self.write_json(
                    self.source / "mcp.json",
                    {
                        "worker": {
                            "command": "python3",
                            "args": ["old"],
                            "env_vars": ["WORKER_TOKEN"],
                        }
                    },
                )
                self.stage(provider)
                self.prepare(provider)
                self.write_json(
                    self.source / "mcp.json",
                    {
                        "worker": {
                            "type": "http",
                            "url": "https://synthetic.example/mcp",
                        }
                    },
                )
                self.stage(provider)
                self.prepare(provider, refresh=True)
                _, servers = self.values(provider)
                self.assertEqual(
                    servers["worker"]["url"], "https://synthetic.example/mcp"
                )
                self.assertNotIn("command", servers["worker"])
                self.assertNotIn("args", servers["worker"])
                self.assertNotIn("env", servers["worker"])
                self.assertNotIn("env_vars", servers["worker"])
                self.write_json(self.source / "mcp.json", {})
                self.stage(provider)
                self.prepare(provider, refresh=True)
                _, servers = self.values(provider)
                self.assertNotIn("worker", servers)

    def test_plugin_environment_references_use_authored_snapshot(self):
        path = self.source / "claude/plugins/example/.mcp.json"
        self.write_json(
            path,
            {
                "mcpServers": {
                    "fixture": {
                        "command": "python3",
                        "env": {"WORKER_TOKEN": "${WORKER_TOKEN}"},
                    }
                }
            },
        )
        tool.stage("claude", self.staged("claude"), self.source, self.home)
        self.assertIn(
            "WORKER_TOKEN",
            tool.read_json(self.staged("claude") / "environment.json")["names"],
        )
        self.write_json(
            path,
            {"fixture": {"command": "python3", "env": {"GH_TOKEN": "${GH_TOKEN}"}}},
        )
        with self.assertRaises(ValueError):
            tool.stage("claude", self.staged("claude"), self.source, self.home)

    def test_refresh_removes_owned_entries_and_preserves_private_changes(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                self.stage(provider)
                self.prepare(provider)
                config = self.private(provider)
                self.write(config / "auth.json", "private auth\n")
                self.write(config / "skills/private/SKILL.md", "private skill\n")
                self.write(config / "skills/shared/SKILL.md", "changed locally\n")
                shutil.rmtree(self.source / "skills/authored")
                declarations = tool.read_json(self.source / "mcp.json")
                declarations.pop("worker")
                self.write_json(self.source / "mcp.json", declarations)
                self.native_value(provider, {"private_new_source_option": True})
                shutil.rmtree(self.home / ".agents/skills/shared", ignore_errors=True)
                self.stage(provider)
                self.prepare(provider, refresh=True)
                self.assertFalse((config / "skills/authored/SKILL.md").exists())
                self.assertEqual(
                    (config / "skills/shared/SKILL.md").read_text(), "changed locally\n"
                )
                self.assertEqual(
                    (config / "skills/private/SKILL.md").read_text(), "private skill\n"
                )
                self.assertEqual((config / "auth.json").read_text(), "private auth\n")
                value, servers = self.values(provider)
                self.assertNotIn("worker", servers)
                self.assertNotIn("model", value)
                self.assertTrue(value["private_new_source_option"])
                self.write(self.source / "skills/authored/SKILL.md", "Authored skill\n")
                self.write(
                    self.home / ".agents/skills/shared/SKILL.md", "Shared skill v1\n"
                )
                declarations["worker"] = {
                    "command": "python3",
                    "args": ["-m", "synthetic"],
                    "env_vars": ["WORKER_TOKEN"],
                }
                self.write_json(self.source / "mcp.json", declarations)

    def test_import_locks_refuse_concurrent_process_and_release_on_exit(self):
        lock = self.root / "profile/.import-lock"
        script = "import importlib.util,sys; s=importlib.util.spec_from_file_location('config',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); from pathlib import Path;\nwith m.profile_lock(Path(sys.argv[2])): print('acquired')"
        with tool.profile_lock(lock):
            result = subprocess.run(
                [sys.executable, "-c", script, str(TOOL), str(lock)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("acquired", result.stdout)
        result = subprocess.run(
            [sys.executable, "-c", script, str(TOOL), str(lock)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_removed_files_and_records_roll_back_if_publication_fails(self):
        self.stage("claude")
        self.prepare("claude")
        config = self.private("claude")
        before = self.contents(config)
        shutil.rmtree(self.source / "skills/authored")
        self.stage("claude")
        replace = os.replace

        def fail_record(source, target):
            if Path(target) == config / ".lockbox-config-managed.json":
                raise OSError("synthetic record failure")
            return replace(source, target)

        with patch.object(tool.os, "replace", side_effect=fail_record):
            with self.assertRaises(OSError):
                self.prepare("claude", refresh=True)
        self.assertEqual(self.contents(config), before)

    def test_read_only_destination_does_not_break_earlier_rollback(self):
        self.stage("claude")
        self.prepare("claude")
        config = self.private("claude")
        before = self.contents(config)
        self.write(self.source / "AGENTS.md", "Changed guidance\n")
        self.write(self.source / "skills/authored/SKILL.md", "Changed skill\n")
        self.stage("claude")
        destination = config / "skills/authored"
        destination.chmod(0o555)
        try:
            with self.assertRaises(PermissionError):
                self.prepare("claude", refresh=True)
            self.assertEqual(self.contents(config), before)
            self.assertFalse((config / ".lockbox-config-transaction.json").exists())
        finally:
            destination.chmod(0o755)

    def test_restore_failure_continues_and_blocks_readiness_and_retry(self):
        self.stage("claude")
        self.prepare("claude")
        config = self.private("claude")
        before = self.contents(config)
        self.write(self.source / "AGENTS.md", "Changed guidance\n")
        self.native_value("claude", {"model": "changed-model"})
        self.stage("claude")
        replace = os.replace

        def fail_publish_and_one_restore(source, target):
            source, target = Path(source), Path(target)
            if target == config / ".lockbox-config-managed.json" or (
                source.name.startswith(".agent-restore-")
                and target == config / "CLAUDE.md"
            ):
                raise PermissionError("synthetic private exception data")
            return replace(source, target)

        with patch.object(tool.os, "replace", side_effect=fail_publish_and_one_restore):
            with self.assertRaises(tool.ImportFailure) as raised:
                self.prepare("claude", refresh=True)
        self.assertEqual(raised.exception.category, "recovery-required")
        self.assertEqual(
            (config / "settings.json").read_bytes(), before["settings.json"]
        )
        self.assertFalse((config / ".lockbox-config-ready").exists())
        self.assertTrue((config / ".lockbox-config-transaction.json").exists())
        with self.assertRaises(tool.ImportFailure):
            self.prepare("claude")
        # Even a stale ready marker left by an interrupted publication is refused.
        self.write_json(
            config / ".lockbox-config-ready",
            {"version": tool.VERSION, "provider": "claude"},
        )
        result = subprocess.run(
            [sys.executable, str(TOOL), "inspect", "claude", "--config", str(config)],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[recovery-required]", result.stderr)
        self.assertNotIn("ready", result.stdout)
        tool.prune_backups(config, "claude")
        self.assertTrue(
            any(
                tool.read_json(p / ".lockbox-backup.json")["status"]
                == "rollback-incomplete"
                for p in config.glob(".lockbox-config-backup-*")
            )
        )

    def test_owned_plugin_directory_and_file_transitions_and_rollback(self):
        path = self.source / "claude/plugins/example"
        self.write(path / "nested/option.txt", "old plugin\n")
        self.stage("claude")
        self.prepare("claude")
        config = self.private("claude")
        for directory in (False, True, False):
            before = self.contents(config)
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            if directory:
                self.write(path / "nested/option.txt", "new plugin\n")
            else:
                self.write(path, "single plugin\n")
            self.stage("claude")
            replace = os.replace

            def fail_record_once(source, target):
                if Path(target) == config / ".lockbox-config-managed.json":
                    raise OSError("synthetic publication failure")
                return replace(source, target)

            with patch.object(tool.os, "replace", side_effect=fail_record_once):
                with self.assertRaises(OSError):
                    self.prepare("claude", refresh=True)
            self.assertEqual(self.contents(config), before)
            self.prepare("claude", refresh=True)
            self.assertEqual((config / "plugins/example").is_dir(), directory)

    def test_plugin_directory_transition_refuses_private_or_changed_descendants(self):
        for kind in ("private", "modified", "private-empty-directory"):
            with self.subTest(kind=kind):
                path = self.source / "claude/plugins/example"
                if path.exists():
                    path.unlink()
                self.write(path / "option.txt", "owned\n")
                self.stage("claude")
                self.prepare("claude", refresh=True)
                config = self.private("claude")
                destination = config / "plugins/example"
                if kind == "private-empty-directory":
                    (destination / "private-empty").mkdir()
                else:
                    self.write(
                        destination
                        / ("private.txt" if kind == "private" else "option.txt"),
                        "private\n",
                    )
                before = self.contents(config)
                shutil.rmtree(path)
                self.write(path, "single\n")
                self.stage("claude")
                with self.assertRaises(ValueError):
                    self.prepare("claude", refresh=True)
                self.assertEqual(self.contents(config), before)
                shutil.rmtree(destination)

    def test_path_rewriting_uses_complete_normalized_roots(self):
        for provider in ("claude", "codex"):
            content = "\n".join(
                [
                    str(self.source / provider / "tool.sh"),
                    str(self.source / "instructions.md"),
                    str(self.source) + "-other/tool.sh",
                    "prefix" + str(self.source / "tool.sh"),
                    str(self.source / provider) + "-other/tool.sh",
                ]
            )
            self.write(self.source / "AGENTS.md", content)
            tool.stage(
                provider,
                self.staged(provider),
                self.source / ".." / self.source.name,
                self.home,
            )
            result = (
                self.staged(provider)
                / "files"
                / ("CLAUDE.md" if provider == "claude" else "AGENTS.md")
            ).read_text()
            lines = result.splitlines()
            self.assertEqual(lines[0], "/home/dev/." + provider + "/tool.sh")
            self.assertEqual(lines[1], "/home/dev/." + provider + "/instructions.md")
            self.assertEqual(lines[2], str(self.source) + "-other/tool.sh")
            self.assertEqual(lines[3], "prefix" + str(self.source / "tool.sh"))
            self.assertEqual(
                lines[4], "/home/dev/." + provider + "/" + provider + "-other/tool.sh"
            )

    def test_noop_refresh_and_completed_backup_retention(self):
        self.stage("claude")
        self.prepare("claude")
        config = self.private("claude")
        backups = set(config.glob(".lockbox-config-backup-*"))
        timestamps = {
            str(p): p.stat().st_mtime_ns
            for p in config.rglob("*")
            if p.is_file()
            and not any(part.startswith(".lockbox-config-backup-") for part in p.parts)
        }
        for _ in range(3):
            self.prepare("claude", refresh=True)
        self.assertEqual(set(config.glob(".lockbox-config-backup-*")), backups)
        self.assertEqual(
            timestamps,
            {
                str(p): p.stat().st_mtime_ns
                for p in config.rglob("*")
                if p.is_file()
                and not any(
                    part.startswith(".lockbox-config-backup-") for part in p.parts
                )
            },
        )
        unrelated = config / ".lockbox-config-backup-private"
        self.write(unrelated / "private.txt", "private\n")
        for n in range(8):
            self.write(self.source / "AGENTS.md", f"guidance {n}\n")
            self.stage("claude")
            self.prepare("claude", refresh=True)
        completed = [
            p
            for p in config.glob(".lockbox-config-backup-*")
            if (p / ".lockbox-backup.json").exists()
            and tool.read_json(p / ".lockbox-backup.json")["status"] == "complete"
        ]
        self.assertEqual(len(completed), tool.BACKUP_LIMIT)
        self.assertEqual((unrelated / "private.txt").read_text(), "private\n")

    def test_container_errors_forward_only_fixed_categories(self):
        args = SimpleNamespace(
            stage=self.root / "locked-stage", cid="synthetic", provider="claude"
        )
        for diagnostic, expected in [
            (b"agent-config: ABORT [permission-denied]; secret", "permission-denied"),
            (b"agent-config: ABORT [secret-category]; secret", "container-operation"),
            (b"secret native error", "container-operation"),
        ]:
            with patch.object(
                tool.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=1, stderr=diagnostic),
            ):
                with self.assertRaises(tool.ImportFailure) as raised:
                    tool.locked_import(args)
            self.assertEqual(raised.exception.category, expected)
            self.assertNotIn("secret", str(raised.exception))
        self.write(
            self.source / "claude/settings.json", '{"secret-private-value": invalid}'
        )
        result = subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "stage",
                "claude",
                "--stage",
                str(self.staged("claude")),
                "--source",
                str(self.source),
            ],
            capture_output=True,
            text=True,
        )
        self.assertIn("[invalid-configuration]", result.stderr)
        self.assertNotIn("secret-private-value", result.stderr + result.stdout)

    def test_ready_private_profile_recreates_missing_host_snapshot_only_once(self):
        for provider in ("claude", "codex"):
            args = SimpleNamespace(
                stage=self.root / (provider + "-locked-stage"),
                cid="synthetic",
                provider=provider,
                refresh=False,
                source=self.source,
                home=self.home,
                import_native=False,
            )
            calls = []

            def invoke(command, **kwargs):
                mode = command[command.index("-") + 1]
                calls.append(mode)
                return SimpleNamespace(
                    returncode=0,
                    stderr=b"",
                    stdout=b"ready\n" if mode == "inspect" else b"",
                )

            with (
                patch.object(tool.subprocess, "run", side_effect=invoke),
                patch.object(tool, "stage", wraps=tool.stage) as staging,
            ):
                tool.locked_import(args)
                tool.locked_import(args)
                self.assertEqual(staging.call_count, 1)
            self.assertTrue((args.stage / "config/environment.json").is_file())
            self.assertEqual(calls.count("init"), 2)
            self.assertNotIn("refresh", calls)

    def test_common_instructions_skills_and_mcp_adapt_to_both_providers(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                self.stage(provider)
                self.prepare(provider)
                config = self.private(provider)
                instruction = "CLAUDE.md" if provider == "claude" else "AGENTS.md"
                self.assertEqual(
                    (config / instruction).read_text(), "Shared instructions\n"
                )
                self.assertEqual(
                    (config / "skills/shared/SKILL.md").read_text(), "Shared skill v1\n"
                )
                self.assertEqual(
                    (config / "skills/authored/SKILL.md").read_text(),
                    "Authored skill\n",
                )
                self.assertEqual(
                    (config / "agents/reviewer.md").read_text(), "Synthetic reviewer\n"
                )
                value, mcp = self.values(provider)
                self.assertNotIn("hooks", value)
                self.assertNotIn("projects", value)
                self.assertEqual(mcp["worker"]["args"], ["-m", "synthetic"])
                self.assertEqual(mcp["remote"]["url"], "https://synthetic.example/mcp")
                if provider == "claude":
                    self.assertEqual(
                        mcp["worker"]["env"], {"WORKER_TOKEN": "${WORKER_TOKEN}"}
                    )
                    self.assertEqual(
                        mcp["remote"]["headers"]["Authorization"],
                        "Bearer ${REMOTE_TOKEN}",
                    )
                    self.assertEqual(
                        mcp["remote"]["headers"]["X-Tenant"], "${TENANT_ID}"
                    )
                    self.assertTrue(value["enabledPlugins"]["agents-md@builtin"])
                    self.assertEqual(
                        value["pluginConfigs"]["agents-md@builtin"]["options"][
                            "instructionFiles"
                        ],
                        "claude-md-and-agents-md",
                    )
                else:
                    self.assertEqual(value["cli_auth_credentials_store"], "file")
                    self.assertEqual(mcp["worker"]["env_vars"], ["WORKER_TOKEN"])
                    self.assertEqual(
                        mcp["remote"]["bearer_token_env_var"], "REMOTE_TOKEN"
                    )
                    self.assertEqual(
                        mcp["remote"]["env_http_headers"], {"X-Tenant": "TENANT_ID"}
                    )

    def test_initial_import_and_refresh_preserve_private_auth_and_extra_entries(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                config = self.private(provider)
                native_file = config / (
                    "settings.json" if provider == "claude" else "config.toml"
                )
                private_value = {"model": "private-model", "private_preference": True}
                self.write(
                    native_file,
                    json.dumps(private_value)
                    if provider == "claude"
                    else tool.toml(private_value),
                )
                self.write(config / "skills/shared/SKILL.md", "Private skill\n")
                self.write(config / "skills/private/SKILL.md", "Private-only skill\n")
                self.write(config / "auth.json", "synthetic private authentication\n")
                self.write(
                    config / ".credentials.json", "synthetic private credentials\n"
                )
                self.write(config / "sessions/history", "private history\n")
                self.stage(provider)
                self.prepare(provider)
                self.assertEqual(self.values(provider)[0]["model"], "private-model")
                self.assertEqual(
                    (config / "skills/shared/SKILL.md").read_text(), "Private skill\n"
                )
                before = self.contents(config)
                shutil.rmtree(self.staged(provider))
                self.prepare(provider)
                self.assertEqual(
                    self.contents(config),
                    before,
                    "warm start must not read or import the host snapshot",
                )
                self.native_value(provider, {"model": "refreshed-model"})
                self.stage(provider)
                self.prepare(provider, refresh=True)
                self.assertEqual(self.values(provider)[0]["model"], "refreshed-model")
                self.assertTrue(self.values(provider)[0]["private_preference"])
                self.assertEqual(
                    (config / "skills/shared/SKILL.md").read_text(), "Shared skill v1\n"
                )
                for rel in (
                    "auth.json",
                    ".credentials.json",
                    "sessions/history",
                    "skills/private/SKILL.md",
                ):
                    self.assertEqual((config / rel).read_bytes(), before[rel])
                self.assertTrue(
                    any(
                        (p / "skills/shared/SKILL.md").read_text() == "Private skill\n"
                        for p in config.glob(".lockbox-config-backup-*")
                        if (p / "skills/shared/SKILL.md").exists()
                    )
                )

    def test_authored_native_overlay_wins_and_paths_are_portable(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                authored = self.source / provider
                content = {
                    "model": "authored-model",
                    "instructions_path": str(self.source / "instructions.md"),
                }
                if provider == "claude":
                    self.write_json(authored / "settings.json", content)
                else:
                    self.write(authored / "config.toml", tool.toml(content))
                self.stage(provider)
                self.prepare(provider)
                value, _ = self.values(provider)
                self.assertEqual(value["model"], "authored-model")
                self.assertEqual(
                    value["instructions_path"],
                    "/home/dev/." + provider + "/instructions.md",
                )

    def test_refresh_publish_failure_rolls_back_all_private_files(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                self.stage(provider)
                self.prepare(provider)
                config = self.private(provider)
                before = self.contents(config)
                self.native_value(provider, {"model": "replacement-model"})
                self.write(
                    self.home / ".agents/skills/shared/SKILL.md", "Replacement skill\n"
                )
                self.stage(provider)
                real_replace = os.replace
                failed = False

                def fail_once(source, target):
                    nonlocal failed
                    if Path(target) == config / "skills/shared/SKILL.md" and not failed:
                        failed = True
                        raise OSError("synthetic publish failure")
                    return real_replace(source, target)

                with patch.object(tool.os, "replace", side_effect=fail_once):
                    with self.assertRaises(OSError):
                        self.prepare(provider, refresh=True)
                self.assertTrue(
                    failed, "failure must occur after earlier files were published"
                )
                self.assertEqual(self.contents(config), before)
                self.assertFalse(list(config.glob(".lockbox-config-pending-*")))
                self.write(
                    self.home / ".agents/skills/shared/SKILL.md", "Shared skill v1\n"
                )

    def test_wrong_provider_marker_does_not_skip_initialization(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                self.stage(provider)
                config = self.private(provider)
                wrong = "codex" if provider == "claude" else "claude"
                self.write_json(
                    config / ".lockbox-config-ready",
                    {"version": tool.VERSION, "provider": wrong},
                )
                self.prepare(provider)
                self.assertEqual(
                    tool.read_json(config / ".lockbox-config-ready"),
                    {"version": tool.VERSION, "provider": provider},
                )
                self.assertEqual(self.values(provider)[0]["model"], "host-model")

    def test_bad_snapshot_is_rejected_before_private_configuration_changes(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                self.stage(provider)
                self.prepare(provider)
                before = self.contents(self.private(provider))
                self.write_json(
                    self.staged(provider) / "manifest.json",
                    {"version": tool.VERSION, "provider": "unknown"},
                )
                with self.assertRaises(ValueError):
                    self.prepare(provider, refresh=True)
                self.assertEqual(self.contents(self.private(provider)), before)

    def test_links_are_materialized_only_inside_curated_roots(self):
        target = self.source / "shared/SKILL.md"
        self.write(target, "Linked skill\n")
        link = self.source / "skills/linked/SKILL.md"
        link.parent.mkdir(parents=True)
        link.symlink_to(target)
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                self.stage(provider)
                imported = self.staged(provider) / "files/skills/linked/SKILL.md"
                self.assertFalse(imported.is_symlink())
                self.assertEqual(imported.read_text(), "Linked skill\n")
                before = self.contents(self.staged(provider))
                for kind in ("escape", "broken", "cycle", "private-alias"):
                    with self.subTest(kind=kind):
                        bad = self.source / "skills/bad"
                        if kind == "escape":
                            outside = self.root / "outside.txt"
                            outside.write_text("outside curated roots\n")
                            bad.symlink_to(outside)
                        elif kind == "broken":
                            bad.symlink_to(self.source / "missing")
                        elif kind == "cycle":
                            bad.symlink_to(self.source / "skills")
                        else:
                            private = self.source / "private/auth.json"
                            self.write(private, "synthetic private state\n")
                            bad.symlink_to(private)
                        with self.assertRaises((OSError, ValueError, RuntimeError)):
                            self.stage(provider)
                        self.assertEqual(self.contents(self.staged(provider)), before)
                        bad.unlink()

    def test_private_destination_links_cannot_redirect_refresh(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                self.stage(provider)
                config = self.private(provider)
                outside = self.root / (provider + "-outside")
                outside.mkdir()
                (config / "skills").symlink_to(outside)
                with self.assertRaises(ValueError):
                    self.prepare(provider, refresh=True)
                self.assertEqual(list(outside.iterdir()), [])

    def test_inline_credentials_are_refused_and_error_does_not_print_values(self):
        for provider in ("claude", "codex"):
            for key in (
                "env",
                "http_headers",
                "api_key",
                "access_token",
                "token",
                "password",
                "secret",
            ):
                with self.subTest(provider=provider, key=key):
                    value = {
                        "mcp_servers": {
                            "service": {key: {"AUTH": "synthetic-private-value"}}
                        }
                    }
                    self.native_value(provider, value)
                    with self.assertRaises(ValueError):
                        self.stage(provider)
            result = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "stage",
                    provider,
                    "--stage",
                    str(self.staged(provider)),
                    "--home",
                    str(self.home),
                    "--source",
                    str(self.source),
                    "--import-native",
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ABORT", result.stderr)
            self.assertNotIn("synthetic-private-value", result.stderr + result.stdout)

    def test_common_declaration_and_instruction_links_cannot_escape(self):
        for name in ("mcp.json", "AGENTS.md"):
            path = self.source / name
            old = path.read_text()
            path.unlink()
            outside = self.root / ("outside-" + name)
            self.write(outside, old)
            path.symlink_to(outside)
            for provider in ("claude", "codex"):
                with self.assertRaises(ValueError):
                    self.stage(provider)
            path.unlink()
            self.write(path, old)

    def test_shared_global_agents_fallback_applies_to_both_providers(self):
        (self.source / "AGENTS.md").unlink()
        self.write(
            self.home / ".codex/AGENTS.md", "Existing neutral global agreement\n"
        )
        for provider in ("claude", "codex"):
            self.stage(provider)
            self.prepare(provider)
            name = "CLAUDE.md" if provider == "claude" else "AGENTS.md"
            self.assertEqual(
                (self.private(provider) / name).read_text(),
                "Existing neutral global agreement\n",
            )

    def test_nested_plugin_mcp_cannot_copy_inline_secrets(self):
        path = self.home / ".claude/plugins/cache/example/1.0/.mcp.json"
        self.write_json(
            path,
            {
                "mcpServers": {
                    "example": {
                        "command": "demo",
                        "env": {"API_KEY": "synthetic-private-value"},
                    }
                }
            },
        )
        with self.assertRaises(ValueError):
            self.stage("claude")
        self.assertFalse(self.staged("claude").exists())
        self.write_json(
            path,
            {
                "mcpServers": {
                    "example": {
                        "command": "demo",
                        "env": {"API_KEY": "${EXAMPLE_API_KEY}"},
                    }
                }
            },
        )
        self.stage("claude")
        self.assertIn(
            "${EXAMPLE_API_KEY}",
            (
                self.staged("claude") / "files/plugins/cache/example/1.0/.mcp.json"
            ).read_text(),
        )

    def test_environment_authorization_uses_only_authored_snapshot(self):
        original = tool.read_json(self.source / "mcp.json")
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                self.stage(provider)
                self.prepare(provider)
                config = self.private(provider)
                if provider == "claude":
                    self.write_json(
                        config / ".claude.json",
                        {
                            "mcpServers": {
                                "unsafe": {"env": {"GH_TOKEN": "${GH_TOKEN}"}}
                            }
                        },
                    )
                else:
                    self.write(
                        config / "config.toml",
                        tool.toml(
                            {"mcp_servers": {"unsafe": {"env_vars": ["GH_TOKEN"]}}}
                        ),
                    )
                output = io.StringIO()
                with redirect_stdout(output):
                    tool.environment_names(provider, config, self.staged(provider))
                self.assertEqual(
                    output.getvalue().splitlines(),
                    ["REMOTE_TOKEN", "TENANT_ID", "WORKER_TOKEN"],
                )
                for name in (
                    "BASH_ENV",
                    "ENV",
                    "PATH",
                    "HOME",
                    "SSH_AUTH_SOCK",
                    "CODEX_HOME",
                    "CLAUDE_CONFIG_DIR",
                    "GH_TOKEN",
                    "GITHUB_TOKEN",
                    "GITHUB_API_TOKEN",
                ):
                    with self.subTest(reference=name):
                        self.write_json(
                            self.source / "mcp.json",
                            {"unsafe": {"command": "python3", "env_vars": [name]}},
                        )
                        with self.assertRaises(ValueError):
                            self.stage(provider)
                self.write_json(self.source / "mcp.json", original)


if __name__ == "__main__":
    unittest.main()

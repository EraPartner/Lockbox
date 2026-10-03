#!/usr/bin/env python3
"""Curated, transactional provider configuration. Never import authentication."""

import argparse
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

VERSION = 3
BACKUP_LIMIT = 5
ERROR_CATEGORIES = {
    "permission-denied": "configuration destination is not writable",
    "invalid-configuration": "configuration validation failed",
    "import-busy": "configuration import already running; retry after it finishes",
    "recovery-required": "configuration rollback is incomplete; recover the retained backup before retrying",
    "io-failure": "configuration file operation failed",
    "container-operation": "container configuration operation failed",
}


class ImportFailure(ValueError):
    def __init__(self, category):
        self.category = category
        super().__init__(ERROR_CATEGORIES[category])


def error_category(error):
    if isinstance(error, ImportFailure):
        return error.category
    if isinstance(error, PermissionError):
        return "permission-denied"
    if isinstance(error, OSError):
        return "io-failure"
    return "invalid-configuration"


def rewrite_paths(text, replacements):
    # Match complete path roots, never siblings or roots embedded in other text.
    roots = sorted(replacements, key=len, reverse=True)
    if not roots:
        return text
    pattern = (
        r"(?<![\w/.-])("
        + "|".join(re.escape(root) for root in roots)
        + r")(?=/|[\s\"'`),;\]}]|$)"
    )
    return re.sub(pattern, lambda match: replacements[match.group(1)], text)


DENIED_ENVIRONMENT = {
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
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
}
CLAUDE_ITEMS = (
    "settings.json",
    "keybindings.json",
    "agents",
    "rules",
    "commands",
    "skills",
    "status-line.sh",
    "statusline",
    "plugins",
)
CODEX_ITEMS = ("config.toml", "AGENTS.md", "agents")
FORBIDDEN = {
    ".git",
    ".credentials.json",
    "auth.json",
    ".env",
    "credentials.json",
    "settings.local.json",
    "history.jsonl",
    "sessions",
    "projects",
    "memory",
}


def merge(base, overlay):
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        result[key] = (
            merge(result[key], value)
            if isinstance(result.get(key), dict) and isinstance(value, dict)
            else copy.deepcopy(value)
        )
    return result


def read_json(path):
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("configuration must be an object")
    return value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("configuration marker is a symlink")
    fd, name = tempfile.mkstemp(prefix=".agent-json-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def snapshot(source, target, roots, seen=()):
    """Materialize allowed links, refusing escapes, cycles and private state."""
    resolved = source.resolve(strict=True)
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise ValueError("configuration link escapes curated roots")
    if resolved in seen:
        raise ValueError("configuration link cycle")
    root = next(root for root in roots if resolved == root or root in resolved.parents)
    if (
        any(
            part in FORBIDDEN or part.startswith(".env.")
            for part in resolved.relative_to(root).parts
        )
        or source.name in FORBIDDEN
        or resolved.name in FORBIDDEN
        or source.name.startswith(".env.")
    ):
        raise ValueError("private state in curated configuration")
    if resolved.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for child in sorted(resolved.iterdir()):
            if child.name == ".git":
                continue
            snapshot(child, target / child.name, roots, seen + (resolved,))
    elif resolved.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resolved, target)
    else:
        raise ValueError("unsupported configuration file type")


def validate_no_inline_secrets(node):
    if isinstance(node, dict):
        for key, item in node.items():
            if key in ("env", "headers", "http_headers") and item:
                if not isinstance(item, dict) or not all(
                    isinstance(value, str)
                    and re.fullmatch(r"(?:Bearer )?\$\{[A-Z_][A-Z0-9_]*\}", value)
                    for value in item.values()
                ):
                    raise ValueError(
                        "inline credentials/environment require explicit environment references"
                    )
            if (
                key in ("api_key", "access_token", "token", "password", "secret")
                and item
            ):
                if not isinstance(item, str) or not re.fullmatch(
                    r"\$\{[A-Z_][A-Z0-9_]*\}", item
                ):
                    raise ValueError(
                        "inline credentials require explicit environment references"
                    )
            validate_no_inline_secrets(item)
    elif isinstance(node, list):
        for item in node:
            validate_no_inline_secrets(item)


def clean_native(value, provider):
    value = copy.deepcopy(value)
    # Host hooks and trust decisions must not execute or authorize container work.
    for key in (
        "hooks",
        "projects",
        "oauthAccount",
        "installMethod",
        "autoUpdatesProtectedForNative",
    ):
        value.pop(key, None)
    validate_no_inline_secrets(value)
    if provider == "codex":
        value["cli_auth_credentials_store"] = "file"
        for server in value.get("mcp_servers", {}).values():
            environment = server.pop("env", {})
            for name, template in environment.items():
                if template != "${" + name + "}":
                    raise ValueError(
                        "Codex MCP environment references must use the same variable name"
                    )
                server.setdefault("env_vars", []).append(name)
    else:
        value = merge(
            value,
            {
                "pluginConfigs": {
                    "agents-md@builtin": {
                        "options": {"instructionFiles": "claude-md-and-agents-md"}
                    }
                }
            },
        )
        value.setdefault("enabledPlugins", {})["agents-md@builtin"] = True
    return value


def scalar(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list) and not any(isinstance(item, dict) for item in value):
        return "[" + ", ".join(scalar(item) for item in value) + "]"
    raise ValueError("unsupported TOML value")


def toml(value):
    lines = []

    def table(node, path=(), array=False):
        if path:
            name = ".".join(json.dumps(part) for part in path)
            lines.append(
                (" [[" if array else " [").strip() + name + ("]]" if array else "]")
            )
        for key, item in node.items():
            if not isinstance(item, dict) and not (
                isinstance(item, list) and item and isinstance(item[0], dict)
            ):
                lines.append(json.dumps(key) + " = " + scalar(item))
        for key, item in node.items():
            if isinstance(item, dict):
                table(item, path + (key,))
            elif isinstance(item, list) and item and isinstance(item[0], dict):
                for row in item:
                    table(row, path + (key,), True)

    table(value)
    result = "\n".join(lines) + "\n"
    tomllib.loads(result)
    return result


def mcp_adapters(declarations):
    claude, codex = {}, {}
    for name, server in declarations.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or not isinstance(server, dict):
            raise ValueError("invalid MCP declaration")
        transport = server.get("type", "stdio")
        allowed = (
            {"type", "command", "args", "env_vars"}
            if transport == "stdio"
            else {"type", "url", "bearer_token_env_var", "env_http_headers"}
        )
        if transport not in ("stdio", "http") or set(server) - allowed:
            raise ValueError("unsupported shared MCP option")
        if transport == "stdio":
            if not isinstance(server.get("command"), str) or not server["command"]:
                raise ValueError("MCP command required")
            args = server.get("args", [])
            names = server.get("env_vars", [])
            if (
                not isinstance(args, list)
                or not all(isinstance(x, str) for x in args)
                or not isinstance(names, list)
            ):
                raise ValueError("invalid MCP arguments")
            c = {"type": "stdio", "command": server["command"], "args": args, "env": {}}
            d = {"command": server["command"], "args": args, "env_vars": names}
            for env in names:
                if not isinstance(env, str) or not re.fullmatch(
                    r"[A-Z_][A-Z0-9_]*", env
                ):
                    raise ValueError("invalid MCP environment reference")
                c["env"][env] = "${" + env + "}"
        else:
            url = server.get("url")
            if (
                not isinstance(url, str)
                or not url.startswith("https://")
                or "@" in url
                or "?" in url
                or "#" in url
            ):
                raise ValueError(
                    "shared MCP URL must be HTTPS without credentials/query"
                )
            c, d = {"type": "http", "url": url}, {"url": url}
            headers = server.get("env_http_headers", {})
            if not isinstance(headers, dict):
                raise ValueError("invalid MCP headers")
            headers = dict(headers)
            token = server.get("bearer_token_env_var")
            if token:
                headers["Authorization"] = token
                d["bearer_token_env_var"] = token
            c["headers"] = {}
            for header, env in headers.items():
                if (
                    not isinstance(header, str)
                    or not re.fullmatch(r"[A-Za-z0-9-]+", header)
                    or not isinstance(env, str)
                    or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", env)
                ):
                    raise ValueError("invalid MCP header reference")
                c["headers"][header] = (
                    ("Bearer " if header == "Authorization" and token else "")
                    + "${"
                    + env
                    + "}"
                )
            d["env_http_headers"] = {
                k: v for k, v in headers.items() if not (k == "Authorization" and token)
            }
        claude[name], codex[name] = c, d
    return claude, codex


def stage(provider, destination, source, home, import_native=False):
    if not source.is_dir():
        print(
            "launcher: authored configuration source is missing or not a directory; "
            "shared instructions, skills and MCP declarations will not be imported. "
            "Populate LOCKBOX_CONFIG_SOURCE and explicitly refresh existing profiles.",
            file=sys.stderr,
        )
    native = home / (".claude" if provider == "claude" else ".codex")
    shared_skills = home / ".agents/skills"
    inputs = (source, native, shared_skills) if import_native else (source,)
    roots = [p.resolve() for p in inputs if p.exists()]
    instructions = next(
        (
            path
            for path in (
                (
                    source / "AGENTS.md",
                    home / ".agents/AGENTS.md",
                    home / ".codex/AGENTS.md",
                )
                if import_native
                else (source / "AGENTS.md",)
            )
            if path.exists() or path.is_symlink()
        ),
        None,
    )
    if instructions is not None and instructions != source / "AGENTS.md":
        roots.append(instructions.parent.resolve())
    pending = Path(tempfile.mkdtemp(prefix=".agent-stage-", dir=destination.parent))
    try:
        tree = pending / "files"
        tree.mkdir()
        items = CLAUDE_ITEMS if provider == "claude" else CODEX_ITEMS
        for name in items:
            if import_native and (
                (native / name).exists() or (native / name).is_symlink()
            ):
                snapshot(native / name, tree / name, roots)
        authored = source / provider
        for name in items:
            if (authored / name).exists() or (authored / name).is_symlink():
                target = tree / name
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
                snapshot(authored / name, target, roots)
        if import_native and shared_skills.exists():
            snapshot(shared_skills, tree / "skills", roots)
        if (source / "skills").exists():
            snapshot(source / "skills", tree / "skills", roots)
        if instructions is not None:
            snapshot(
                instructions,
                tree / ("CLAUDE.md" if provider == "claude" else "AGENTS.md"),
                roots,
            )
        declarations = {}
        declaration_source = source / "mcp.json"
        if declaration_source.exists() or declaration_source.is_symlink():
            snapshot(declaration_source, pending / "shared-mcp.json", roots)
            declarations = read_json(pending / "shared-mcp.json")
            (pending / "shared-mcp.json").unlink()
        c_mcp, d_mcp = mcp_adapters(declarations)
        if provider == "claude":
            value = (
                read_json(tree / "settings.json")
                if (tree / "settings.json").exists()
                else {}
            )
            write_json(tree / "settings.json", clean_native(value, provider))
            write_json(tree / ".claude.json", {"mcpServers": c_mcp})
        else:
            value = (
                tomllib.loads((tree / "config.toml").read_text())
                if (tree / "config.toml").exists()
                else {}
            )
            value = clean_native(value, provider)
            value["mcp_servers"] = {**value.get("mcp_servers", {}), **d_mcp}
            (tree / "config.toml").write_text(toml(value))
        # Plugin MCP/settings files are authored configuration too. Validate them
        # before snapshot publication, including nested plugin cache manifests.
        for path in tree.rglob("*.json"):
            if path.is_file() and path.name in {
                ".mcp.json",
                "mcp.json",
                "settings.json",
                "config.json",
                "installed_plugins.json",
                "known_marketplaces.json",
                ".claude.json",
            }:
                validate_no_inline_secrets(read_json(path))
        # Rewrite only the known authored roots; never import a host credential path.
        replacements = {
            str(source / provider): "/home/dev/." + provider,
            str(source): "/home/dev/." + provider,
        }
        if import_native:
            replacements = {
                str(native): "/home/dev/." + provider,
                str(shared_skills): "/home/dev/." + provider + "/skills",
                **replacements,
            }
        normalized = {os.path.normpath(old): new for old, new in replacements.items()}
        normalized.update(
            {str(Path(old).resolve()): new for old, new in replacements.items()}
        )
        for path in tree.rglob("*"):
            if path.is_file() and path.suffix in (".json", ".toml", ".md", ".sh"):
                try:
                    text = path.read_text()
                except UnicodeError:
                    continue
                text = rewrite_paths(text, normalized)
                path.write_text(text)
        write_json(
            pending / "manifest.json", {"version": VERSION, "provider": provider}
        )
        # The private config is writable by the agent; it cannot request host secrets.
        write_json(
            pending / "environment.json",
            {"names": referenced_environment(provider, tree)},
        )
        old = destination.with_name(destination.name + ".previous")
        if old.exists():
            shutil.rmtree(old)
        if destination.exists():
            os.replace(destination, old)
        try:
            os.replace(pending, destination)
        except Exception:
            if old.exists():
                os.replace(old, destination)
            raise
    finally:
        if pending.exists():
            shutil.rmtree(pending)


@contextmanager
def profile_lock(path):
    """Serialize imports; kernel releases locks even if the launcher is killed."""
    if path.parent.is_symlink():
        raise ValueError("configuration lock directory must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ImportFailure("import-busy") from None
        yield
    finally:
        os.close(fd)


def native_file(provider, rel):
    return str(rel) in (
        ("settings.json", ".claude.json") if provider == "claude" else ("config.toml",)
    )


def config_path(config, rel, provider, allow_directory=False):
    allowed = set(CLAUDE_ITEMS if provider == "claude" else CODEX_ITEMS) | {"skills"}
    allowed |= {"CLAUDE.md", ".claude.json"} if provider == "claude" else set()
    if (
        rel.is_absolute()
        or not rel.parts
        or rel.parts[0] not in allowed
        or any(
            part in FORBIDDEN or part in (".", "..") or part.startswith(".lockbox-")
            for part in rel.parts
        )
    ):
        raise ValueError("invalid managed configuration path")
    target = config / rel
    if any(
        p.is_symlink()
        for p in (target, *target.parents)
        if p == config or config in p.parents
    ):
        raise ValueError("private configuration destination is a symlink")
    if (
        target.exists()
        and not target.is_file()
        and not (allow_directory and target.is_dir())
    ):
        raise ValueError("configuration destination is not a regular file")
    return target


def config_values(value, prefix=()):
    # MCP servers are owned as complete entries, so removal disables the server.
    if (
        isinstance(value, dict)
        and value
        and not (len(prefix) == 2 and prefix[0] in ("mcpServers", "mcp_servers"))
    ):
        for key, item in value.items():
            yield from config_values(item, prefix + (key,))
    else:
        yield prefix, value


def lookup(value, keys):
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return False, None
        value = value[key]
    return True, value


def remove_value(value, keys):
    key = keys[0]
    if len(keys) == 1:
        value.pop(key, None)
    else:
        remove_value(value[key], keys[1:])
        if not value[key]:
            del value[key]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(provider, config, staged, refresh):
    with profile_lock(config / ".lockbox-config-lock"):
        _prepare(provider, config, staged, refresh)


def prune_backups(config, provider):
    completed = []
    for path in config.glob(".lockbox-config-backup-*"):
        record = path / ".lockbox-backup.json"
        if path.is_symlink() or not path.is_dir() or record.is_symlink():
            continue
        try:
            if read_json(record) != {
                "version": 1,
                "provider": provider,
                "status": "complete",
            }:
                continue
            if any(p.is_symlink() for p in path.rglob("*")):
                continue
            completed.append((path.stat().st_mtime_ns, path))
        except (OSError, ValueError):
            continue
    completed.sort(key=lambda item: item[0], reverse=True)
    for _, path in completed[BACKUP_LIMIT:]:
        try:
            shutil.rmtree(path)
        except OSError:
            print(
                "agent-config: backup retention deferred; a completed backup is not writable."
            )


def publish_plan(config, plan, provider):
    """Publish successful mutations only; an interrupted import is never ready."""
    marker = config / ".lockbox-config-ready"
    journal = config / ".lockbox-config-transaction.json"
    # Identical content and permissions are not mutations and need no backup.
    plan = {
        rel: update
        for rel, update in plan.items()
        if not (
            update is None
            and not (config / rel).exists()
            or update is not None
            and (config / rel).is_file()
            and (config / rel).read_bytes() == update[0]
            and (config / rel).stat().st_mode & 0o777 == update[1]
        )
    }
    if not plan:
        print(
            f"{provider}: private configuration already matches the authored snapshot."
        )
        return
    # A directory can become a file only if every descendant is being retired.
    directories = set()
    for rel, update in plan.items():
        target = config / rel
        if update is not None and target.is_dir():
            retired = [r for r, u in plan.items() if u is None and rel in r.parents]
            if not retired:
                raise ValueError(
                    "directory transition would replace private configuration"
                )
            directories.add(rel)
            for child in target.rglob("*"):
                child_rel = child.relative_to(config)
                if child.is_symlink() or not (child.is_dir() or child.is_file()):
                    raise ValueError("unsupported configuration directory transition")
                if child.is_dir():
                    if not any(
                        child_rel in retired_path.parents for retired_path in retired
                    ):
                        raise ValueError(
                            "directory transition would replace private configuration"
                        )
                    directories.add(child_rel)
                elif child_rel not in plan or plan[child_rel] is not None:
                    raise ValueError(
                        "directory transition would replace private configuration"
                    )
        for parent in target.parents:
            if parent == config:
                break
            if (
                parent.is_file()
                and plan.get(parent.relative_to(config), "absent") is not None
            ):
                raise ValueError("file transition would replace private configuration")
    backup = Path(tempfile.mkdtemp(prefix=".lockbox-config-backup-", dir=config))
    pending = Path(tempfile.mkdtemp(prefix=".lockbox-config-pending-", dir=config))
    changed, created_dirs, removed_dirs = [], [], []
    marker_existed = marker.exists()
    journal_started = False
    try:
        for rel, update in plan.items():
            target = config / rel
            if target.is_file():
                (backup / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup / rel)
            if update is not None:
                content, mode = update
                (pending / rel).parent.mkdir(parents=True, exist_ok=True)
                (pending / rel).write_bytes(content)
                (pending / rel).chmod(mode)
        # Readiness is saved even if unchanged by the final plan.
        if marker_existed and not (backup / marker.name).exists():
            shutil.copy2(marker, backup / marker.name)
        write_json(
            backup / ".lockbox-backup.json",
            {"version": 1, "provider": provider, "status": "pending"},
        )
        write_json(journal, {"version": 1, "provider": provider, "backup": backup.name})
        journal_started = True
        marker.unlink(missing_ok=True)
        for rel in sorted(
            (r for r, u in plan.items() if u is None),
            key=lambda r: len(r.parts),
            reverse=True,
        ):
            (config / rel).unlink()
            changed.append(rel)
        for rel in sorted(directories, key=lambda r: len(r.parts), reverse=True):
            mode = (config / rel).stat().st_mode & 0o777
            (config / rel).rmdir()
            removed_dirs.append((rel, mode))
        for rel, update in plan.items():
            if update is None or rel.name == marker.name:
                continue
            target = config / rel
            missing = []
            for parent in target.parents:
                if parent == config or parent.exists():
                    break
                missing.append(parent)
            for parent in reversed(missing):
                parent.mkdir()
                created_dirs.append(parent)
            os.replace(pending / rel, target)
            changed.append(rel)
        # Complete backup ownership is recorded before publishing readiness.
        write_json(
            backup / ".lockbox-backup.json",
            {"version": 1, "provider": provider, "status": "complete"},
        )
        if Path(marker.name) in plan:
            os.replace(pending / marker.name, marker)
        else:
            shutil.copy2(backup / marker.name, marker)
        journal.unlink()
    except Exception as error:
        failures, deferred = [], []
        # Restore every successful mutation even when a separate restoration fails.
        for rel in reversed(changed):
            target = config / rel
            try:
                if (backup / rel).is_file():
                    if target.is_dir():
                        deferred.append(rel)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd, temporary = tempfile.mkstemp(
                        prefix=".agent-restore-", dir=target.parent
                    )
                    os.close(fd)
                    try:
                        shutil.copy2(backup / rel, temporary)
                        os.replace(temporary, target)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                failures.append(rel)
        # Remove new directory scaffolding before restoring an ancestor file.
        for path in reversed(created_dirs):
            try:
                path.rmdir()
            except OSError:
                if path.exists():
                    failures.append(path)
        for rel, mode in reversed(removed_dirs):
            try:
                (config / rel).mkdir(parents=True, exist_ok=True)
                (config / rel).chmod(mode)
            except OSError:
                failures.append(rel)
        # A deleted ancestor file may have been blocked by new scaffolding above.
        for rel in deferred:
            if (backup / rel).is_file():
                try:
                    if (config / rel).is_dir():
                        (config / rel).rmdir()
                    shutil.copy2(backup / rel, config / rel)
                    failures = [failure for failure in failures if failure != rel]
                except OSError:
                    failures.append(rel)
        if journal_started:
            try:
                marker.unlink(missing_ok=True)
                write_json(
                    backup / ".lockbox-backup.json",
                    {
                        "version": 1,
                        "provider": provider,
                        "status": "rollback-incomplete" if failures else "rolled-back",
                    },
                )
                if not failures and marker_existed:
                    shutil.copy2(backup / marker.name, marker)
                if not failures:
                    journal.unlink()
            except OSError:
                failures.append(marker)
                try:
                    marker.unlink(missing_ok=True)
                except OSError:
                    pass  # The journal still blocks inspect and initialization.
        if failures:
            raise ImportFailure("recovery-required") from None
        raise error
    finally:
        shutil.rmtree(pending)
    prune_backups(config, provider)
    print(f"{provider}: private configuration published; rollback backup: {backup}")


def _prepare(provider, config, staged, refresh):
    marker = config / ".lockbox-config-ready"
    manifest = {"version": VERSION, "provider": provider}
    if (config / ".lockbox-config-transaction.json").exists() or (
        config / ".lockbox-config-transaction.json"
    ).is_symlink():
        raise ImportFailure("recovery-required")
    if marker.exists() and read_json(marker) == manifest and not refresh:
        return
    if read_json(staged / "manifest.json") != manifest:
        raise ValueError("invalid configuration snapshot")
    tree = staged / "files"
    if tree.is_symlink() or any(path.is_symlink() for path in tree.rglob("*")):
        raise ValueError("snapshot contains unmaterialized links")
    ownership = config / ".lockbox-config-managed.json"
    previous = read_json(ownership) if ownership.exists() else {**manifest, "files": {}}
    if (
        set(previous) != {"version", "provider", "files"}
        or previous["version"] != VERSION
        or previous["provider"] != provider
        or not isinstance(previous["files"], dict)
    ):
        raise ValueError("invalid managed configuration record")
    incoming = {str(p.relative_to(tree)): p for p in tree.rglob("*") if p.is_file()}
    plan, managed, conflicts = {}, {}, 0
    for name in sorted(set(incoming) | (set(previous["files"]) if refresh else set())):
        rel = Path(name)
        target = config_path(config, rel, provider, allow_directory=True)
        source = incoming.get(name)
        old = previous["files"].get(name, {})
        if native_file(provider, rel):
            load = (
                (lambda p: tomllib.loads(p.read_text()))
                if rel.name == "config.toml"
                else read_json
            )
            existing = load(target) if target.exists() else {}
            authored = load(source) if source is not None else {}
            value = copy.deepcopy(existing)
            current_values = dict(config_values(authored))
            if refresh:
                for row in old.get("values", []):
                    keys, baseline = tuple(row["path"]), row["value"]
                    if not keys or not all(isinstance(k, str) for k in keys):
                        raise ValueError("invalid managed configuration keys")
                    if keys not in current_values:
                        present, current = lookup(value, keys)
                        if present and current == baseline:
                            remove_value(value, keys)
                        elif present:
                            conflicts += 1
            value = merge(value, authored) if refresh else merge(authored, value)
            for registry in ("mcpServers", "mcp_servers"):
                for server_name, definition in authored.get(registry, {}).items():
                    value.setdefault(registry, {})[server_name] = copy.deepcopy(
                        definition
                        if refresh
                        else existing.get(registry, {}).get(server_name, definition)
                    )
            if rel.name != ".claude.json":
                value = merge(value, clean_native({}, provider))
            owned = []
            for keys, expected in config_values(authored):
                present, current = lookup(value, keys)
                was_present, _ = lookup(existing, keys)
                was_owned = any(
                    tuple(row["path"]) == keys for row in old.get("values", [])
                )
                if (
                    keys
                    and present
                    and current == expected
                    and (refresh or not was_present or was_owned)
                ):
                    owned.append({"path": list(keys), "value": expected})
            managed[name] = {"values": owned}
            content = (
                toml(value)
                if rel.name == "config.toml"
                else json.dumps(value, indent=2) + "\n"
            ).encode()
            if source is not None or target.exists():
                plan[rel] = (content, 0o600)
        elif source is not None:
            if refresh or not target.exists():
                content = source.read_bytes()
                plan[rel] = (content, source.stat().st_mode & 0o777)
                managed[name] = {"sha256": hashlib.sha256(content).hexdigest()}
            elif name in previous["files"]:
                managed[name] = old
        elif target.is_file():
            if digest(target) == old.get("sha256"):
                plan[rel] = None
            else:
                conflicts += 1
    for path, value in (
        (marker, manifest),
        (ownership, {**manifest, "files": managed}),
    ):
        if path.is_symlink():
            raise ValueError("configuration marker is a symlink")
        plan[path.relative_to(config)] = (
            (json.dumps(value, indent=2) + "\n").encode(),
            0o600,
        )
    publish_plan(config, plan, provider)
    if conflicts:
        print(
            f"{provider}: preserved {conflicts} locally changed entries removed from the source; they are now private."
        )


def locked_import(args):
    with profile_lock(args.stage / ".import-lock"):

        def invoke(mode):
            result = subprocess.run(
                [
                    "container",
                    "exec",
                    "-i",
                    "--user",
                    "dev",
                    "-e",
                    "BASH_ENV=",
                    args.cid,
                    "/opt/python/bin/python3",
                    "-",
                    mode,
                    args.provider,
                    "--stage",
                    "/home/dev/.agent-stage-root/config",
                ],
                input=Path(__file__).read_bytes(),
                capture_output=True,
            )
            if result.returncode:
                # Fixed importer diagnostics only; never echo native command data.
                match = re.search(rb"agent-config: ABORT \[([a-z-]+)\]", result.stderr)
                category = match.group(1).decode() if match else "container-operation"
                raise ImportFailure(
                    category if category in ERROR_CATEGORIES else "container-operation"
                )
            return result.stdout.decode()

        if (
            invoke("inspect").strip() != "ready"
            or args.refresh
            # A replaced host or mount layout still needs authored environment
            # authorization. init leaves an already ready private profile alone.
            or not (args.stage / "config/manifest.json").is_file()
        ):
            stage(
                args.provider,
                args.stage / "config",
                args.source,
                args.home,
                args.import_native,
            )
            print(invoke("refresh" if args.refresh else "init"), end="")
        else:
            invoke("init")
        print(invoke("report"), end="")


def report(provider, config):
    skills = config / "skills"
    instruction = config / ("CLAUDE.md" if provider == "claude" else "AGENTS.md")
    print(
        f"{provider}: global instruction file {'present' if instruction.exists() else 'absent; project AGENTS.md remains available'}; {len(list(skills.glob('*/SKILL.md')))} skill files present. Native discovery is checked separately."
    )
    value = (
        read_json(config / ".claude.json")
        if provider == "claude"
        else tomllib.loads((config / "config.toml").read_text())
    )
    for name, server in value.get(
        "mcpServers" if provider == "claude" else "mcp_servers", {}
    ).items():
        command = server.get("command")
        present = not command or shutil.which(command) is not None
        print(
            f"{provider}: MCP {name}: {'configured; connection not checked here' if present else 'unavailable command; install it in the image'}"
        )


def referenced_environment(provider, config):
    value = (
        read_json(config / ".claude.json")
        if provider == "claude"
        else tomllib.loads((config / "config.toml").read_text())
    )
    names = set()
    servers = list(
        value.get("mcpServers" if provider == "claude" else "mcp_servers", {}).values()
    )
    if provider == "claude":
        for path in (config / "plugins").rglob("*"):
            if path.is_file() and path.name in (".mcp.json", "mcp.json"):
                nested = read_json(path)
                validate_no_inline_secrets(nested)
                servers.extend(nested.get("mcpServers", nested).values())
    for server in servers:
        if provider == "codex":
            names.update(server.get("env_vars", []))
            names.update(server.get("env_http_headers", {}).values())
            if server.get("bearer_token_env_var"):
                names.add(server["bearer_token_env_var"])
        else:
            for text in list(server.get("env", {}).values()) + list(
                server.get("headers", {}).values()
            ):
                if isinstance(text, str):
                    names.update(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\}", text))
    for name in sorted(names):
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name)
            or name in DENIED_ENVIRONMENT
        ):
            raise ValueError("unsafe MCP environment reference")
    return sorted(names)


def environment_names(provider, config, staged=None):
    # Only the read-only authored snapshot can authorize host variables.
    del provider, config
    if staged is None or not (staged / "environment.json").exists():
        return
    value = read_json(staged / "environment.json")
    if set(value) != {"names"} or not isinstance(value["names"], list):
        raise ValueError("invalid authored environment authorization")
    for name in value["names"]:
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name)
            or name in DENIED_ENVIRONMENT
        ):
            raise ValueError("unsafe MCP environment reference")
        print(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=(
            "stage",
            "inspect",
            "init",
            "refresh",
            "report",
            "environment",
            "locked-import",
        ),
    )
    parser.add_argument("provider", choices=("claude", "codex"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--stage", type=Path, default=Path("/home/dev/.agent-stage"))
    parser.add_argument(
        "--source", type=Path, default=Path.home() / ".config/lockbox/agents"
    )
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--import-native", action="store_true")
    parser.add_argument("--cid")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    config = args.config or Path("/home/dev/." + args.provider)
    if args.mode == "stage":
        args.stage.parent.mkdir(parents=True, exist_ok=True)
        stage(args.provider, args.stage, args.source, args.home, args.import_native)
    elif args.mode == "locked-import":
        if not args.cid:
            raise ValueError("container identity required")
        locked_import(args)
    elif args.mode == "inspect":
        if (config / ".lockbox-config-transaction.json").exists() or (
            config / ".lockbox-config-transaction.json"
        ).is_symlink():
            raise ImportFailure("recovery-required")
        marker = config / ".lockbox-config-ready"
        print(
            "ready"
            if marker.exists()
            and read_json(marker) == {"version": VERSION, "provider": args.provider}
            else "import"
        )
    elif args.mode == "report":
        report(args.provider, config)
    elif args.mode == "environment":
        environment_names(args.provider, config, args.stage)
    else:
        config.mkdir(mode=0o700, parents=True, exist_ok=True)
        prepare(args.provider, config, args.stage, args.mode == "refresh")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        # Parser and I/O exceptions can contain authored values. Print fixed
        # categories only, including through the host/container boundary.
        category = error_category(error)
        print(
            f"agent-config: ABORT [{category}]; {ERROR_CATEGORIES[category]}. No configuration values printed.",
            file=sys.stderr,
        )
        sys.exit(1)

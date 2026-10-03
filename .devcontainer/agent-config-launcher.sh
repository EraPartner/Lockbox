#!/usr/bin/env bash
# Shared in-repo native login and curated configuration policy.
lockbox_host_python() {
  local candidate
  for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import tomllib' >/dev/null 2>&1; then
      printf '%s\n' "$candidate"; return 0
    fi
  done
  echo 'launcher: curated configuration requires host Python 3.11 or newer.' >&2
  return 1
}

lockbox_acquire_startup_lock() {
  local stage="$1" script="$2" python
  shift 2
  if [[ "${LOCKBOX_STARTUP_LOCK:-}" == "$stage/.startup-lock" && -e /dev/fd/9 ]]; then return 0; fi
  python="$(lockbox_host_python)" || return 1
  exec "$python" "$(dirname "$AGENT_CONFIG_TOOL")/launch-lock.py" "$stage/.startup-lock" "$script" "$@"
}

lockbox_release_startup_lock() {
  exec 9>&-
  unset LOCKBOX_STARTUP_LOCK
}

lockbox_require_staging_boundary() {
  local workspace="$1" staging_root="$2"
  case "$workspace/" in
    "$staging_root/"*) echo 'launcher: writable workspace overlaps private host staging; refusing launch.' >&2; return 1 ;;
  esac
  case "$staging_root/" in
    "$workspace/"*) echo 'launcher: private host staging is inside the writable workspace; refusing launch.' >&2; return 1 ;;
  esac
}

lockbox_wait_proxy() {
  # Keep this bounded readiness barrier separate from lifecycle. Exhaustion is
  # an error even when a stale firewall sentinel exists from an earlier start.
  # shellcheck disable=SC2016
  container exec --user dev -e BASH_ENV= "$1" /bin/bash --noprofile --norc -c '
    for _ in $(seq 1 150); do
      if (exec 3<>/dev/tcp/127.0.0.1/3128) 2>/dev/null; then exit 0; fi
      sleep 0.2
    done
    echo "launcher: egress proxy did not become ready within 30 seconds." >&2
    exit 1'
}

lockbox_container_generation() {
  container exec --user dev -e BASH_ENV= "$1" /opt/python/bin/python3 -c '
from pathlib import Path
boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
# /proc stat comm can contain spaces. Field 22 follows 20 fields after comm.
started = Path("/proc/1/stat").read_text().rsplit(") ", 1)[1].split()[19]
assert len(boot) == 36 and all(c in "0123456789abcdef-" for c in boot)
assert started.isdigit()
print(boot + ":" + started)
'
}

lockbox_require_managed_policy() {
  [[ "$AGENT" == claude ]] || return 0
  # Detect an old image even when a new container has the current launch policy.
  local expected actual
  expected="$(shasum -a 256 "$(dirname "$AGENT_CONFIG_TOOL")/claude-guard.mjs" | cut -d' ' -f1)"
  actual="$(container exec "$1" sha256sum /usr/local/share/claude-guard/guard.mjs 2>/dev/null | cut -d' ' -f1)"
  if [[ "$actual" != "$expected" ]]; then
    echo 'launcher: missing or stale baked Claude policy; rebuild the selected image and container.' >&2
    return 1
  fi
  container exec --user dev -e BASH_ENV= "$1" node /usr/local/share/claude-guard/guard.mjs --managed-settings \
    | container exec -i --user dev -e BASH_ENV= "$1" python3 -c '
import json, os, pathlib, stat, sys
paths = [pathlib.Path("/usr/local/share/claude-guard/guard.mjs"), pathlib.Path("/etc/claude-code/managed-settings.json")]
assert json.load(sys.stdin) == json.loads(paths[1].read_text())
for path in paths:
    for item in (path, *path.parents):
        info = item.stat()
        assert not item.is_symlink() and info.st_uid == 0 and not info.st_mode & 0o022
    assert not os.access(path, os.W_OK)
'
}

lockbox_agent_config_run() {
  container exec -i --user dev -e BASH_ENV= "$1" /opt/python/bin/python3 - "$2" "$AGENT" --stage /home/dev/.agent-stage-root/config < "$AGENT_CONFIG_TOOL"
}

lockbox_require_agent_layout() {
  local key expected
  if [[ "$AGENT" == claude ]]; then key=CLAUDE_CONFIG_DIR; expected=/home/dev/.claude;
  else key=CODEX_HOME; expected=/home/dev/.codex; fi
  if [[ "$(container exec "$1" printenv "$key")" != "$expected" ]]; then
    echo "launcher: stale private configuration layout; recreate with: $2" >&2
    return 1
  fi
}

lockbox_prepare_agent_config() {
  local cid="$1" stage="$2" refresh="$3" python
  case "$refresh" in 0|1) ;; *) echo 'launcher: config refresh must be 0 or 1' >&2; return 1 ;; esac
  python="$(lockbox_host_python)" || return 1
  local flags=()
  case "${LOCKBOX_CONFIG_IMPORT_NATIVE:-0}" in
    0) ;; 1) flags+=(--import-native) ;;
    *) echo 'launcher: LOCKBOX_CONFIG_IMPORT_NATIVE must be 0 or 1' >&2; return 1 ;;
  esac
  [[ "$refresh" == 0 ]] || flags+=(--refresh)
  "$python" "$AGENT_CONFIG_TOOL" locked-import "$AGENT" --stage "$stage" --cid "$cid" \
    --source "${LOCKBOX_CONFIG_SOURCE:-$HOME/.config/lockbox/agents}" ${flags[@]+"${flags[@]}"}
}

lockbox_ensure_agent_login() {
  local user="$1" cid="$2" hint="$3"
  if [[ "$AGENT" == codex ]]; then
    sandbox_ensure_codex_login "$user" "$cid" launcher "$hint"
    return
  fi
  if container exec --user "$user" -e BASH_ENV= "$cid" claude auth status >/dev/null 2>&1; then return; fi
  # Explicit environment auth remains per-process; no Keychain/cache copying.
  if [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then EXEC_ENV+=(-e CLAUDE_CODE_OAUTH_TOKEN); return; fi
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then EXEC_ENV+=(-e ANTHROPIC_API_KEY); return; fi
  if [[ ! -t 0 || ! -t 1 ]]; then
    echo "launcher: Claude is logged out. $hint Or supply explicit environment authentication." >&2
    return 1
  fi
  echo 'launcher: opening native Claude login. Open the displayed URL in your host browser and paste the code if requested.' >&2
  container exec -i -t --user "$user" -e BASH_ENV= "$cid" claude auth login || return 1
  container exec --user "$user" -e BASH_ENV= "$cid" claude auth status >/dev/null 2>&1
}

lockbox_forward_mcp_environment() {
  local names name
  names="$(lockbox_agent_config_run "$1" environment)" || return 1
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    if printenv "$name" >/dev/null; then
      EXEC_ENV+=(-e "$name")
    else
      echo "launcher: MCP environment reference $name is unset; dependent server may be unavailable." >&2
    fi
  done <<< "$names"
}

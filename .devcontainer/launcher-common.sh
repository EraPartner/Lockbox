#!/usr/bin/env bash
# Shared host-side helpers for sandbox launchers. Vendored into each managed
# .devcontainer/ by LockBox/sync.sh and sourced by the launcher as
# "$(dirname "$0")/../launcher-common.sh".
#
# WHY: stage_claude_config(), the Keychain credential block, and the
# stop-on-exit trap were copy-pasted near-verbatim across all launchers and
# had begun to drift. This is the single source; per-launcher specifics
# (git-agent's push token + ssh signing, the charter) stay in the individual
# launchers and run alongside these.
#
# Must stay POSIX-bash-3.2 compatible (macOS /bin/bash). No associative arrays,
# no ${var^^}. Functions use a shared global EXEC_ENV array by design.

# --- Stage a sanitized ~/.claude into the RO bind-mount staging dir -----------
# Usage: sandbox_stage_claude_config <profile>
#   Produces  $HOME/.claude-sandbox/stage/<profile>/dot-claude/   (config tree)
#   and       $HOME/.claude-sandbox/stage/<profile>/claude.json   (.claude.json)
# Copies only the safe, declarative config; rewrites host plugin paths to the
# container path; strips hooks from staged settings. The reviewed command guard,
# post-edit monitor and managed settings are baked into the root-owned image.
sandbox_stage_claude_config() {
  local profile="$1"
  local src dst item jf f items_file item_count
  items_file="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/claude-stage-items.txt"
  if [[ ! -r "$items_file" ]]; then
    echo "sandbox: refusing Claude stage without curated item list: $items_file" >&2
    return 1
  fi
  item_count=0
  while IFS= read -r item || [[ -n "$item" ]]; do
    case "$item" in
      ''|\#*) continue ;;
      *[!A-Za-z0-9._-]*|.|..)
        echo "sandbox: refusing invalid Claude stage item: $item" >&2
        return 1
        ;;
    esac
    item_count=$((item_count + 1))
  done < "$items_file"
  if (( item_count == 0 )); then
    echo "sandbox: refusing empty Claude stage item list: $items_file" >&2
    return 1
  fi
  src="$(cd "$HOME/.claude" 2>/dev/null && pwd -P || true)"
  dst="$HOME/.claude-sandbox/stage/$profile"
  rm -rf "$dst/dot-claude"
  mkdir -p "$dst/dot-claude"
  chmod 0700 "$dst" "$dst/dot-claude"
  if [[ -n "$src" && -d "$src" ]]; then
    while IFS= read -r item || [[ -n "$item" ]]; do
      case "$item" in ''|\#*) continue ;; esac
      [[ -e "$src/$item" ]] || continue
      case "$item" in
        statusline|plugins)
          # These entries can hold multi-megabyte Git clones. Exclude nested
          # repositories at copy time instead of copying and deleting them.
          if ! tar -C "$src" --exclude .git -cf - "$item" \
            | tar -C "$dst/dot-claude" -xf -; then
            echo "sandbox: refusing incomplete Claude stage after archive failure: $item" >&2
            rm -rf "$dst/dot-claude" "$dst/claude.json"
            return 1
          fi
          ;;
        *)
          if ! cp -a "$src/$item" "$dst/dot-claude/"; then
            echo "sandbox: refusing incomplete Claude stage after copy failure: $item" >&2
            rm -rf "$dst/dot-claude" "$dst/claude.json"
            return 1
          fi
          ;;
      esac
    done < "$items_file"
    # Rewrite host plugin paths to the container path. Escape the interpolated
    # values for BRE + the `#` sed delimiter first: a host $HOME/$src containing a
    # sed metacharacter (or a literal `#`) would otherwise produce a malformed
    # `s###` expression, so the substitution silently fails and the container's
    # installed_plugins.json keeps host paths.
    local hpat spat
    hpat="$(printf '%s' "$HOME/.claude" | sed 's/[][\.*^$#/]/\\&/g')"
    spat="$(printf '%s' "$src"          | sed 's/[][\.*^$#/]/\\&/g')"
    for jf in known_marketplaces.json installed_plugins.json; do
      f="$dst/dot-claude/plugins/$jf"
      [[ -f "$f" ]] || continue
      if ! sed -i '' -e "s#$hpat#/home/dev/.claude#g" -e "s#$spat#/home/dev/.claude#g" "$f"; then
        echo "sandbox: refusing Claude stage with unreplaced host plugin paths: $jf" >&2
        rm -rf "$dst/dot-claude" "$dst/claude.json"
        return 1
      fi
    done
    find "$dst/dot-claude" -name '.DS_Store' -delete 2>/dev/null || true
    if [[ -f "$dst/dot-claude/settings.json" ]]; then
      if ! command -v jq >/dev/null 2>&1; then
        echo "sandbox: refusing unsanitized Claude settings because jq is unavailable" >&2
        rm -rf "$dst/dot-claude" "$dst/claude.json"
        return 1
      fi
      local hjt
      hjt="$(mktemp "$dst/.settings.XXXXXX")"
      if ! jq 'del(.hooks)' "$dst/dot-claude/settings.json" >"$hjt"; then
        echo "sandbox: refusing invalid or unsanitized Claude settings" >&2
        rm -f "$hjt"
        rm -rf "$dst/dot-claude" "$dst/claude.json"
        return 1
      fi
      chmod 0600 "$hjt"
      mv "$hjt" "$dst/dot-claude/settings.json"
    fi
  fi
  # Stage .claude.json, stripping the blocks the container must never see:
  #   .oauthAccount — host identity (account email, account/org UUIDs, display
  #                   name, plan tier).
  #   .projects     — a full map of every host project PATH plus that project's
  #                   prior prompt/history text and per-project tool grants. The
  #                   container has no use for the host's project list, and a
  #                   hostile in-container source doc could otherwise read it and
  #                   exfiltrate the host filesystem layout + past prompts over an
  #                   allowlisted host. Claude Code recreates its own .projects
  #                   entry for /workspaces/... in-container as needed.
  #   .installMethod / .autoUpdatesProtectedForNative — the HOST claude is a NATIVE
  #                   install (~/.local/bin/claude); the in-container claude is an
  #                   npm-global install (/usr/local/share/npm-global/bin/claude).
  #                   Leaking installMethod=native makes the container's claude
  #                   check ~/.local/bin/claude and warn "missing or broken" on
  #                   every start (the dir doesn't exist). Stripped so the container
  #                   claude detects its own (npm) install. autoUpdates=false is KEPT
  #                   (we never auto-update a pin-verified sandbox).
  # Mirrors the .hooks strip above. Sanitization is mandatory: absence/failure
  # of jq aborts the launch instead of copying sensitive host state verbatim.
  if [[ -f "$HOME/.claude.json" ]]; then
    if ! command -v jq >/dev/null 2>&1; then
      echo "sandbox: refusing unsanitized ~/.claude.json because jq is unavailable" >&2
      rm -rf "$dst/dot-claude" "$dst/claude.json"
      return 1
    fi
    local cjt
    cjt="$(mktemp "$dst/.claude-json.XXXXXX")"
    if ! jq 'del(.oauthAccount, .projects, .installMethod, .autoUpdatesProtectedForNative)' "$HOME/.claude.json" >"$cjt"; then
      echo "sandbox: refusing invalid or unsanitized ~/.claude.json" >&2
      rm -f "$cjt"
      rm -rf "$dst/dot-claude" "$dst/claude.json"
      return 1
    fi
    chmod 0600 "$cjt"
    mv "$cjt" "$dst/claude.json"
  fi
}

# --- Forward the Claude LLM token into a shared EXEC_ENV array ----------------
# Usage: sandbox_forward_llm_creds <keychain-service>
# Appends NAME-ONLY `-e KEY` flags to the caller's EXEC_ENV array (declare it
# first) and exports the value into the launcher's own environment. `container
# exec -e KEY` (no =VALUE) forwards the value from the launcher's env, so
# the secret never appears in the exec argv — i.e. it is not visible via `ps`/
# /proc/<pid>/cmdline the way `-e KEY=VALUE` would be. Prefers the named macOS
# Keychain item; falls back to the host env vars.
sandbox_forward_llm_creds() {
  local kc_service="$1" tok var
  if command -v security >/dev/null 2>&1; then
    tok="$(security find-generic-password -s "$kc_service" -w 2>/dev/null || true)"
    if [[ -n "$tok" ]]; then
      export CLAUDE_CODE_OAUTH_TOKEN="$tok"
      EXEC_ENV+=(-e CLAUDE_CODE_OAUTH_TOKEN)
      return 0
    fi
  fi
  for var in CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN; do
    [[ -n "${!var:-}" ]] && { export "${var?}"; EXEC_ENV+=(-e "$var"); }
  done
  # Explicit success: the final `[[ -n ... ]] &&` above can leave $? = 1 (e.g. when
  # no keychain token exists and the last env var is unset), which would otherwise
  # abort a `set -e` launcher that calls this as a bare statement.
  return 0
}

# --- Ensure Codex has a private, in-container login --------------------------
# Usage: sandbox_ensure_codex_login <container-user> <container> <label> <hint>
#
# Both in-repo launchers use the same authentication policy: prefer an existing
# cache in the provider's private volume, allow one-shot environment bootstrap,
# then fall back to the official interactive device-code flow. Keeping this here
# prevents the security-sensitive credential plumbing from drifting between the
# LockBox and generic sandboxes.
sandbox_require_host_node() {
  if ! command -v node >/dev/null 2>&1 || ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 18 ? 0 : 1)' >/dev/null 2>&1; then
    echo 'launcher: managed policy requires host Node.js 18 or newer; install it before launching either provider.' >&2
    return 1
  fi
}

sandbox_require_claude_policy() {
  local cid="$1" user="$2" dc="$3" profile="$4" root="$5"
  local hashes watcher verifier installer identity
  hashes="$(shasum -a 256 "$dc/claude-guard.mjs" "$dc/claude-post-edit.mjs" "$dc/claude-explicit-format.sh" | cut -d ' ' -f1)" || return 1
  watcher="$(node "$dc/claude-guard.mjs" --watcher-policy "$profile" "$root" | shasum -a 256 | cut -d ' ' -f1)" || return 1
  verifier="$(shasum -a 256 "$dc/claude-policy-verify.sh" | cut -d ' ' -f1)" || return 1
  installer="$(shasum -a 256 "$dc/claude-policy-install.sh" | cut -d ' ' -f1)" || return 1
  identity="$(printf '%s\n' "$hashes" "$watcher" "$verifier" "$installer" | shasum -a 256 | cut -d ' ' -f1)" || return 1
  # Stream the current trusted verifier instead of executing an image-supplied checker.
  container exec -i --user "$user" -e BASH_ENV= \
    -e PATH=/usr/local/share/npm-global/bin:/opt/python/bin:/usr/local/bin:/usr/bin:/bin \
    "$cid" /bin/bash --noprofile --norc -s -- "$identity" < "$dc/claude-policy-verify.sh"
}

sandbox_ensure_codex_login() {
  local container_user="$1" container_name="$2" label="$3" hint="$4"

  container exec --user "$container_user" "$container_name" codex login status >/dev/null 2>&1 && return 0

  if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    container exec -i --user "$container_user" -e OPENAI_API_KEY "$container_name" \
      bash -c 'printenv OPENAI_API_KEY | codex login --with-api-key'
  elif [[ -n "${CODEX_ACCESS_TOKEN:-}" ]]; then
    container exec -i --user "$container_user" -e CODEX_ACCESS_TOKEN "$container_name" \
      bash -c 'printenv CODEX_ACCESS_TOKEN | codex login --with-access-token'
  elif [[ -t 0 && -t 1 ]]; then
    echo "$label: no cached Codex login; starting ChatGPT device-code login." >&2
    container exec -i -t --user "$container_user" "$container_name" codex login --device-auth
  else
    echo "$label: no cached Codex login. Run interactively once, or set" >&2
    echo "  OPENAI_API_KEY/CODEX_ACCESS_TOKEN. $hint" >&2
    return 1
  fi
}

# --- Verify baked tool pins once per running container boot ------------------
# Usage: sandbox_verify_pins_cached CONTAINER USER VERIFIER PINFILE PIN_ENV TAG
#
# A successful full verification is cached under /run, which is root-owned and
# unavailable to the sandbox user. The record is content-bound to the current
# boot, verifier, pin manifest, service identity, and protocol version. Any
# missing, malformed, stale, or insecure record is a cache miss, never success.
# The full checker still runs as the service user with startup hooks and pinfile
# overrides disabled. Cache publication failure only loses the optimization: the
# current launch was fully verified and may continue.
sandbox_verify_pins_cached() {
  local cname="$1" service_user="$2" verifier="$3" pinfile="$4" pin_env="$5" tag="$6"
  local clean_path sentinel cache_script

  case "$service_user" in ''|*[!A-Za-z0-9_.-]*) return 64 ;; esac
  case "$verifier:$pinfile" in *[!A-Za-z0-9_./:-]*|*:*:*) return 64 ;; esac
  case "$verifier:$pinfile" in /*:/*) ;; *) return 64 ;; esac
  case "$pin_env" in [A-Za-z_]* ) ;; *) return 64 ;; esac
  case "$pin_env" in *[!A-Za-z0-9_]*) return 64 ;; esac
  case "$tag" in ''|*[!A-Za-z0-9_.-]*) return 64 ;; esac

  clean_path='/usr/local/share/npm-global/bin:/opt/python/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin:/home/dev/.npm-global/bin'
  sentinel="/run/lockbox-verify-pins/$tag.ok"
  # The single-quoted program is intentionally expanded only by the container's
  # Bash after its positional arguments have been supplied.
  # shellcheck disable=SC2016
  cache_script='set -eu
mode=$1
sentinel=$2
verifier=$3
pinfile=$4
service_user=$5
clean_path=$6
tag=$7
parent=${sentinel%/*}

secure_node() {
  node=$1
  [ ! -L "$node" ] || return 1
  [ "$(stat -c %u "$node")" = 0 ] || return 1
  perms=$(stat -c %a "$node")
  [ $((0$perms & 022)) -eq 0 ]
}

secure_chain() {
  original=$1
  case "$original" in /*) ;; *) return 1 ;; esac
  resolved=$(readlink -f -- "$original") || return 1
  for start in "$original" "$resolved"; do
    current=$start
    while :; do
      [ "$(stat -c %u -- "$current")" = 0 ] || return 1
      if [ ! -L "$current" ]; then
        perms=$(stat -c %a -- "$current") || return 1
        [ $((0$perms & 022)) -eq 0 ] || return 1
      fi
      [ "$current" = / ] && break
      current=${current%/*}
      [ -n "$current" ] || current=/
    done
  done
}

expected_key() {
  boot_id=$(cat /proc/sys/kernel/random/boot_id)
  verifier_hash=$(sha256sum "$verifier" | awk "{print \$1}")
  pinfile_hash=$(sha256sum "$pinfile" | awk "{print \$1}")
  printf "%s\n" \
    lockbox-verify-pins-cache-v1 "$boot_id" "$verifier" "$verifier_hash" \
    "$pinfile" "$pinfile_hash" "$service_user" "$clean_path" \
    | sha256sum | awk "{print \$1}"
}

case "$mode" in
  probe)
    [ -x "$verifier" ] && [ -r "$pinfile" ] || exit 1
    secure_chain "$verifier" && secure_chain "$pinfile" || exit 1
    secure_node "$parent" || exit 1
    [ -f "$sentinel" ] && [ ! -L "$sentinel" ] || exit 1
    secure_node "$sentinel" || exit 1
    expected=$(expected_key) || exit 1
    actual=$(cat "$sentinel") || exit 1
    [ "$actual" = "$expected" ]
    ;;
  commit)
    [ -x "$verifier" ] && [ -r "$pinfile" ] || exit 1
    secure_chain "$verifier" && secure_chain "$pinfile" || exit 1
    if [ -e "$parent" ]; then
      secure_node "$parent" || exit 1
    else
      install -d -o root -g root -m 0755 "$parent"
    fi
    expected=$(expected_key) || exit 1
    tmp="$parent/.$tag.tmp.$$"
    trap '\''rm -f "$tmp"'\'' EXIT HUP INT TERM
    umask 077
    printf "%s\n" "$expected" > "$tmp"
    chown root:root "$tmp"
    chmod 0444 "$tmp"
    mv -fT "$tmp" "$sentinel"
    trap - EXIT HUP INT TERM
    ;;
  invalidate)
    if [ -d "$parent" ] && [ ! -L "$parent" ]; then
      rm -f "$sentinel"
    fi
    ;;
  *) exit 64 ;;
esac'

  if container exec --user root -e BASH_ENV= "$cname" \
      /usr/bin/env -u "$pin_env" PATH="$clean_path" /bin/bash -c "$cache_script" bash \
      probe "$sentinel" "$verifier" "$pinfile" "$service_user" "$clean_path" "$tag"; then
    return 0
  fi

  if ! container exec --user "$service_user" -e BASH_ENV= "$cname" \
      /usr/bin/env -u "$pin_env" PATH="$clean_path" "$verifier" --quiet; then
    container exec --user root -e BASH_ENV= "$cname" \
      /usr/bin/env -u "$pin_env" PATH="$clean_path" /bin/bash -c "$cache_script" bash \
      invalidate "$sentinel" "$verifier" "$pinfile" "$service_user" "$clean_path" "$tag" \
      >/dev/null 2>&1 || true
    return 1
  fi

  # Old images have a valid full checker but lack the ancestry and manifest
  # validation required for safe reuse. Keep their per-launch full check and do
  # not publish a cache record until the baked verifier declares this protocol.
  local cache_protocol
  cache_protocol="$(container exec --user "$service_user" -e BASH_ENV= "$cname" \
    /usr/bin/env -u "$pin_env" PATH="$clean_path" "$verifier" --print-cache-protocol \
    2>/dev/null || true)"
  if [[ "$cache_protocol" != lockbox-verify-pins-cache-v1 ]]; then
    echo "launcher: WARN — image verifier lacks cache protocol; full verification will repeat until rebuild." >&2
    return 0
  fi

  if ! container exec --user root -e BASH_ENV= "$cname" \
      /usr/bin/env -u "$pin_env" PATH="$clean_path" /bin/bash -c "$cache_script" bash \
      commit "$sentinel" "$verifier" "$pinfile" "$service_user" "$clean_path" "$tag"; then
    echo "launcher: WARN — pin verification passed, but its root-owned cache could not be written." >&2
  fi
}

# --- Install an exit trap: optionally stop the detached VM --------------------
# Usage: sandbox_install_stop_trap <container-id> [stop_on_exit:0|1]
# Runs at most once. Each launcher selects its stop policy; config stays private.
# Detached containers keep their memory allocation until explicitly stopped.
sandbox_install_stop_trap() {
  local cid="$1" stop_on_exit="${2:-0}"
  _SANDBOX_STOP_CID="$cid"
  _SANDBOX_STOP_ON_EXIT="$stop_on_exit"
  _SANDBOX_STOP_DONE=0
  trap _sandbox_stop_on_exit EXIT
}
_sandbox_stop_on_exit() {
  [[ "${_SANDBOX_STOP_DONE:-0}" == 1 ]] && return 0
  _SANDBOX_STOP_DONE=1
  # Stop the detached VM so it stops pinning RAM. PID 1 traps
  # SIGTERM for a graceful shutdown; `container stop` is a no-op if already stopped.
  if [[ "${_SANDBOX_STOP_ON_EXIT:-0}" == 1 && -n "${_SANDBOX_STOP_CID:-}" ]] \
     && command -v container >/dev/null 2>&1; then
    container stop "${_SANDBOX_STOP_CID}" >/dev/null 2>&1 \
      || echo "launcher: 'container stop ${_SANDBOX_STOP_CID}' failed — stop it manually to free RAM." >&2
  fi
}

# --- Warn if a reused/started container's BAKED egress allowlist is stale ------
# Usage: sandbox_warn_stale_allowlist <container> <canonical-allowlist-file> <rebuild-hint>
# For the IMAGE-baked launchers the allowlist is COPY'd into the image, so the
# reuse-running / start-stopped paths keep the OLD baked copy: a sync.sh allowlist
# change (e.g. a NARROWING) silently does not take effect until a rebuild. Compare
# the running container's /etc/squid/allowlist.txt against the freshly-vendored
# canonical file and warn. Never auto-recreates — a rebuild is the operator's call
# The generic sandbox bind-mounts its allowlist and restarts before a changed or
# unconfirmed policy can open a session. This helper is for baked-image launchers.
sandbox_warn_stale_allowlist() {
  local cname="$1" canonical="$2" hint="$3" tmp
  [[ -f "$canonical" ]] || return 0
  tmp="$(mktemp)" || return 0
  if container exec "$cname" cat /etc/squid/allowlist.txt >"$tmp" 2>/dev/null && ! cmp -s "$tmp" "$canonical"; then
    echo "launcher: WARN — the running container's BAKED egress allowlist differs from the" >&2
    echo "  current $canonical. The baked copy is what squid enforces; rebuild to apply it:" >&2
    echo "  $hint" >&2
  fi
  rm -f "$tmp"
}

# --- Read-only workspace policy (Git metadata, hooks, launcher trees) --------
# All helpers remain compatible with the macOS Bash 3.2. They inspect paths;
# they never change Git config/index or execute a repository hook.
sandbox_mount_refuse() {
  echo "launcher: refusing unsafe read-only mount policy — $*" >&2
  return 1
}

# Resolve dot components without following symlinks. Checking each component
# BEFORE consuming '..' prevents symlink/../ aliases from escaping protection.
# Missing trailing components are permitted for the immutable empty overlay.
# Colon and control characters cannot be represented safely in `-v src:dst:ro`.
sandbox_mount_normalize_path() {
  local path="$1" rest part current="" next
  case "$path" in
    /*) ;;
    *) sandbox_mount_refuse "expected an absolute path: $path"; return 1 ;;
  esac
  case "$path" in
    *:*|*[[:cntrl:]]*) sandbox_mount_refuse "path has an unsupported mount delimiter"; return 1 ;;
  esac
  rest="${path#/}"
  while [[ -n "$rest" ]]; do
    part="${rest%%/*}"
    if [[ "$rest" == */* ]]; then rest="${rest#*/}"; else rest=""; fi
    case "$part" in
      ''|.) continue ;;
      ..) current="${current%/*}"; continue ;;
    esac
    next="$current/$part"
    if [[ -L "$next" ]]; then
      sandbox_mount_refuse "symlink component is unsupported: $next"; return 1
    fi
    if [[ -n "$rest" && -e "$next" && ! -d "$next" ]]; then
      sandbox_mount_refuse "non-directory path component: $next"; return 1
    fi
    current="$next"
  done
  printf '%s\n' "${current:-/}"
}

# A directory overlay does not protect symlink targets or another hard-linked
# name in the RW workspace. These layouts are deliberately unsupported. Refuse
# scan errors too; an unreadable subtree is not evidence that it is safe.
sandbox_mount_check_tree() {
  local tree="$1" aliases
  [[ -d "$tree" && ! -L "$tree" ]] || {
    sandbox_mount_refuse "expected a real directory: $tree"; return 1;
  }
  aliases="$(find "$tree" \( -type l -o \( -type f -links +1 \) \) -print)" || {
    sandbox_mount_refuse "cannot inspect protected directory: $tree"; return 1;
  }
  [[ -z "$aliases" ]] || {
    sandbox_mount_refuse "symlink or hard-linked file inside protected directory: $tree"; return 1;
  }
}

sandbox_mount_empty_source() {
  local repo="$1" empty entries
  empty="$(sandbox_mount_normalize_path "$HOME/.claude-sandbox/empty-hooks")" || return 1
  case "$empty" in
    "$repo"|"$repo"/*) sandbox_mount_refuse "empty overlay source is inside the writable workspace"; return 1 ;;
  esac
  mkdir -p "$empty" || {
    sandbox_mount_refuse "cannot prepare immutable empty directory: $empty"; return 1;
  }
  sandbox_mount_check_tree "$empty" || return 1
  entries="$(find "$empty" -mindepth 1 -print)" || return 1
  [[ -z "$entries" ]] || {
    sandbox_mount_refuse "empty overlay contains files: $empty"; return 1;
  }
  printf '%s\n' "$empty"
}

# Append one in-workspace directory to the selected array. Paths are normalized
# host paths; caller supplies a canonical repo and container workspace root.
sandbox_mount_directory() {
  local repo="$1" ws="$2" path="$3" kind="$4" src rel
  [[ "$path" != "$repo" ]] || {
    sandbox_mount_refuse "protecting the workspace root would disable editing: $repo"; return 1;
  }
  case "$path" in "$repo"/*) ;; *) return 0 ;; esac
  rel="${path#"$repo"/}"
  if [[ -e "$path" ]]; then
    sandbox_mount_check_tree "$path" || return 1
    src="$path"
  else
    src="$(sandbox_mount_empty_source "$repo")" || return 1
  fi
  case "$kind" in
    git) GIT_RO_MOUNTS+=(-v "$src:$ws/$rel:ro") ;;
    devcontainer) DEVCONTAINER_RO_MOUNTS+=(-v "$src:$ws/$rel:ro") ;;
    *) sandbox_mount_refuse "unknown mount policy kind"; return 1 ;;
  esac
}

# Git config includes are executable policy too: a writable included file could
# relocate hooks after preflight. Git enumerates includes with their origins and
# expands home-directory syntax; relative values use the containing config dir.
# Refuse in-workspace includes outside the protected metadata tree, even when
# currently missing or conditionally inactive. A later file creation or changed
# branch must not make that policy writable. No config file is modified here.
sandbox_mount_check_git_includes() {
  local repo="$1" tmp status origin entry value config path count=0
  tmp="$(mktemp "${TMPDIR:-/tmp}/lockbox-git-includes.XXXXXX")" || {
    sandbox_mount_refuse "cannot prepare Git include inspection"; return 1;
  }
  if git -C "$repo" config --path --null --includes --show-origin \
      --get-regexp '^include(if\..*)?\.path$' >"$tmp"; then status=0; else status=$?; fi
  if [[ "$status" == 1 && ! -s "$tmp" ]]; then rm -f "$tmp"; return 0; fi
  if [[ "$status" != 0 ]]; then
    rm -f "$tmp"
    sandbox_mount_refuse "Git could not enumerate config includes"; return 1
  fi
  # Failure branches unlink the owned temporary file and immediately return;
  # they do not overwrite any input that the loop will continue reading.
  # shellcheck disable=SC2094
  while IFS= read -r -d '' origin; do
    if ! IFS= read -r -d '' entry || [[ "$entry" != *$'\n'* ]]; then
      rm -f "$tmp"
      sandbox_mount_refuse "malformed Git include origin records"; return 1
    fi
    case "$origin" in
      file:*) config="${origin#file:}" ;;
      *) rm -f "$tmp"; sandbox_mount_refuse "unsupported Git include origin"; return 1 ;;
    esac
    case "$config" in /*) ;; *) config="$repo/$config" ;; esac
    config="$(sandbox_mount_normalize_path "$config")" || { rm -f "$tmp"; return 1; }
    value="${entry#*$'\n'}"
    case "$value" in /*) path="$value" ;; *) path="${config%/*}/$value" ;; esac
    path="$(sandbox_mount_normalize_path "$path")" || { rm -f "$tmp"; return 1; }
    case "$path" in
      "$repo/.git/"*) ;;
      "$repo"|"$repo"/*)
        rm -f "$tmp"
        sandbox_mount_refuse "Git config include is in the writable workspace: $path"; return 1 ;;
    esac
    count=$((count + 1))
  done <"$tmp"
  rm -f "$tmp"
  [[ "$count" != 0 && -z "$origin" ]] || {
    sandbox_mount_refuse "empty or incomplete Git include origin records"; return 1;
  }
}

# A broad non-Git target can contain another clone's executable Git policy.
# Support only one Git boundary: the selected root. Discover every launcher
# tree without following directory symlinks, and keep filenames NUL-delimited.
# Root metadata is pruned; nested metadata (directories, files or symlinks) is
# refused rather than accidentally exposed through the writable parent mount.
sandbox_mount_discover_workspace() {
  local repo="$1" tmp path normalized i j key LC_ALL=C
  SANDBOX_DEVCONTAINER_PATHS=()
  tmp="$(mktemp "${TMPDIR:-/tmp}/lockbox-workspace-mounts.XXXXXX")" || {
    sandbox_mount_refuse "cannot prepare workspace boundary inspection"; return 1;
  }
  if ! find "$repo" -name .git -print0 -prune -o -name .devcontainer -print0 >"$tmp"; then
    rm -f "$tmp"
    sandbox_mount_refuse "cannot inspect all workspace Git and launcher boundaries"; return 1
  fi
  # Unlinking the owned input on a failure is safe: the function then returns.
  # shellcheck disable=SC2094
  while IFS= read -r -d '' path; do
    if [[ "${path##*/}" == .git ]]; then
      [[ "$path" == "$repo/.git" ]] && continue
      rm -f "$tmp"
      sandbox_mount_refuse "nested Git boundary is unsupported: $path"; return 1
    fi
    normalized="$(sandbox_mount_normalize_path "$path")" || { rm -f "$tmp"; return 1; }
    SANDBOX_DEVCONTAINER_PATHS+=("$normalized")
  done <"$tmp"
  rm -f "$tmp"
  [[ -z "$path" ]] || {
    sandbox_mount_refuse "incomplete workspace boundary records"; return 1;
  }
  # BSD sort lacks portable NUL sorting. Launcher trees are few, so an indexed
  # array insertion sort provides deterministic ordering on Bash 3.2 as well.
  for ((i=1; i<${#SANDBOX_DEVCONTAINER_PATHS[@]}; i++)); do
    key="${SANDBOX_DEVCONTAINER_PATHS[$i]}"
    j=$((i - 1))
    while (( j >= 0 )) && [[ "${SANDBOX_DEVCONTAINER_PATHS[$j]}" > "$key" ]]; do
      SANDBOX_DEVCONTAINER_PATHS[j + 1]="${SANDBOX_DEVCONTAINER_PATHS[$j]}"
      j=$((j - 1))
    done
    SANDBOX_DEVCONTAINER_PATHS[j + 1]="$key"
  done
}

# Populate GIT_RO_MOUNTS. Git's effective path expands '~', honors config
# includes, and distinguishes unset hooksPath from an explicitly empty value.
# External paths (including /dev/null) are outside the writable workspace;
# absolute/traversal aliases INSIDE it receive the same overlay as .githooks.
sandbox_git_ro_mounts() {
  local repo="$1" ws="$2" hp path
  GIT_RO_MOUNTS=()
  repo="$(sandbox_mount_normalize_path "$repo")" || return 1
  ws="$(sandbox_mount_normalize_path "$ws")" || return 1
  sandbox_mount_discover_workspace "$repo" || return 1
  [[ -e "$repo/.git" || -L "$repo/.git" ]] || return 0
  [[ -d "$repo/.git" && ! -L "$repo/.git" && ! -e "$repo/.git/commondir" && ! -L "$repo/.git/commondir" ]] || {
    sandbox_mount_refuse "use a standard clone with a real .git directory (no worktree, symlink, or commondir)"; return 1;
  }
  sandbox_mount_directory "$repo" "$ws" "$repo/.git" git || return 1
  sandbox_mount_check_git_includes "$repo" || return 1
  # Preserve trailing newlines in the actual pathname so delimiter validation
  # cannot accidentally protect a different directory after shell trimming.
  hp="$(git -C "$repo" rev-parse --git-path hooks && printf '\001')" || {
    sandbox_mount_refuse "Git could not resolve its effective hooks directory"; return 1;
  }
  hp="${hp%$'\001'}"
  hp="${hp%$'\n'}"
  [[ -n "$hp" ]] || { sandbox_mount_refuse "Git returned an empty hooks path"; return 1; }
  case "$hp" in /*) path="$hp" ;; *) path="$repo/$hp" ;; esac
  path="$(sandbox_mount_normalize_path "$path")" || return 1
  case "$path" in "$repo/.git"|"$repo/.git/"*) return 0 ;; esac
  sandbox_mount_directory "$repo" "$ws" "$path" git
}

# Protect every present launcher tree, including arbitrary nested directories.
# Absent trees remain absent; unrelated project directories are not created.
sandbox_devcontainer_ro_mounts() {
  local repo="$1" ws="$2" path
  DEVCONTAINER_RO_MOUNTS=()
  repo="$(sandbox_mount_normalize_path "$repo")" || return 1
  ws="$(sandbox_mount_normalize_path "$ws")" || return 1
  sandbox_mount_discover_workspace "$repo" || return 1
  for path in ${SANDBOX_DEVCONTAINER_PATHS[@]+"${SANDBOX_DEVCONTAINER_PATHS[@]}"}; do
    sandbox_mount_directory "$repo" "$ws" "$path" devcontainer || return 1
  done
}

# Creation/reuse gates bind their saved identity to the FULL ordered RO plan.
# NUL separators prevent ambiguous serialization of paths containing spaces.
# Version this prefix when supported-layout or mount semantics change.
sandbox_ro_mount_policy_id() {
  local digest
  digest="$(set -o pipefail
    printf '%s\0' lockbox-ro-mounts-v2 \
      ${GIT_RO_MOUNTS[@]+"${GIT_RO_MOUNTS[@]}"} \
      ${DEVCONTAINER_RO_MOUNTS[@]+"${DEVCONTAINER_RO_MOUNTS[@]}"} | shasum -a 256
  )" || { sandbox_mount_refuse "cannot hash the read-only mount policy"; return 1; }
  digest="${digest%% *}"
  [[ ${#digest} == 64 && "$digest" != *[!0-9a-f]* ]] || {
    sandbox_mount_refuse "invalid read-only mount policy digest"; return 1;
  }
  # Consumed by the launcher's creation/reuse gate.
  # shellcheck disable=SC2034
  RO_MOUNT_POLICY_ID="$digest"
}

# --- Bind launch policy to the inspected image descriptor --------------------
# Hash canonical JSON, so formatting/key order changes cannot manufacture drift.
# No fallback to the image tag: tags are mutable and cannot prove fresh policy.
sandbox_image_policy_id() {
  local image="$1" descriptor digest
  descriptor="$(container image inspect "$image")" || {
    echo "launcher: cannot inspect image '$image'; refusing stale-image reuse." >&2
    return 1
  }
  # Apple Container 1.0.0 ImageResource JSON exposes the index digest here.
  # shellcheck disable=SC2034
  IMAGE_DIGEST="$(printf '%s\n' "$descriptor" | jq -esr '
    if length == 1 and (.[0] | type == "array" and length == 1)
    then .[0][0].configuration.descriptor.digest else empty end |
    select(type == "string" and test("^sha256:[0-9a-f]{64}$"))')" || {
    echo "launcher: image descriptor has no unique OCI index digest; refusing reuse." >&2
    return 1
  }
  digest="$(set -o pipefail
    printf '%s\n' "$descriptor" |
      jq -ceS 'select(type == "array" or type == "object") | select(length > 0) | select(any(.. | strings; test("^sha256:[0-9a-f]{64}$")))' |
      shasum -a 256
  )" || {
    echo "launcher: invalid image descriptor; refusing stale-image reuse." >&2
    return 1
  }
  digest="${digest%% *}"
  [[ ${#digest} == 64 && "$digest" != *[!0-9a-f]* ]] || return 1
  # Consumed by creation-time SANDBOX_LAUNCH_POLICY and the reuse gate.
  # shellcheck disable=SC2034
  IMAGE_POLICY_ID="$digest"
}

# Validate creation-time policy without executing inside the container. This must
# run before starting a stopped VM: legacy images may have unsafe startup hooks.
# Schema: Apple Container 1.0.0 ContainerConfiguration and ProcessConfiguration.
sandbox_require_container_policy() {
  local name="$1" agent="$2" policy="$3" metadata
  [[ "${IMAGE_DIGEST:-}" =~ ^sha256:[0-9a-f]{64}$ && -n "$agent" && -n "$policy" ]] || {
    echo "launcher: missing expected image/provider/policy identity." >&2; return 1;
  }
  metadata="$(container inspect "$name")" || {
    echo "launcher: cannot inspect container '$name'; refusing reuse." >&2
    return 1
  }
  if ! printf '%s\n' "$metadata" | jq -es \
      --arg name "$name" --arg agent "$agent" --arg policy "$policy" \
      --arg digest "${IMAGE_DIGEST:-}" '
    length == 1 and (.[0] |
    type == "array" and length == 1 and
    .[0].configuration.id == $name and
    .[0].configuration.image.descriptor.digest == $digest and
    (.[0].configuration.initProcess.environment |
      type == "array" and all(.[]; type == "string") and
      ([.[] | select(startswith("SANDBOX_AGENT="))] == ["SANDBOX_AGENT=" + $agent]) and
      ([.[] | select(startswith("SANDBOX_LAUNCH_POLICY="))] == ["SANDBOX_LAUNCH_POLICY=" + $policy])))
    ' >/dev/null 2>&1; then
    echo "launcher: stale or malformed container provider/image/mount policy; recreate '$name'." >&2
    return 1
  fi
}

# ─── vendored by LockBox v0.1.0 · canonical sha256:92b4408c19cc27f9a12a8486e33adc985663dfbb9be82511b71094a1a30ac000 ───
# Generated from the canonical source by LockBox/sync.sh — DO NOT EDIT HERE.
# Edit LockBox/launcher-common.sh and re-run ./sync.sh.

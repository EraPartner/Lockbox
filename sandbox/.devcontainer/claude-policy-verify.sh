#!/usr/bin/env bash
# Executed from the reviewed host source over stdin; never trust an image's checker.
set -euo pipefail
policy_root=/
if [[ "${1:-}" == --root && $# -ge 2 ]]; then policy_root="$2"; shift 2; fi
[[ $# == 1 && "$1" =~ ^[0-9a-f]{64}$ ]] || { echo 'invalid expected Claude bundle identity' >&2; exit 2; }
files=(
  usr/local/share/claude-guard/guard.mjs
  usr/local/share/claude-guard/hooks/post-edit.mjs
  usr/local/share/claude-guard/managed/explicit-format
  usr/local/share/claude-guard/hooks/watched-projects.json
  usr/local/share/claude-guard/verify-policy.sh
  usr/local/share/claude-guard/install-policy.sh
)
manifest=usr/local/share/claude-guard/policy.sha256
settings=etc/claude-code/managed-settings.json
codex=etc/codex/requirements.toml
for file in "${files[@]}" "$settings" "$codex" "$manifest"; do
  [[ -f "$policy_root/$file" && ! -L "$policy_root/$file" ]] || { echo 'Claude policy file missing or symlinked; rebuild image' >&2; exit 1; }
  if [[ "$policy_root" == / ]]; then
    current="/$file"
    while :; do
      [[ ! -L "$current" && "$(stat -c %u "$current")" == 0 ]] || { echo 'Claude policy is not root-owned' >&2; exit 1; }
      mode="$(stat -c %a "$current")"
      (( (8#$mode & 8#22) == 0 )) || { echo 'Claude policy is writable outside root' >&2; exit 1; }
      [[ "$current" != / ]] || break
      current="${current%/*}"; [[ -n "$current" ]] || current=/
    done
  fi
done
# Fixed paths prevent a forged checksum manifest from directing reads elsewhere.
actual="$(cd "$policy_root" && sha256sum "${files[@]}" "$settings" "$codex")"
[[ "$actual" == "$(cat "$policy_root/$manifest")" ]] || { echo 'Claude policy checksum drift; rebuild image' >&2; exit 1; }
identity="$(cd "$policy_root" && sha256sum "${files[@]}" | cut -d ' ' -f1 | sha256sum | cut -d ' ' -f1)"
[[ "$identity" == "$1" ]] || { echo 'Claude policy differs from reviewed source; rebuild image and recreate container' >&2; exit 1; }
# Settings are generated, too. Matching a manifest alone is insufficient.
node "$policy_root/${files[0]}" --managed-settings | cmp - "$policy_root/$settings"
node "$policy_root/${files[0]}" --codex-requirements | cmp - "$policy_root/$codex"

# ─── vendored by LockBox v0.1.0 · canonical sha256:c5d1e0b5274a66927e5bdb0e1ab9851461aac5f9e6ca279c6616e39b4068229c ───
# Generated from the canonical source by LockBox/sync.sh — DO NOT EDIT HERE.
# Edit LockBox/claude-policy-verify.sh and re-run ./sync.sh.

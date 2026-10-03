#!/usr/bin/env bash
# Image-build helper only. Never run this on the host.
set -euo pipefail
[[ "$EUID" == 0 && $# == 2 && -f /etc/debian_version ]] || { echo 'Claude policy installation requires a root image build and project/root arguments' >&2; exit 2; }
policy=/usr/local/share/claude-guard
mkdir -p /etc/claude-code /etc/codex
node "$policy/guard.mjs" --watcher-policy "$1" "$2" > "$policy/hooks/watched-projects.json"
node "$policy/guard.mjs" --managed-settings > /etc/claude-code/managed-settings.json
node "$policy/guard.mjs" --codex-requirements > /etc/codex/requirements.toml
chown -R root:root "$policy" /etc/claude-code /etc/codex
chmod -R go-w "$policy" /etc/claude-code /etc/codex
chmod 0644 /etc/claude-code/managed-settings.json /etc/codex/requirements.toml
chmod 0755 "$policy/managed/explicit-format"
cd /
sha256sum usr/local/share/claude-guard/guard.mjs \
  usr/local/share/claude-guard/hooks/post-edit.mjs \
  usr/local/share/claude-guard/managed/explicit-format \
  usr/local/share/claude-guard/hooks/watched-projects.json \
  usr/local/share/claude-guard/verify-policy.sh \
  usr/local/share/claude-guard/install-policy.sh \
  etc/claude-code/managed-settings.json etc/codex/requirements.toml > "$policy/policy.sha256"

# ─── vendored by LockBox v0.1.0 · canonical sha256:160cbf9ee96cadea83038b2cb268f3b78a755466b0408a05e603f19c184c2ff3 ───
# Generated from the canonical source by LockBox/sync.sh — DO NOT EDIT HERE.
# Edit LockBox/claude-policy-install.sh and re-run ./sync.sh.

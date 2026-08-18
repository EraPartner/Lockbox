#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
codex_home="${CODEX_HOME:-$HOME/.codex}"

install -d "$codex_home"
install -m 0644 "$script_dir/AGENTS.md" "$codex_home/AGENTS.md"

touch "$HOME/.bashrc"
grep -Fqx 'export CODEX_SESSION_ENV=cloud' "$HOME/.bashrc" || \
  printf '%s\n' 'export CODEX_SESSION_ENV=cloud' >> "$HOME/.bashrc"

printf '%s\n' 'LockBox cloud setup complete. Use Codex Open pull request for Git handoff.'
printf '%s\n' 'Host-only signing, push, and security checks remain unavailable.'

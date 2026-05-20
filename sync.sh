#!/usr/bin/env bash
# Vendoring sync: copy the canonical egress files into each devcontainer's
# .devcontainer/. Edit init-firewall.sh / squid.conf HERE, run this, then rebuild
# the affected containers. The allowlist.txt and (optional) inbound-ports stay
# per-project — they are NOT synced.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"

TARGETS=(
  "/Users/computer/Library/Mobile Documents/iCloud~md~obsidian/Documents/Brain/.devcontainer"
  "/Users/computer/Documents/Personal/Scripts/Projects/Vision/.devcontainer"
  "/Users/computer/Documents/Personal/Scripts/Projects/Watchman/.devcontainer"
  "/Users/computer/Documents/Personal/Scripts/Projects/git-agent/.devcontainer"
)

for dst in "${TARGETS[@]}"; do
  if [[ -d "$dst" ]]; then
    cp "$HERE/init-firewall.sh" "$dst/init-firewall.sh"
    cp "$HERE/squid.conf"       "$dst/squid.conf"
    chmod +x "$dst/init-firewall.sh"
    echo "synced -> $dst"
  else
    echo "SKIP (missing) -> $dst" >&2
  fi
done
echo "Done. Rebuild affected containers: devcontainer up --remove-existing-container ..."

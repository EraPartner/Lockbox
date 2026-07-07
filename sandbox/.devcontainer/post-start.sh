#!/usr/bin/env bash
# Baked at /usr/local/share/dev-sandbox/post-start.sh. Runs every start, as `dev`,
# AFTER the root entrypoint did perms repair + egress lock + proxy. Refreshes the
# Claude config and verifies the egress lock.

set -euo pipefail

STAGE=/home/dev/.claude-stage

# Auto-pull the sanitized host Claude config into the container on every start.
# ASYMMETRIC BY DESIGN — the two config surfaces merge in OPPOSITE directions:
#   - the dot-claude tree (settings, agents, commands, …) is HOST-newer-wins
#     (rsync --update): host edits propagate in, but a file the container touched
#     more recently is kept.
#   - .claude.json (below) is CONTAINER-authoritative (`.[1] * .[0]`, container
#     overriding the staged host copy). This deliberately preserves in-container
#     session / per-project state across a warm restart instead of clobbering it
#     with the static staged snapshot each start. Consequence: host .claude.json
#     edits do NOT propagate to an already-created container (rebuild to re-seed);
#     the staged copy only fills in keys the container doesn't already have.
if [[ -d "$STAGE/dot-claude" && -d /home/dev/.claude ]]; then
  rsync -a --update --ignore-errors "$STAGE/dot-claude/" /home/dev/.claude/ 2>/dev/null || true
fi
if [[ -f "$STAGE/claude.json" && -f /home/dev/.claude.json ]]; then
  tmp=$(mktemp)
  if jq -s '.[1] * .[0] | del(.installMethod, .autoUpdatesProtectedForNative)' /home/dev/.claude.json "$STAGE/claude.json" > "$tmp" 2>/dev/null && [[ -s "$tmp" ]]; then
    mv "$tmp" /home/dev/.claude.json
  else
    rm -f "$tmp"
  fi
fi
# Background-task state isn't meaningful here; keep the volume converged.
for p in scheduled-tasks tasks jobs daemon; do rm -rf "/home/dev/.claude/$p" 2>/dev/null || true; done

# Fail the lifecycle if the egress firewall didn't verify (fail-closed regardless,
# but surface it loudly).
if [[ ! -f /run/egress-firewall-ok ]]; then
  cat >&2 <<'EOF'
[post-start] ✖✖ EGRESS FIREWALL NOT VERIFIED (/run/egress-firewall-ok missing).
[post-start]     Check `container logs` for the [firewall] error, then restart.
EOF
  exit 1
fi

echo "[post-start] Ready. Run \`claude\` in /workspaces/project. Health: \`dev-sandbox-doctor\`."

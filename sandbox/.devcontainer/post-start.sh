#!/usr/bin/env bash
# Baked at /usr/local/share/dev-sandbox/post-start.sh. Runs every start, as `dev`,
# AFTER the root entrypoint did perms repair + egress lock + proxy. Verifies the
# egress lock without merging host configuration.

set -euo pipefail

AGENT="${SANDBOX_AGENT:-}"
case "$AGENT" in
  claude|codex) ;;
  *)
    echo "[post-start] ABORT: SANDBOX_AGENT is '${AGENT:-unset}'; recreate with DEV_SANDBOX_REBUILD=1." >&2
    exit 1
    ;;
esac
# Configuration import belongs to the launcher, after lifecycle readiness.
# Ordinary starts reuse private state; refresh requires an explicit option.

# Fail the lifecycle if the egress firewall didn't verify (fail-closed regardless,
# but surface it loudly).
if [[ ! -f /run/egress-firewall-ok ]]; then
  cat >&2 <<'EOF'
[post-start] ✖✖ EGRESS FIREWALL NOT VERIFIED (/run/egress-firewall-ok missing).
[post-start]     Check `container logs` for the [firewall] error, then restart.
EOF
  exit 1
fi

echo "[post-start] Ready. Run \`$AGENT\` in /workspaces/project. Health: \`dev-sandbox-doctor\`."

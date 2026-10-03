#!/usr/bin/env bash
# Runs every time the container starts, as the `dev` user, AFTER the root
# ENTRYPOINT has already done perms repair + started the egress proxy + applied
# the firewall. This hook seeds project memory
# and verifies the egress lock. No sudo (no-new-privileges).

set -euo pipefail

AGENT="${SANDBOX_AGENT:-}"
case "$AGENT" in
  claude|codex) ;;
  *)
    echo "[post-start] ABORT: SANDBOX_AGENT is '${AGENT:-unset}'; recreate with the selected provider launcher." >&2
    exit 1
    ;;
esac
# Configuration import belongs to the launcher, after lifecycle readiness.
# Ordinary starts reuse private state; refresh requires an explicit option.

# Refuse to proceed if the egress firewall didn't verify. The entrypoint writes
# this sentinel only after confirming default-deny is active; its absence means
# the lock may be open. Egress is fail-closed regardless (init-firewall.sh sets
# DROP first), but surface it loudly and fail the lifecycle so it's not missed.
if [[ ! -f /run/egress-firewall-ok ]]; then
  cat >&2 <<'EOF'
[post-start] ✖✖ EGRESS FIREWALL NOT VERIFIED (/run/egress-firewall-ok missing).
[post-start]     The egress lock did not confirm. Check `container logs` for the
[post-start]     [firewall] error, then restart the container. Do not bypass
[post-start]     agent permissions until this is resolved.
EOF
  exit 1
fi

echo "[post-start] Ready for $AGENT. Edit LockBox; lint with shellcheck, then ./sync.sh --check."

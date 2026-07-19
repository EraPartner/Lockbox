#!/usr/bin/env bash
# Functional egress smoke test — boots the sandbox image and proves the egress lock
# actually ENFORCES, not just that the config lints. Needs a privileged container
# runtime (NET_ADMIN for iptables), so it is NOT part of the self-contained lint CI
# jobs; run it via `make test` locally (Apple `container`) or the CI `egress-test`
# job (docker).
#
#   RUNTIME=container|docker   runtime to use (default: container)
#   IMAGE=<tag>                image tag (default: dev-sandbox:egress-test)
#   SKIP_BUILD=1               reuse an existing IMAGE instead of building
#   KEEP=1                     leave the container running afterwards for inspection
#
# HARD assertions (exit non-zero on failure) are all internet-INDEPENDENT — squid
# blocks/denies them locally — so the gate is deterministic even on a runner with no
# egress: firewall sentinel up, squid up, an off-allowlist host is blocked, and a
# non-CONNECT cleartext request is refused (the squid.conf CONNECT-only guarantee).
# The on-allowlist REACHABILITY check needs real internet, so it is a SOFT warning
# and never fails the run.
set -uo pipefail

RT="${RUNTIME:-container}"
IMAGE="${IMAGE:-dev-sandbox:egress-test}"
NAME="egress-smoke-$$"
HERE="$(cd "$(dirname "$0")" && pwd -P)"
# Normalise: apple/container rejects a build context path containing ".." with
#   Error: <repo>/sandbox is not a child of <repo>/test/../sandbox/.devcontainer
# and never starts the build. docker tolerates it, so this only bit the default
# RUNTIME=container path.
DC="$(cd "$HERE/../sandbox/.devcontainer" && pwd -P)"
PROXY="http://127.0.0.1:3128"

command -v "$RT" >/dev/null 2>&1 || { echo "egress-test: runtime '$RT' not found on PATH" >&2; exit 2; }

pass=0; fail=0; warn=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; pass=$((pass+1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; fail=$((fail+1)); }
note() { printf '  \033[33m!\033[0m %s\n' "$*"; warn=$((warn+1)); }

cleanup() { [[ -n "${KEEP:-}" ]] || "$RT" rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cx() { "$RT" exec "$NAME" "$@"; }

if [[ -z "${SKIP_BUILD:-}" ]]; then
  echo "== build ($RT $IMAGE) =="
  "$RT" build -t "$IMAGE" "$DC" >/dev/null || { echo "egress-test: build FAILED" >&2; exit 1; }
fi

echo "== run =="
"$RT" rm -f "$NAME" >/dev/null 2>&1 || true
"$RT" run -d --name "$NAME" \
  --init --cap-drop ALL \
  --cap-add NET_ADMIN --cap-add CHOWN --cap-add DAC_OVERRIDE \
  --cap-add FOWNER --cap-add SETUID --cap-add SETGID \
  -m 2g --tmpfs /tmp --tmpfs /var/tmp \
  "$IMAGE" >/dev/null || { echo "egress-test: run FAILED" >&2; exit 1; }

echo "== assertions =="

# 1) firewall sentinel — proves init-firewall.sh applied AND its -C verification
#    conjunction matched (fail-closed: no sentinel => the lock is incomplete).
sentinel=0
for _ in $(seq 1 30); do cx test -f /run/egress-firewall-ok 2>/dev/null && { sentinel=1; break; }; sleep 1; done
(( sentinel )) && ok "firewall sentinel present (egress lock verified)" || bad "firewall sentinel MISSING (lock not verified)"

# 2) squid up
squid=0
for _ in $(seq 1 20); do cx sh -c 'pgrep -x squid >/dev/null 2>&1' && { squid=1; break; }; sleep 1; done
(( squid )) && ok "squid proxy running" || bad "squid proxy NOT running"

# 3) off-allowlist host BLOCKED (no internet needed — squid terminates on SNI).
if cx sh -c "curl -sS -x $PROXY --max-time 12 -o /dev/null https://example.com/ 2>/dev/null"; then
  bad "off-allowlist host example.com was REACHABLE — allowlist NOT enforced"
else
  ok "off-allowlist host blocked (example.com)"
fi

# 4) non-CONNECT cleartext REFUSED — the squid.conf CONNECT-only guarantee. A
#    `http://host:443/` request is absolute-URI/non-CONNECT: it clears deny !Safe_ports
#    (443) then hits `deny !CONNECT` => squid 403. No internet needed (squid denies
#    before egress). If this regressed to `allow allowed_dom`, squid would forward
#    cleartext and NOT return 403.
ccode="$(cx sh -c "curl -sS -x $PROXY --max-time 12 -o /dev/null -w '%{http_code}' http://api.anthropic.com:443/ 2>/dev/null" || true)"
if [[ "$ccode" == "403" ]]; then
  ok "non-CONNECT cleartext refused by squid (HTTP 403)"
else
  bad "non-CONNECT cleartext NOT refused (got '${ccode:-none}', expected 403 — CONNECT-only regressed?)"
fi

# 5) on-allowlist host REACHABLE (SOFT — needs real egress; a restricted runner
#    should not fail the security gate on this).
code="$(cx sh -c "curl -sS -x $PROXY --max-time 15 -o /dev/null -w '%{http_code}' https://api.anthropic.com/ 2>/dev/null" || true)"
if [[ "$code" =~ ^[1-5][0-9][0-9]$ ]]; then
  ok "on-allowlist host reachable via proxy (api.anthropic.com HTTP $code)"
else
  note "on-allowlist host not reachable (curl status '${code:-none}') — expected if the runner has no egress"
fi

echo
echo "egress-test: $pass passed, $fail failed, $warn warn"
(( fail == 0 ))

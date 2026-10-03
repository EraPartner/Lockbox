#!/usr/bin/env bash
# Build and boot either image, then exercise both providers as dev. Hard gates
# use disposable mounts, local TLS controls and firewall counters; no host auth
# or internet is required. Run on the host, never inside an editing sandbox.
# RUNTIME=container|docker (default container), IMAGE_KIND=generic|lockbox,
# IMAGE=<tag>, BUILD_CONTEXT=<directory>, SKIP_BUILD=1, KEEP=1.
# CHECK_INTERNET=1 also runs optional, non-gating endpoint reachability probes.
set -uo pipefail

RT="${RUNTIME:-container}"
KIND="${IMAGE_KIND:-generic}"
HERE="$(cd "$(dirname "$0")" && pwd -P)"
ROOT="$(cd "$HERE/.." && pwd -P)"
case "$RT" in container|docker) ;; *) echo "egress-test: unsupported runtime '$RT'" >&2; exit 2 ;; esac
case "$KIND" in
  generic)
    DEFAULT_CONTEXT="$ROOT/sandbox/.devcontainer"
    WORKSPACE=/workspaces/project
    VERIFY=/usr/local/bin/dev-sandbox-verify-pins
    POST_CREATE=/usr/local/share/dev-sandbox/post-create.sh
    POST_START=/usr/local/share/dev-sandbox/post-start.sh
    ;;
  lockbox)
    DEFAULT_CONTEXT="$ROOT/.devcontainer"
    WORKSPACE=/workspaces/LockBox
    VERIFY=/usr/local/bin/lockbox-verify-pins
    POST_CREATE="$WORKSPACE/.devcontainer/post-create.sh"
    POST_START="$WORKSPACE/.devcontainer/post-start.sh"
    ;;
  *) echo "egress-test: unsupported IMAGE_KIND '$KIND'" >&2; exit 2 ;;
esac
IMAGE="${IMAGE:-lockbox-$KIND:egress-test}"
# Apple Container requires a normalized context without '..'.
DC="$(cd "${BUILD_CONTEXT:-$DEFAULT_CONTEXT}" && pwd -P)" || exit 2
command -v "$RT" >/dev/null 2>&1 || { echo "egress-test: runtime '$RT' not found" >&2; exit 2; }

pass=0; fail=0; warn=0
ok() { printf '  PASS: %s\n' "$*"; pass=$((pass+1)); }
bad() { printf '  FAIL: %s\n' "$*"; fail=$((fail+1)); }
note() { printf '  WARN: %s\n' "$*"; warn=$((warn+1)); }
NAMES=()
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/lockbox-runtime.XXXXXX")" || exit 2
FIXTURE="$(cd "$FIXTURE" && pwd -P)" || exit 2
cleanup() {
  if [[ -n "${KEEP:-}" ]]; then
    echo "egress-test: retained disposable mounts at $FIXTURE"
  else
    if (( ${#NAMES[@]} > 0 )); then
      for name in "${NAMES[@]}"; do "$RT" rm -f "$name" >/dev/null 2>&1 || true; done
    fi
    rm -rf "$FIXTURE"
  fi
}
trap cleanup EXIT
cx() { "$RT" exec "$NAME" "$@"; }
cxdev() { "$RT" exec --user dev -e BASH_ENV= -e HOME=/home/dev "$NAME" "$@"; }

if [[ -z "${SKIP_BUILD:-}" ]]; then
  echo "== build ($RT $KIND $IMAGE) =="
  "$RT" build -t "$IMAGE" "$DC" >/dev/null || { echo "egress-test: build FAILED" >&2; exit 1; }
fi

# A synthetic standard clone lets the real launcher mount planner run without
# reading the user's Git configuration or writing a real Git repository.
REPO="$FIXTURE/workspace"
mkdir -p "$REPO/.git/hooks" "$REPO/.git/objects" "$REPO/.git/refs/heads" \
  "$REPO/.githooks" "$REPO/.devcontainer" "$REPO/nested/.devcontainer" || exit 2
printf 'ref: refs/heads/runtime-fixture\n' > "$REPO/.git/HEAD"
printf '[core]\n\thooksPath = .githooks\n' > "$REPO/.git/config"
for path in .git/hooks/probe .githooks/probe .devcontainer/probe nested/.devcontainer/probe; do
  printf 'immutable fixture\n' > "$REPO/$path"
done
cp "$ROOT/.devcontainer/post-create.sh" "$ROOT/.devcontainer/post-start.sh" "$REPO/.devcontainer/" || exit 2
# Make Unix permissions permissive so failed dev writes prove the RO overlays,
# rather than accidental ownership restrictions. The ordinary RW file is a control.
chmod -R a+rwX "$REPO" || exit 2
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
# shellcheck source=../launcher-common.sh
source "$ROOT/launcher-common.sh"
sandbox_git_ro_mounts "$REPO" "$WORKSPACE" || exit 1
sandbox_devcontainer_ro_mounts "$REPO" "$WORKSPACE" || exit 1

for provider in claude codex; do
  NAME="egress-smoke-$KIND-$provider-$$"
  NAMES+=("$NAME")
  echo "== run ($KIND / $provider) =="
  if ! "$RT" run -d --name "$NAME" \
    --init --cap-drop ALL \
    --cap-add NET_ADMIN --cap-add CHOWN --cap-add DAC_OVERRIDE \
    --cap-add FOWNER --cap-add SETUID --cap-add SETGID \
    -m 2g --tmpfs /tmp --tmpfs /var/tmp \
    -e "SANDBOX_AGENT=$provider" \
    -e DEVCONTAINER=true -e DISABLE_TELEMETRY=1 -e DISABLE_ERROR_REPORTING=1 \
    -e NODE_USE_ENV_PROXY=1 \
    -e HTTP_PROXY=http://127.0.0.1:3128 -e HTTPS_PROXY=http://127.0.0.1:3128 \
    -e http_proxy=http://127.0.0.1:3128 -e https_proxy=http://127.0.0.1:3128 \
    -e NO_PROXY=localhost,127.0.0.1,::1 -e no_proxy=localhost,127.0.0.1,::1 \
    -v "$REPO:$WORKSPACE" \
    "${GIT_RO_MOUNTS[@]}" "${DEVCONTAINER_RO_MOUNTS[@]}" \
    "$IMAGE" >/dev/null; then
    bad "$provider container startup failed"
    continue
  fi

  sentinel=0
  for _ in $(seq 1 30); do
    cx test -f /run/egress-firewall-ok 2>/dev/null && { sentinel=1; break; }
    sleep 1
  done
  if (( sentinel )); then ok "$provider firewall sentinel present"; else bad "$provider firewall sentinel missing"; fi
  proxy=0
  for _ in $(seq 1 20); do
    cx bash -c 'exec 3<>/dev/tcp/127.0.0.1/3128' 2>/dev/null && { proxy=1; break; }
    sleep 1
  done
  if (( proxy )); then ok "$provider proxy listening"; else bad "$provider proxy unavailable"; fi

  # Stream only this reviewed fixture into the ephemeral container. Never copy
  # host configuration, credentials, keys or the actual project checkout.
  if ! "$RT" exec -i "$NAME" sh -c 'cat > /tmp/lockbox-runtime-policy.py; chmod 0755 /tmp/lockbox-runtime-policy.py' < "$HERE/runtime-policy.py"; then
    bad "$provider runtime fixture install failed"
    continue
  fi
  if cxdev "$VERIFY"; then ok "$provider pin manifest verified as dev"; else bad "$provider pin manifest failed"; fi
  if cxdev bash "$POST_CREATE" && cxdev bash "$POST_START"; then
    ok "$provider normal create/start lifecycle completed as dev"
  else
    bad "$provider create/start lifecycle failed"
  fi
  if cxdev python3 /tmp/lockbox-runtime-policy.py login "$provider"; then
    ok "$provider native logged-out status checked without credentials"
  else
    bad "$provider native logged-out status failed"
  fi
  if "$RT" exec -i "$NAME" sh -c 'cat > /tmp/lockbox-agent-config.py; chmod 0644 /tmp/lockbox-agent-config.py' < "$ROOT/.devcontainer/agent-config.py" \
    && "$RT" exec -i "$NAME" sh -c 'cat > /tmp/lockbox-provider-features.py; chmod 0644 /tmp/lockbox-provider-features.py' < "$HERE/provider-features.py" \
    && cxdev python3 /tmp/lockbox-provider-features.py "$provider" /tmp/lockbox-agent-config.py; then
    ok "$provider native shared instruction, skill and MCP fixture passed"
  else
    bad "$provider native feature fixture failed"
  fi
  if cxdev python3 /tmp/lockbox-runtime-policy.py mounts "$WORKSPACE"; then
    ok "$provider RW control and all RO overlays verified"
  else
    bad "$provider mount enforcement failed"
  fi
  if cx python3 /tmp/lockbox-runtime-policy.py network; then
    ok "$provider packet and TLS policy runtime fixtures passed"
  else
    bad "$provider packet or TLS policy fixture failed"
  fi

  # Refusal must be a local HTTP 403, not merely a timeout or DNS failure.
  code="$(cxdev curl -sS -x http://127.0.0.1:3128 --max-time 8 \
    -o /dev/null -w '%{http_code}' http://api.anthropic.com:443/ 2>/dev/null || true)"
  if [[ "$code" == 403 ]]; then ok "$provider non-CONNECT cleartext returns HTTP 403"; else bad "$provider cleartext refusal returned '${code:-none}'"; fi

  # Verify the pinned Claude config-directory contract with disposable state.
  if cxdev python3 /tmp/lockbox-runtime-policy.py claude-layout; then
    ok "$provider Claude custom config-directory layout verified"
  else
    bad "$provider Claude config-directory layout failed"
  fi

  if [[ -n "${CHECK_INTERNET:-}" && "$provider" == codex ]]; then
    for endpoint in https://api.anthropic.com/ https://api.openai.com/ https://malware-list.aikido.dev/malware_predictions.json; do
      code="$(cxdev curl -sS -I -x http://127.0.0.1:3128 --max-time 15 \
        -o /dev/null -w '%{http_code}' "$endpoint" 2>/dev/null || true)"
      if [[ "$code" =~ ^[1-5][0-9][0-9]$ && "$code" != 403 ]]; then
        ok "optional endpoint responds: $endpoint (HTTP $code)"
      else
        note "optional endpoint unavailable: $endpoint (HTTP '${code:-none}')"
      fi
    done
  fi
done

echo "egress-test ($KIND): $pass passed, $fail failed, $warn warnings; IPv6 skips appear above"
(( fail == 0 ))

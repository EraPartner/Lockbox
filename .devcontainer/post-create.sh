#!/usr/bin/env bash
# Runs once when the devcontainer is first created, as the `dev` user.
# Wires shared tooling and initializes provider-specific state.
#
# NOTE: all privileged setup (perms repair, egress proxy, firewall) happens in
# the root ENTRYPOINT (/usr/local/sbin/lockbox-entrypoint) BEFORE this runs.
# The container has no-new-privileges, so there is no sudo here.

set -euo pipefail
cd /workspaces/LockBox
AGENT="${SANDBOX_AGENT:-}"
case "$AGENT" in
  claude|codex) ;;
  *)
    echo "[post-create] ABORT: SANDBOX_AGENT is '${AGENT:-unset}'; recreate with the selected provider launcher." >&2
    exit 1
    ;;
esac

# Wait for the egress proxy (started by the root entrypoint) before any network
# install — postCreate can race the entrypoint's proxy startup, and with egress
# locked to the proxy UID, installs fail until 127.0.0.1:3128 is listening.
echo "[post-create] Waiting for egress proxy on 127.0.0.1:3128..."
for _ in $(seq 1 30); do
  (exec 3<>/dev/tcp/127.0.0.1/3128) 2>/dev/null && break
  sleep 1
done

# Supply-chain protection: safe-chain screens package installs against its malware
# list (malware-list.aikido.dev, allowlisted in squid). `safe-chain setup` writes
# shell wrappers; BASH_ENV (set at container run) sources them into every bash
# session so npm/bun/pip installs an agent runs mid-session are screened.
#
# safe-chain is now BAKED INTO THE IMAGE at a reviewed pin (see ../tool-pins.env
# and the Dockerfile). It used to be `npm install -g @aikidosec/safe-chain` right
# here: an UNPINNED registry fetch executed inside the security boundary, and
# executed BEFORE it could screen anything — so a compromised safe-chain release
# was the one package guaranteed to land unscreened. This step now only wires up
# the shell wrappers: no network, no new code, nothing to retry.
command -v safe-chain >/dev/null 2>&1 || {
  echo "[post-create] ERROR: safe-chain is missing from the image; rebuild required." >&2
  exit 1
}
safe-chain setup >/dev/null 2>&1 || {
  echo "[post-create] ERROR: safe-chain setup failed; refusing an unscreened session." >&2
  exit 1
}
echo "[post-create] safe-chain wired up (baked pin, no runtime fetch)."

# No JS dependency install: LockBox is bash/shell infra with no root package.json /
# lockfile, so there is nothing to `npm ci`. safe-chain above still screens any
# ad-hoc npm/pip installs an agent runs mid-session. The repo's own tooling (shellcheck,
# make, node for .claude/hooks/guard.mjs, python3 for plugin hooks) is baked into the
# image, so no per-create install step is needed.

# Minimal ~/.gitconfig: just mark the bind-mounted workspace safe so read-only
# git ops (log/diff/status) work despite the mount's non-dev ownership. No
# identity/signing/push config — publication happens in the separate git-agent
# container, and this editing container's .git is read-only.
if [[ ! -f /home/dev/.gitconfig || ! -s /home/dev/.gitconfig ]]; then
  cat > /home/dev/.gitconfig <<'EOF'
[safe]
    directory = /workspaces/LockBox
EOF
fi

# Provider configuration is initialized by the host launcher after lifecycle
# readiness. Claude uses its private volume; there is no per-create host merge.

echo "[post-create] Done."
echo "[post-create] Lint:  shellcheck *.sh    ·    self-check:  ./sync.sh --check ; ./audit.sh"
echo "[post-create] Selected agent: $AGENT"

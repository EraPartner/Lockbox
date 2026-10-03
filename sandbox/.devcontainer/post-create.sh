#!/usr/bin/env bash
# Baked at /usr/local/share/dev-sandbox/post-create.sh (NOT read from the
# workspace — the workspace is the mounted TARGET project). Runs once on create,
# as `dev`. Initializes provider config and a minimal git identity.

set -euo pipefail
AGENT="${SANDBOX_AGENT:-}"
case "$AGENT" in
  claude|codex) ;;
  *)
    echo "[post-create] ABORT: SANDBOX_AGENT is '${AGENT:-unset}'; recreate with DEV_SANDBOX_REBUILD=1." >&2
    exit 1
    ;;
esac

# The egress-proxy barrier lives in the launcher (bin/dev step 6): it polls
# 127.0.0.1:3128 in the SAME shell immediately before invoking this script, so the
# proxy is already up by the time anything here touches the network.

# Provider configuration is initialized by the host launcher after lifecycle
# readiness. Claude uses its private volume; there is no per-create host merge.

# --- Supply-chain protection: wire up the BAKED safe-chain --------------------
# Screens package installs against the malware list (malware-list.aikido.dev,
# allowlisted in squid). `safe-chain setup` writes shell wrappers; BASH_ENV (set
# for the final dev process) sources them into bash sessions so npm/bun/pip
# installs an agent runs mid-session are screened. This box previously promised
# screening (Dockerfile + doctor) but never installed it, so installs here were
# not screened.
#
# safe-chain is now BAKED INTO THE IMAGE at a reviewed pin (see ../../tool-pins.env
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

# --- git: identity only (no signing key, no push token in this box) -----------
# Include the bind-mounted host gitconfig (user.name/email) and mark the
# workspace safe (the bind mount has non-dev ownership). Git state is mounted
# read-only; staging, signing, committing and pushing belong to git-agent.
if [[ ! -f /home/dev/.gitconfig || ! -s /home/dev/.gitconfig ]]; then
  cat > /home/dev/.gitconfig <<'EOF'
[include]
    path = /home/dev/.gitconfig-host
[safe]
    directory = /workspaces/project
EOF
fi

echo "[post-create] Done. Generic full-dev sandbox:"
echo "[post-create]   - workspace /workspaces/project is READ-WRITE"
echo "[post-create]   - egress is locked to the allowlist (base + this project's overlay)"
echo "[post-create]   - edit and test here; stage, sign, commit and push in 'git-agent'"
echo "[post-create]   - selected agent: $AGENT"

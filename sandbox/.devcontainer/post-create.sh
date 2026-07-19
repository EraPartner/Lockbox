#!/usr/bin/env bash
# Baked at /usr/local/share/dev-sandbox/post-create.sh (NOT read from the
# workspace — the workspace is the mounted TARGET project). Runs once on create,
# as `dev`. Seeds the Claude config and writes a minimal git config for identity.

set -euo pipefail

# The egress-proxy barrier lives in the launcher (bin/dev step 6): it polls
# 127.0.0.1:3128 in the SAME shell immediately before invoking this script, so the
# proxy is already up by the time anything here touches the network.

# --- Seed ~/.claude from the SANITIZED stage the host launcher produced --------
STAGE=/home/dev/.claude-stage
if [[ ! -f /home/dev/.claude/settings.json && -d "$STAGE/dot-claude" ]]; then
  echo "[post-create] Seeding ~/.claude from sanitized stage..."
  rsync -a --ignore-errors "$STAGE/dot-claude/" /home/dev/.claude/ \
    || echo "[post-create] WARN: ~/.claude rsync seed had errors." >&2
fi
if [[ ! -f /home/dev/.claude.json && -f "$STAGE/claude.json" ]]; then
  cp "$STAGE/claude.json" /home/dev/.claude.json
  chmod 0600 /home/dev/.claude.json
fi

# --- Supply-chain protection: wire up the BAKED safe-chain --------------------
# Screens package installs against the malware list (malware-list.aikido.dev,
# allowlisted in squid). `safe-chain setup` writes shell wrappers; BASH_ENV (set
# in bin/dev's `container run`) sources them into every bash session so npm/bun/pip
# installs Claude runs mid-session are screened. Matches Vision/Watchman/Napoleon/
# Brain — this box previously promised it (Dockerfile + doctor) but never installed
# it, so installs here were NOT screened. (F12)
#
# safe-chain is now BAKED INTO THE IMAGE at a reviewed pin (see ../../tool-pins.env
# and the Dockerfile). It used to be `npm install -g @aikidosec/safe-chain` right
# here: an UNPINNED registry fetch executed inside the security boundary, and
# executed BEFORE it could screen anything — so a compromised safe-chain release
# was the one package guaranteed to land unscreened. This step now only wires up
# the shell wrappers: no network, no new code, nothing to retry.
if command -v safe-chain >/dev/null 2>&1; then
  safe-chain setup >/dev/null 2>&1 || true
  echo "[post-create] safe-chain wired up (baked pin, no runtime fetch)."
else
  echo "[post-create] ⚠ WARN: safe-chain MISSING FROM THE IMAGE — package installs are NOT" >&2
  echo "[post-create]   supply-chain screened. The image was built wrong; rebuild it." >&2
  echo "[post-create]   \`.devcontainer/bin/doctor\` will also flag this." >&2
fi

# --- git: identity only (no signing key, no push token in this box) -----------
# Include the bind-mounted host gitconfig (user.name/email) and mark the
# workspace safe (the bind mount has non-dev ownership). Commits made here are
# UNSIGNED; push them from the dedicated `git-agent` sandbox (which signs).
if [[ ! -f /home/dev/.gitconfig || ! -s /home/dev/.gitconfig ]]; then
  cat > /home/dev/.gitconfig <<'EOF'
[include]
    path = /home/dev/.gitconfig-host
[safe]
    directory = /workspaces/project
# This sandbox does not forward a signing key. Don't fail commits if the host
# gitconfig sets commit.gpgsign=true; override it off locally.
[commit]
    gpgsign = false
[tag]
    gpgsign = false
EOF
fi

echo "[post-create] Done. Generic full-dev sandbox:"
echo "[post-create]   - workspace /workspaces/project is READ-WRITE"
echo "[post-create]   - egress is locked to the allowlist (base + this project's overlay)"
echo "[post-create]   - commit locally if you like; PUSH from the 'git-agent' sandbox"

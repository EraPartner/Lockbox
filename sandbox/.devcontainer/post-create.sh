#!/usr/bin/env bash
# Baked at /usr/local/share/dev-sandbox/post-create.sh (NOT read from the
# workspace — the workspace is the mounted TARGET project). Runs once on create,
# as `dev`. Seeds the Claude config and writes a minimal git config for identity.

set -euo pipefail

# Wait for the egress proxy (started by the root entrypoint) before anything that
# might touch the network.
echo "[post-create] Waiting for egress proxy on 127.0.0.1:3128..."
for _ in $(seq 1 30); do
  (exec 3<>/dev/tcp/127.0.0.1/3128) 2>/dev/null && break
  sleep 1
done

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

# --- Supply-chain protection: install Aikido safe-chain -----------------------
# Screens package installs against the malware list (malware-list.aikido.dev,
# allowlisted in squid). `safe-chain setup` writes shell wrappers; BASH_ENV (set
# in bin/dev's `container run`) sources them into every bash session so npm/bun/pip
# installs Claude runs mid-session are screened. Matches Vision/Watchman/Napoleon/
# Brain — this box previously promised it (Dockerfile + doctor) but never installed
# it, so installs here were NOT screened. (F12)
echo "[post-create] Installing safe-chain (supply-chain protection)..."
sc_ok=0
for attempt in 1 2 3; do
  if npm install -g @aikidosec/safe-chain >/dev/null 2>&1 && command -v safe-chain >/dev/null 2>&1; then
    sc_ok=1; break
  fi
  echo "[post-create] safe-chain install attempt $attempt failed; retrying..." >&2
  sleep $(( attempt * 2 ))
done
if (( sc_ok )); then
  safe-chain setup >/dev/null 2>&1 || true
  echo "[post-create] safe-chain installed (screens npm/bun/pip in later sessions)."
else
  echo "[post-create] ⚠ WARN: safe-chain install FAILED after retries — package installs are NOT" >&2
  echo "[post-create]   supply-chain screened. \`.devcontainer/bin/doctor\` will flag this." >&2
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

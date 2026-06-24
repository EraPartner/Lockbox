#!/usr/bin/env bash
# Pre-commit leak audit (pattern from nvk/agent-stack-bootstrap's audit.sh).
#
# The sandbox design forwards credentials at RUNTIME (env vars, `gh auth token`,
# macOS Keychain) and never writes them into the repo. This script enforces that
# invariant: it scans the committed devcontainer / egress / installer files for
# HARDCODED secrets and private-key material, and exits non-zero if any are found
# (so it can run as a pre-commit / pre-publish gate). Env-var references like
# `GH_TOKEN=$gh_tok` are fine — only literal secret VALUES trip it.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd -P)"
PROJECTS="$(cd "$HERE/.." && pwd -P)"
# shellcheck source=paths.sh
source "$HERE/paths.sh"

# Scan the egress repo itself, every managed .devcontainer (shared list), and the
# per-project install scripts.
ROOTS=(
  "$HERE"
  "${EGRESS_DEVCONTAINERS[@]}"
  "$PROJECTS/Vision/install.sh"
  "$PROJECTS/Watchman/install.sh"
)

# Value-bearing secret signatures (the literal token formats — not var names).
PATTERNS=(
  'sk-ant-[A-Za-z0-9_-]{16,}'           # Anthropic API key
  'ghp_[A-Za-z0-9]{20,}'                # GitHub PAT (classic)
  'gh[ousr]_[A-Za-z0-9]{20,}'           # GitHub oauth/user/server/refresh token
  'github_pat_[A-Za-z0-9_]{20,}'        # GitHub PAT (fine-grained)
  'AKIA[0-9A-Z]{16}'                    # AWS access key id
  'xox[baprs]-[A-Za-z0-9-]{10,}'        # Slack token
  'sk-proj-[A-Za-z0-9_-]{20,}'          # OpenAI project key
  'AIza[0-9A-Za-z_-]{35}'               # Google API key
  'npm_[A-Za-z0-9]{36}'                 # npm automation token
  'glpat-[A-Za-z0-9_-]{20,}'            # GitLab PAT
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'  # any private key block
)

# Dirs never worth scanning.
PRUNE=( -name .git -o -name node_modules -o -name dist -o -name venv -o -name .venv )

findings=0
report() { echo "  ✗ $*"; findings=$((findings + 1)); }

scan_one() {
  local f="$1"
  # Skip non-text (binary) files.
  if ! grep -Iq . "$f" 2>/dev/null; then return; fi
  local pat
  for pat in "${PATTERNS[@]}"; do
    while IFS= read -r hit; do
      [[ -n "$hit" ]] && report "secret in ${f#"$PROJECTS"/}: ${hit}"
    done < <(grep -nE "$pat" "$f" 2>/dev/null)
  done
  # Committed private-key files (a .pub is a public key — allowed).
  case "$f" in
    *.pem|*.key|*/id_rsa|*/id_ed25519)
      report "private-key file committed: ${f#"$PROJECTS"/}" ;;
  esac
}

scan_root() {
  local root="$1"
  if [[ -f "$root" ]]; then
    scan_one "$root"
  elif [[ -d "$root" ]]; then
    while IFS= read -r f; do scan_one "$f"; done \
      < <(find "$root" \( "${PRUNE[@]}" \) -prune -o -type f ! -name '*.pub' -print 2>/dev/null)
  fi
}

echo "== Egress/devcontainer leak audit =="
for root in "${ROOTS[@]}"; do
  [[ -e "$root" ]] && scan_root "$root" || echo "  (skip, missing) ${root}"
done

echo
if (( findings == 0 )); then
  echo "✓ No hardcoded secrets or private keys found in committed sandbox files."
  exit 0
fi
echo "✗ ${findings} potential leak(s) found — do NOT commit. Move secrets to runtime"
echo "  forwarding (env / gh auth token / Keychain), as the sandbox design requires."
exit 1

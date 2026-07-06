#!/usr/bin/env bash
# Pre-commit leak audit (pattern from nvk/agent-stack-bootstrap's audit.sh).
#
# The sandbox design forwards credentials at RUNTIME (env vars, `gh auth token`,
# macOS Keychain) and never writes them into the repo. This script enforces that
# invariant: it scans this repo's TRACKED / STAGED files for HARDCODED secrets and
# private-key material, and exits non-zero if any are found (so it runs as a
# pre-commit / pre-publish gate + in CI). Env-var references like
# `GH_TOKEN=$gh_tok` are fine — only literal secret VALUES trip it.
#
# WHAT IT SCANS — the git INDEX, not the working tree. For each tracked file it
# reads the STAGED blob (`git show :<file>`), not the on-disk copy. This matters:
#   - Closes a bypass: `git add secret` then overwriting the worktree copy to
#     remove the secret used to pass (the old `find`-over-the-filesystem scan saw
#     the clean worktree) while the COMMIT still recorded the secret. Scanning the
#     index sees exactly what is being committed.
#   - Scopes to THIS repo: no false positives from untracked/gitignored local files
#     (a stray `.env`, dropped `id_rsa`) or from sibling repos. Each fleet repo
#     runs its own copy of this gate.
#   - On a fresh CI checkout the index == HEAD, so CI scans the committed content.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd -P)"
cd "$HERE" || { echo "audit: cannot cd to $HERE" >&2; exit 2; }

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "audit: not inside a git work tree ($HERE) — cannot scan the index." >&2
  exit 2
fi

# Value-bearing secret signatures (the literal token formats — not var names),
# combined into ONE alternation so each file is scanned with a single grep pass.
# NOTE: the bare 40-char AWS *secret* access key has no distinctive prefix, so a
# regex for it matches every 40-hex git SHA (e.g. the action pins in .github/) —
# unusable noise. It is deliberately omitted; the prefixed AWS ids below are kept.
SECRET_RE='sk-ant-[A-Za-z0-9_-]{16,}'                # Anthropic API key
SECRET_RE="$SECRET_RE"'|sk-proj-[A-Za-z0-9_-]{20,}'  # OpenAI project key
SECRET_RE="$SECRET_RE"'|sk-[A-Za-z0-9]{40,}'         # OpenAI legacy key (sk-<48>)
SECRET_RE="$SECRET_RE"'|ghp_[A-Za-z0-9]{20,}'        # GitHub PAT (classic)
SECRET_RE="$SECRET_RE"'|gh[ousr]_[A-Za-z0-9]{20,}'   # GitHub oauth/user/server/refresh
SECRET_RE="$SECRET_RE"'|github_pat_[A-Za-z0-9_]{20,}' # GitHub PAT (fine-grained)
SECRET_RE="$SECRET_RE"'|AKIA[0-9A-Z]{16}'            # AWS access key id
SECRET_RE="$SECRET_RE"'|ASIA[0-9A-Z]{16}'            # AWS STS temp access key id
SECRET_RE="$SECRET_RE"'|xox[baprs]-[A-Za-z0-9-]{10,}' # Slack token
SECRET_RE="$SECRET_RE"'|sk_live_[A-Za-z0-9]{20,}'    # Stripe live secret key
SECRET_RE="$SECRET_RE"'|rk_live_[A-Za-z0-9]{20,}'    # Stripe live restricted key
SECRET_RE="$SECRET_RE"'|AIza[0-9A-Za-z_-]{35}'       # Google API key
SECRET_RE="$SECRET_RE"'|npm_[A-Za-z0-9]{36}'         # npm automation token
SECRET_RE="$SECRET_RE"'|glpat-[A-Za-z0-9_-]{20,}'    # GitLab PAT
SECRET_RE="$SECRET_RE"'|-----BEGIN [A-Z ]*PRIVATE KEY-----' # any private key block

findings=0
# Report ONLY file:line — never the matched text. In CI the `secrets` job runs
# this script, so echoing the matched line would write the literal secret into the
# build log / terminal scrollback, widening exposure.
report() { echo "  ✗ $*"; findings=$((findings + 1)); }

scan_file() {
  local f="$1" tmp
  # Private-key FILE by NAME — flag regardless of text/binary (a DER-encoded .key
  # is binary and would be skipped by the text scan below).
  case "$f" in
    */id_rsa|*/id_ed25519|*.key) report "private-key file committed: ${f}" ;;
  esac
  tmp="$(mktemp)" || return 0
  # Staged blob (index version) — see the header for why not the worktree copy.
  if ! git show ":$f" >"$tmp" 2>/dev/null; then rm -f "$tmp"; return 0; fi
  # `.pem` is ALSO the extension for PUBLIC certs / chains / CSRs, so gate it on
  # content instead of the filename to avoid a false hit on a committed public cert.
  case "$f" in
    *.pem) grep -q 'PRIVATE KEY' "$tmp" && report "private-key file committed: ${f}" ;;
  esac
  # Value-bearing secrets — skip binary blobs, report file:line only.
  if grep -Iq . "$tmp"; then
    local ln
    while IFS= read -r ln; do
      [[ -n "$ln" ]] && report "secret pattern at ${f}:${ln}"
    done < <(grep -nE "$SECRET_RE" "$tmp" 2>/dev/null | cut -d: -f1)
  fi
  rm -f "$tmp"
}

echo "== Egress/devcontainer leak audit (git index) =="
# All tracked files, NUL-delimited so paths with spaces are safe.
while IFS= read -r -d '' f; do
  scan_file "$f"
done < <(git ls-files -z)

echo
if (( findings == 0 )); then
  echo "✓ No hardcoded secrets or private keys found in the staged/committed files."
  exit 0
fi
echo "✗ ${findings} potential leak(s) found — do NOT commit. Move secrets to runtime"
echo "  forwarding (env / gh auth token / Keychain), as the sandbox design requires."
exit 1

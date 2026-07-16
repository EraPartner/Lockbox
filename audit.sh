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

echo "== Egress/devcontainer leak audit (git index) =="

# Private-key FILES by NAME — git ls-files pathspecs do the filtering natively (the
# default pathspec magic makes `*` cross `/`, and a bare `id_rsa` matches the root
# copy that `*/id_rsa` alone would miss). Flag regardless of text/binary — a
# DER-encoded .key is binary and would be skipped by the value scan below. Paths are
# NUL-delimited so names with spaces are safe.
while IFS= read -r -d '' f; do
  report "private-key file committed: ${f}"
done < <(git ls-files -z -- 'id_rsa' '*/id_rsa' 'id_ed25519' '*/id_ed25519' '*.key')
# `.pem` is ALSO the extension for PUBLIC certs / chains / CSRs, so gate it on the
# STAGED blob's content (index version — see the header for why not the worktree copy)
# to avoid a false hit on a committed public cert.
while IFS= read -r -d '' f; do
  git show ":$f" 2>/dev/null | grep -q 'PRIVATE KEY' && report "private-key file committed: ${f}"
done < <(git ls-files -z -- '*.pem')

# Value-bearing secrets — ONE process over the whole index: `git grep --cached`
# searches the staged blobs directly (same semantics as `git show :<file>` per
# file, so the index-not-worktree invariant holds), and -I skips binary blobs.
# This replaces the old per-file mktemp/git-show/grep/rm cycle (~5-6 fork/execs
# per tracked file on every commit). Report file:line only (cut drops the matched
# text — see `report` above for why).
while IFS= read -r hit; do
  [[ -n "$hit" ]] && report "secret pattern at ${hit}"
done < <(git grep --cached -I -nE "$SECRET_RE" -- . 2>/dev/null | cut -d: -f1,2)

echo
if (( findings == 0 )); then
  echo "✓ No hardcoded secrets or private keys found in the staged/committed files."
  exit 0
fi
echo "✗ ${findings} potential leak(s) found — do NOT commit. Move secrets to runtime"
echo "  forwarding (env / gh auth token / Keychain), as the sandbox design requires."
exit 1

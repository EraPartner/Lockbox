#!/usr/bin/env bash
# Canonical locations of the egress-managed devcontainers. Sourced by sync.sh and
# audit.sh so the target list lives in ONE place.
#
# WHY THIS EXISTS: sync.sh and audit.sh used to hardcode their own (different)
# path lists. When the repos moved from
# /Users/computer/Documents/Personal/Scripts/Projects -> /Users/computer/Code,
# only audit.sh's derivation followed; sync.sh kept the old paths and silently
# SKIPped 3 of 4 containers, so the "single source of truth" egress lock stopped
# propagating to Vision/Watchman/git-agent. One shared list prevents that drift.
#
# Override CODE_ROOT / BRAIN_DC in the environment if your layout differs.

CODE_ROOT="${CODE_ROOT:-/Users/computer/Code}"
BRAIN_DC="${BRAIN_DC:-/Users/computer/Library/Mobile Documents/iCloud~md~obsidian/Documents/Brain/.devcontainer}"

# The generic dev-sandbox was MERGED INTO this repo (it had no git repo of its
# own; this puts its security-critical launcher/Dockerfile/entrypoint under git +
# the .githooks gate). It now lives at <repo>/sandbox/.devcontainer and is a sync
# target like any other. Resolve the repo root from THIS file's own location so
# the path stays correct regardless of where the repo is checked out.
EGRESS_REPO="${EGRESS_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)}"

# Every .devcontainer the canonical egress files are vendored into.
# shellcheck disable=SC2034  # consumed by scripts that source this file (sync.sh/audit.sh)
EGRESS_DEVCONTAINERS=(
  "$CODE_ROOT/Vision/.devcontainer"
  "$CODE_ROOT/Watchman/.devcontainer"
  "$CODE_ROOT/Napoleon-relay/.devcontainer"
  "$CODE_ROOT/git-agent/.devcontainer"
  "$CODE_ROOT/dotfiles/.devcontainer"
  "$CODE_ROOT/VaultLens/.devcontainer"
  "$EGRESS_REPO/.devcontainer"
  "$EGRESS_REPO/sandbox/.devcontainer"
  "$BRAIN_DC"
)

# CI / self-check: with EGRESS_SELF_ONLY=1, restrict the target list to the in-repo
# sandbox devcontainer only. The sibling fleet repos aren't checked out in CI, so
# `EGRESS_SELF_ONLY=1 ./sync.sh --check` lets CI verify THIS repo's own vendored
# copies AND its regenerated allowlist (via the same gen_allowlist, no divergence)
# without needing the whole fleet side-by-side.
if [[ "${EGRESS_SELF_ONLY:-0}" == 1 ]]; then
  # shellcheck disable=SC2034  # consumed by scripts that source this file
  EGRESS_DEVCONTAINERS=("$EGRESS_REPO/sandbox/.devcontainer")
fi

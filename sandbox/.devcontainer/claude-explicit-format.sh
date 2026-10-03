#!/usr/bin/env bash
# Bounded single-file formatter for Codex. This command never installs tools
# and never formats outside the file's Git worktree. It can run explicitly or
# through the reviewed post-edit allowlist.
set -euo pipefail

automatic=0
if [ "${1:-}" = "--automatic" ]; then
  automatic=1
  shift
fi
if [ "$#" -ne 1 ]; then
  printf 'Usage: explicit-format [--automatic] <file>\n' >&2
  exit 2
fi

requested_file="$1"
[ -f "$requested_file" ] || {
  printf 'explicit-format: not a regular file: %s\n' "$requested_file" >&2
  exit 1
}

resolved_file="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' \
  "$requested_file")"
worktree="$(git -C "$(dirname "$resolved_file")" rev-parse --show-toplevel 2>/dev/null)" || {
  printf 'explicit-format: file is not in a Git worktree: %s\n' "$resolved_file" >&2
  exit 1
}
resolved_worktree="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' \
  "$worktree")"

case "$resolved_file" in
  "$resolved_worktree"/*) ;;
  *)
    printf 'explicit-format: refusing path outside Git worktree: %s\n' "$resolved_file" >&2
    exit 1
    ;;
esac

find_local_prettier() {
  local search_dir
  search_dir="$(dirname "$resolved_file")"
  while :; do
    if [ -x "$search_dir/node_modules/.bin/prettier" ]; then
      printf '%s\n' "$search_dir/node_modules/.bin/prettier"
      return 0
    fi
    [ "$search_dir" = "$resolved_worktree" ] && return 1
    search_dir="$(dirname "$search_dir")"
  done
}

extension="${resolved_file##*.}"
extension="$(printf '%s' "$extension" | tr '[:upper:]' '[:lower:]')"

unavailable() {
  # 3 means unsupported here, rather than a formatter execution failure. The
  # post-edit hook skips it; explicit invocations still explain prerequisites.
  printf 'explicit-format: %s\n' "$1" >&2
  if [ "$automatic" -eq 1 ]; then exit 3; fi
  exit 1
}

case "$extension" in
  js|jsx|ts|tsx|mjs|cjs|json|jsonc|css|scss|less|html|vue|svelte|md|mdx|yaml|yml|graphql)
    prettier="$(find_local_prettier)" || {
      unavailable 'repository-local Prettier is unavailable'
    }
    exec "$prettier" --write --log-level silent -- "$resolved_file"
    ;;
  py)
    if command -v ruff >/dev/null 2>&1; then
      exec ruff format -- "$resolved_file"
    elif command -v black >/dev/null 2>&1; then
      exec black --quiet -- "$resolved_file"
    fi
    unavailable 'Ruff or Black is required for Python'
    ;;
  rs)
    command -v rustfmt >/dev/null 2>&1 || {
      unavailable 'rustfmt is unavailable'
    }
    # File arguments make rustfmt follow child modules, including #[path]
    # outside this worktree. Stdin formats only the requested source. Keep the
    # original intact on failure and use its directory for config discovery.
    formatted_rust="$(mktemp)"
    trap 'rm -f "$formatted_rust"' EXIT
    (cd "$(dirname "$resolved_file")" && rustfmt --emit stdout < "$resolved_file") \
      > "$formatted_rust"
    cat "$formatted_rust" > "$resolved_file"
    ;;
  go)
    command -v gofmt >/dev/null 2>&1 || {
      unavailable 'gofmt is unavailable'
    }
    exec gofmt -w "$resolved_file"
    ;;
  *)
    printf 'explicit-format: unsupported file extension: %s\n' "$extension" >&2
    exit 1
    ;;
esac

# ─── vendored by LockBox v0.1.0 · canonical sha256:67747f90cca198e06c52e994520fd18d961fbbe73676fb79853d4c11fb0eee26 ───
# Generated from the canonical source by LockBox/sync.sh — DO NOT EDIT HERE.
# Edit LockBox/claude-explicit-format.sh and re-run ./sync.sh.

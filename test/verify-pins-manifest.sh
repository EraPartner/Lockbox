#!/usr/bin/env bash
# Invalid manifests must fail before any platform-specific binary hashing. This
# test therefore runs on macOS as well as in the Linux sandbox image.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
CHECKER="$ROOT/sandbox/.devcontainer/bin/verify-pins"
TEST_TMP="$(mktemp -d)"
trap 'rm -rf "$TEST_TMP"' EXIT

expect_rejected() {
  local label="$1" content="$2" manifest
  manifest="$TEST_TMP/$label.txt"
  printf '%s' "$content" > "$manifest"
  if DEV_SANDBOX_PINFILE="$manifest" "$CHECKER" --quiet > /dev/null 2> "$TEST_TMP/$label.err"; then
    echo "verify-pins-manifest-test: accepted $label manifest" >&2
    exit 1
  fi
}

expect_rejected empty ''
expect_rejected comments $'# no pins\n'
expect_rejected malformed $'node\t/bin/node\tnot-a-sha256\n'
expect_rejected unexpected $'other\t/bin/other\t0000000000000000000000000000000000000000000000000000000000000000\n'
expect_rejected duplicate $'node\t/bin/node\t0000000000000000000000000000000000000000000000000000000000000000\nnode\t/bin/node\t0000000000000000000000000000000000000000000000000000000000000000\n'

protocol="$("$CHECKER" --print-cache-protocol)"
[[ "$protocol" == lockbox-verify-pins-cache-v1 ]] || {
  echo "verify-pins-manifest-test: cache protocol mismatch: $protocol" >&2
  exit 1
}

echo "verify-pins-manifest-test: invalid and incomplete manifests rejected"

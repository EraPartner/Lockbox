#!/usr/bin/env bash
# Exercise both installed checker variants with real executable files and hashes.
# GNU stat/readlink are simulated for the temporary fixtures, so the valid control
# can run on macOS without root ownership or modifying system directories. Real
# root ownership and image paths remain a container smoke-test responsibility.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
TEST_TMP="$(mktemp -d)"
trap 'rm -rf "$TEST_TMP"' EXIT

fail() { echo "verify-pins-manifest-test: $*" >&2; exit 1; }
TEST_BIN="$TEST_TMP/bin"
mkdir "$TEST_BIN"
TEST_SHA256="$(command -v sha256sum)"
TEST_PYTHON="$(command -v python3)"
export TEST_SHA256 TEST_PYTHON

for name in node npm claude codex bwrap gh git python3 safe-chain; do
  printf '#!/usr/bin/env bash\n# synthetic %s executable\nexit 0\n' "$name" > "$TEST_BIN/$name"
  chmod 0755 "$TEST_BIN/$name"
done
cat > "$TEST_BIN/sha256sum" <<'EOF'
#!/usr/bin/env bash
exec "$TEST_SHA256" "$@"
EOF
cat > "$TEST_BIN/readlink" <<'EOF'
#!/usr/bin/env bash
[[ "$1" == -f && "$2" == -- ]] || exit 2
exec "$TEST_PYTHON" -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$3"
EOF
cat > "$TEST_BIN/stat" <<'EOF'
#!/usr/bin/env bash
[[ "$1" == -c && "$3" == -- && -e "$4" ]] || exit 2
case "$2" in
  %u) printf '%s\n' "${TEST_PIN_UID:-0}" ;;
  %a) printf '%s\n' "${TEST_PIN_MODE:-755}" ;;
  *) exit 2 ;;
esac
EOF
chmod 0755 "$TEST_BIN/sha256sum" "$TEST_BIN/readlink" "$TEST_BIN/stat"

VALID="$TEST_TMP/valid.txt"
: > "$VALID"
for name in node npm claude codex bwrap gh git python3 safe-chain; do
  hash="$("$TEST_SHA256" "$TEST_BIN/$name" | awk '{print $1}')"
  printf '%s\t%s\t%s\n' "$name" "$TEST_BIN/$name" "$hash" >> "$VALID"
done
VALID_CONTENT="$(cat "$VALID")"
FIRST_ROW="${VALID_CONTENT%%$'\n'*}"

run_checker() {
  local manifest="$1"
  env PATH="$TEST_BIN:$PATH" "$PIN_ENV=$manifest" "$CHECKER" --quiet
}

expect_rejected() {
  local label="$1" content="$2" reason="$3" manifest
  manifest="$TEST_TMP/$label.txt"
  printf '%s' "$content" > "$manifest"
  if run_checker "$manifest" > /dev/null 2> "$TEST_TMP/$label.err"; then
    fail "$VARIANT accepted $label manifest"
  fi
  grep -Fq -- "$reason" "$TEST_TMP/$label.err" || fail "$VARIANT rejected $label for an unexpected reason"
}

for VARIANT in lockbox sandbox; do
  if [[ "$VARIANT" == lockbox ]]; then
    CHECKER="$ROOT/.devcontainer/bin/verify-pins"
    PIN_ENV=LOCKBOX_PINFILE
  else
    CHECKER="$ROOT/sandbox/.devcontainer/bin/verify-pins"
    PIN_ENV=DEV_SANDBOX_PINFILE
  fi

  protocol="$("$CHECKER" --print-cache-protocol)"
  [[ "$protocol" == lockbox-verify-pins-cache-v1 ]] || fail "$VARIANT cache protocol mismatch: $protocol"

  # The complete manifest is a meaningful control: each tool resolves through
  # PATH and is hashed. Repeat without a final newline to cover the last row.
  run_checker "$VALID" > "$TEST_TMP/valid.out" || fail "$VARIANT rejected valid manifest"
  [[ ! -s "$TEST_TMP/valid.out" ]] || fail "$VARIANT ignored --quiet"
  printf '%s' "$VALID_CONTENT" > "$TEST_TMP/no-newline.txt"
  run_checker "$TEST_TMP/no-newline.txt" || fail "$VARIANT rejected valid final row without newline"

  expect_rejected empty '' 'pin manifest is incomplete'
  expect_rejected comments $'# no pins\n' 'pin manifest is incomplete'
  expect_rejected partial "$FIRST_ROW" 'pin manifest is incomplete'
  expect_rejected malformed "$VALID_CONTENT"$'\nnode\t/bin/node\tnot-a-sha256\n' 'malformed pin manifest row'
  expect_rejected empty-name "$VALID_CONTENT"$'\n\t/bin/node\t'"${FIRST_ROW##*$'\t'}" 'malformed pin manifest row'
  expect_rejected extra-field "$VALID_CONTENT"$'\n'"$FIRST_ROW"$'\textra\n' 'malformed pin manifest row'
  expect_rejected extra-empty-field "$VALID_CONTENT"$'\n'"$FIRST_ROW"$'\t\n' 'malformed pin manifest row'
  expect_rejected unexpected "$VALID_CONTENT"$'\nother\t/bin/other\t0000000000000000000000000000000000000000000000000000000000000000\n' 'unexpected pinned tool'
  expect_rejected duplicate "$VALID_CONTENT"$'\n'"$FIRST_ROW" 'duplicate pinned tool'

  if TEST_PIN_UID=1000 run_checker "$VALID" > /dev/null 2> "$TEST_TMP/owner.err"; then
    fail "$VARIANT accepted non-root tool ancestry"
  fi
  grep -Fq 'not protected by root-owned' "$TEST_TMP/owner.err" || fail "$VARIANT skipped ownership check"
  if TEST_PIN_MODE=777 run_checker "$VALID" > /dev/null 2> "$TEST_TMP/mode.err"; then
    fail "$VARIANT accepted writable tool ancestry"
  fi
  grep -Fq 'not protected by root-owned' "$TEST_TMP/mode.err" || fail "$VARIANT skipped mode check"

  printf '# changed\n' >> "$TEST_BIN/node"
  if run_checker "$VALID" > /dev/null 2> "$TEST_TMP/drift.err"; then
    fail "$VARIANT accepted executable fingerprint drift"
  fi
  grep -Fq 'node FINGERPRINT DRIFT' "$TEST_TMP/drift.err" || fail "$VARIANT skipped hashing"
  # Restore the fixture for the next checker variant.
  printf '#!/usr/bin/env bash\n# synthetic node executable\nexit 0\n' > "$TEST_BIN/node"
done

echo 'verify-pins-manifest-test: both checkers passed valid controls and rejected incomplete, malformed, duplicate, unsafe, and changed pins'

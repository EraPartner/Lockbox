#!/usr/bin/env bash
# Unit-test the host-side cache orchestration without starting a container. The
# root-side record validation itself is also pinned by literal invariant checks;
# the host runtime smoke test covers it in a real image.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
# shellcheck source=../launcher-common.sh
source "$ROOT/launcher-common.sh"

fail() { echo "verify-pins-cache-test: $*" >&2; exit 1; }
require_literal() {
  grep -Fq -- "$2" "$1" || fail "$1 is missing: $2"
}

TEST_TMP="$(mktemp -d)"
trap 'rm -rf "$TEST_TMP"' EXIT
CALL_LOG="$TEST_TMP/calls"

container() {
  {
    printf 'CALL'
    printf ' %q' "$@"
    printf '\n'
  } >> "$CALL_LOG"
  case "$*" in
    *' probe /run/lockbox-verify-pins/'*) [[ "${FAKE_CACHE_HIT:-0}" == 1 ]] ;;
    *' invalidate /run/lockbox-verify-pins/'*) return 0 ;;
    *' commit /run/lockbox-verify-pins/'*) [[ "${FAKE_COMMIT_FAIL:-0}" != 1 ]] ;;
    *'/usr/local/bin/test-verify-pins --print-cache-protocol'*)
      [[ "${FAKE_OLD_PROTOCOL:-0}" != 1 ]] && printf '%s\n' 'lockbox-verify-pins-cache-v1'
      ;;
    *'/usr/local/bin/test-verify-pins --quiet'*) [[ "${FAKE_VERIFY_FAIL:-0}" != 1 ]] ;;
    *) fail "unexpected container call: $*" ;;
  esac
}

run_helper() {
  sandbox_verify_pins_cached test-container dev \
    /usr/local/bin/test-verify-pins /usr/local/share/test/binary-pins.txt \
    TEST_PINFILE test-cache
}

: > "$CALL_LOG"
FAKE_CACHE_HIT=1 run_helper
[[ "$(wc -l < "$CALL_LOG" | tr -d ' ')" == 1 ]] || fail "warm hit did not stop after probe"

: > "$CALL_LOG"
FAKE_CACHE_HIT=0 run_helper
grep -Fq -- '--user dev -e BASH_ENV=' "$CALL_LOG" || fail "cold verifier did not clear BASH_ENV"
grep -Fq -- '/usr/bin/env -u TEST_PINFILE' "$CALL_LOG" || fail "cold verifier did not clear pin override"
grep -Fq -- '/usr/local/bin/test-verify-pins --quiet' "$CALL_LOG" || fail "cold verifier was not absolute"
grep -Fq -- ' commit /run/lockbox-verify-pins/test-cache.ok' "$CALL_LOG" || fail "cold success was not committed"

: > "$CALL_LOG"
FAKE_CACHE_HIT=0 FAKE_OLD_PROTOCOL=1 run_helper 2> "$TEST_TMP/old-warning"
if grep -Fq -- ' commit /run/lockbox-verify-pins/test-cache.ok' "$CALL_LOG"; then
  fail "pre-protocol verifier committed a cache record"
fi
grep -Fq -- 'image verifier lacks cache protocol' "$TEST_TMP/old-warning" \
  || fail "pre-protocol cache skip was not reported"

: > "$CALL_LOG"
if FAKE_CACHE_HIT=0 FAKE_VERIFY_FAIL=1 run_helper; then
  fail "checker failure was accepted"
fi
grep -Fq -- ' invalidate /run/lockbox-verify-pins/test-cache.ok' "$CALL_LOG" || fail "failure did not invalidate cache"
if grep -Fq -- ' commit /run/lockbox-verify-pins/test-cache.ok' "$CALL_LOG"; then
  fail "checker failure committed a cache record"
fi

: > "$CALL_LOG"
FAKE_CACHE_HIT=0 FAKE_COMMIT_FAIL=1 run_helper 2> "$TEST_TMP/commit-warning"
grep -Fq -- 'pin verification passed, but its root-owned cache could not be written' "$TEST_TMP/commit-warning" \
  || fail "cache-write failure was not reported"

# These are literal snippets of the embedded container program.
# shellcheck disable=SC2016
for literal in \
  'lockbox-verify-pins-cache-v1' \
  '/proc/sys/kernel/random/boot_id' \
  'secure_chain "$verifier" && secure_chain "$pinfile"' \
  '[ -f "$sentinel" ] && [ ! -L "$sentinel" ]' \
  'chmod 0444 "$tmp"' \
  'mv -fT "$tmp" "$sentinel"'; do
  require_literal "$ROOT/launcher-common.sh" "$literal"
done

echo "verify-pins-cache-test: warm, cold, pre-protocol, failure, override, and commit paths passed"

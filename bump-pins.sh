#!/usr/bin/env bash
# Toolchain pin resolver / drift gate for LockBox.
#
# WHY THIS EXISTS: the sandbox images bake a Claude CLI, Node, Python and
# safe-chain. "Always run the latest" is NOT achievable safely at container
# runtime — a runtime `npm i -g` would (a) make the npm registry a trusted
# input inside the security boundary with no human in the loop, and (b) delete
# the launch-integrity gate (bin/verify-pins fails closed on SHA-256 drift, and
# you cannot pin a hash you do not know before the build). So "latest" is moved
# to BUILD time, behind a reviewed PR + a cooldown window.
#
# The pins live in ONE place (tool-pins.env). The Dockerfiles carry them as ARG
# defaults so a plain `container build` needs no extra flags; --check asserts the
# two never diverge (the drift class paths.sh exists to prevent, applied to the
# toolchain instead of the egress files).
#
# Modes:
#   --check          offline. Assert tool-pins.env == both Dockerfiles' ARG
#                    defaults. Exits non-zero on divergence. Pre-commit gate.
#   --report         network. Show pinned vs cooldown-eligible vs upstream latest
#                    for every tracked tool. Read-only; answers "are we stale?".
#   --write          network. Resolve the newest COOLDOWN-eligible version of each
#                    tool, download + hash the real artifacts, and rewrite
#                    tool-pins.env AND both Dockerfiles.
#   (no mode)        dry-run: same resolution as --write, but prints the diff it
#                    WOULD apply and writes nothing.
#
# COOLDOWN: a release younger than COOLDOWN_DAYS is never selected. Nearly every
# recent npm compromise (chalk/debug, nx, shai-hulud) was detected and yanked
# within a day; waiting a week costs a few days of features and removes almost
# all of the zero-day-publish exposure. This is the highest-value control here.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PINS="$HERE/tool-pins.env"

# Targets come from paths.sh — the SAME list sync.sh and audit.sh use, so adding a
# managed container is still a one-line edit in one file and its toolchain pins are
# picked up automatically. (Before this, the pins were hand-duplicated per repo:
# all nine Dockerfiles happened to agree, but nothing asserted it.)
# shellcheck source=paths.sh
source "$HERE/paths.sh"
DOCKERFILES=()
MISSING=()
for _d in "${EGRESS_DEVCONTAINERS[@]}"; do
  if [[ -f "$_d/Dockerfile" ]]; then DOCKERFILES+=("$_d/Dockerfile")
  else MISSING+=("$_d")
  fi
done
(( ${#DOCKERFILES[@]} > 0 )) || { echo "bump-pins: no managed Dockerfiles found (check paths.sh)" >&2; exit 1; }

MODE=dryrun
for arg in "$@"; do
  case "$arg" in
    --check)   MODE=check ;;
    --report)  MODE=report ;;
    --write)   MODE="write" ;;
    -h|--help) sed -n '2,32p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "bump-pins: unknown arg '$arg' (use --check, --report, --write)" >&2; exit 2 ;;
  esac
done

[[ -f "$PINS" ]] || { echo "bump-pins: missing $PINS" >&2; exit 1; }
# shellcheck source=tool-pins.env disable=SC1091
source "$PINS"

: "${COOLDOWN_DAYS:=7}"

# Portable SHA-256 (Linux CI has sha256sum; macOS host has shasum).
sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

# Read an ARG default out of a Dockerfile (e.g. arg_of FILE NODE_VERSION).
arg_of() { sed -n "s/^ARG ${2}=\(.*\)$/\1/p" "$1" | head -1; }

# Short display name for a Dockerfile path: "<project>/.devcontainer/Dockerfile".
# Targets now span sibling repos, so a $HERE-relative strip is not enough.
label() { local d; d="$(dirname "$1")"; echo "$(basename "$(dirname "$d")")/$(basename "$d")/Dockerfile"; }

# ---------------------------------------------------------------------------
# --check — offline drift gate: tool-pins.env must equal both Dockerfiles.
# ---------------------------------------------------------------------------
if [[ "$MODE" == check ]]; then
  rc=0
  # apple/container size ceiling for Dockerfiles. Its DOCUMENTED limit is 16384
  # (https://github.com/apple/container/issues/735) and it rejects >16384 with a
  # clear invalidArgument — but in practice the builder CRASHES well before that,
  # with the opaque and undiagnosable:
  #     Error: unavailable: "Stream unexpectedly closed."
  # and zero build output. Established by bisecting this repo's own Dockerfile on
  # 2026-07-19: 12267 / 14268 / 14306 / 14801 bytes all build; 15307 / 15320 /
  # 15473 / 15480 / 15663 / 15690 / 15739 all fail that way. The threshold is
  # somewhere in 14801..15307, so gate on the largest PROVEN-GOOD size. If you
  # ever raise this, re-bisect — do not trust the documented 16 KiB.
  DOCKERFILE_MAX_BYTES=14801
  for df in "${DOCKERFILES[@]}"; do
    [[ -f "$df" ]] || continue
    sz=$(wc -c < "$df" | tr -d ' ')
    if (( sz > DOCKERFILE_MAX_BYTES )); then
      echo "TOO BIG: $(label "$df") is ${sz} bytes (> ${DOCKERFILE_MAX_BYTES}); apple/container will fail with \"Stream unexpectedly closed\". Trim comments." >&2
      rc=1
    elif (( sz > DOCKERFILE_MAX_BYTES - 512 )); then
      echo "WARN: $(label "$df") is ${sz} bytes — only $((DOCKERFILE_MAX_BYTES - sz)) left before apple/container's practical build ceiling." >&2
    fi
  done
  # REQUIRED in every managed Dockerfile. Node/Python verify themselves against
  # upstream SHASUMS at build, so only their versions are mirrored; claude needs the
  # version AND the per-arch binary hash (see the header).
  MIRRORED=(NODE_VERSION PY_RELEASE PY_VERSION CLAUDE_CODE_VERSION
            CLAUDE_CODE_SHA256_ARM64 CLAUDE_CODE_SHA256_AMD64)
  # OPTIONAL: only the images that BAKE safe-chain declare this. The rest still
  # install it at runtime in post-create (unpinned) — tracked as a follow-up, since
  # baking it in each repo also means editing that repo's post-create.sh. Where the
  # ARG is present it must match; where it is absent it is not an error.
  OPTIONAL_MIRRORED=(SAFE_CHAIN_VERSION)
  for df in "${DOCKERFILES[@]}"; do
    [[ -f "$df" ]] || { echo "bump-pins: MISSING Dockerfile $df" >&2; rc=1; continue; }
    for k in "${MIRRORED[@]}"; do
      want="${!k:-}"
      got="$(arg_of "$df" "$k")"
      if [[ -z "$got" ]]; then
        echo "DRIFT: $(label "$df") has no 'ARG $k=' (expected $want)" >&2; rc=1
      elif [[ "$got" != "$want" ]]; then
        echo "DRIFT: $(label "$df") $k=$got but tool-pins.env says $want" >&2; rc=1
      fi
    done
    for k in "${OPTIONAL_MIRRORED[@]}"; do
      want="${!k:-}"
      got="$(arg_of "$df" "$k")"
      if [[ -n "$got" && "$got" != "$want" ]]; then
        echo "DRIFT: $(label "$df") $k=$got but tool-pins.env says $want" >&2; rc=1
      fi
    done
  done
  if (( ${#MISSING[@]} > 0 )); then
    # Not fatal: sibling fleet repos are absent in CI and on other machines. sync.sh
    # treats a missing target as fatal because a dropped container silently loses the
    # egress lock; a missing Dockerfile here only means its pins were not checked.
    echo "note: ${#MISSING[@]} managed devcontainer(s) not present on this machine; pins unchecked for them." >&2
  fi
  if (( rc == 0 )); then
    echo "✓ pins: tool-pins.env matches all ${#DOCKERFILES[@]} Dockerfiles."
  else
    echo "✗ pins: Dockerfile ARG defaults diverged from tool-pins.env. Run ./bump-pins.sh --write, or hand-fix." >&2
  fi
  exit "$rc"
fi

# ---------------------------------------------------------------------------
# Resolution helpers (network). Used by --report / --write / dry-run.
# ---------------------------------------------------------------------------

# npm_pick <pkg> -> "<cooldown-eligible-version> <latest-version> <latest-date>"
# Picks the highest semver whose publish time is at least COOLDOWN_DAYS old.
# Prereleases are ignored. Fails loudly rather than silently falling back.
npm_pick() {
  local pkg="$1" meta
  meta="$(mktemp)"
  curl -fsSLo "$meta" "https://registry.npmjs.org/${pkg}" \
    || { echo "bump-pins: ERROR fetching registry metadata for $pkg" >&2; rm -f "$meta"; exit 1; }
  COOLDOWN_DAYS="$COOLDOWN_DAYS" python3 - "$meta" <<'PY'
import json, os, sys, datetime
d = json.load(open(sys.argv[1]))
cooldown = int(os.environ["COOLDOWN_DAYS"])
now = datetime.datetime.now(datetime.timezone.utc)

def key(v):
    # numeric semver sort; anything with a prerelease suffix is skipped upstream
    return tuple(int(x) for x in v.split("."))

rows = []
for v, t in d.get("time", {}).items():
    if v in ("created", "modified") or v not in d.get("versions", {}):
        continue
    if not all(p.isdigit() for p in v.split(".")):   # skip 1.2.3-beta.1 etc
        continue
    ts = datetime.datetime.fromisoformat(t.replace("Z", "+00:00"))
    rows.append((key(v), v, ts))
rows.sort()
if not rows:
    sys.exit("no stable versions found")
latest = rows[-1]
eligible = [r for r in rows if (now - r[2]).days >= cooldown]
if not eligible:
    sys.exit(f"no version older than {cooldown}d")
pick = eligible[-1]
print(pick[1], latest[1], latest[2].date(), (now - pick[2]).days)
PY
  rm -f "$meta"
}

# The Claude CLI npm package is a THIN WRAPPER (~20 KB): the real executable
# ships in a per-platform optionalDependency (@anthropic-ai/claude-code-linux-
# arm64/-x64) and its postinstall copies that binary over bin/claude.exe. So
# hashing the wrapper tarball would pin almost nothing. Instead we pin the
# SHA-256 of the actual native binary, per arch, and the Dockerfile asserts it
# right after install — which is exactly what a substituted platform package
# would change. (install.cjs does no network I/O; it only copies.)
claude_binary_sha() {
  local version="$1" plat="$2" tgz dir out
  tgz="$(mktemp)"; dir="$(mktemp -d)"
  curl -fsSLo "$tgz" \
    "https://registry.npmjs.org/@anthropic-ai/claude-code-${plat}/-/claude-code-${plat}-${version}.tgz" \
    || { echo "bump-pins: ERROR fetching claude-code-${plat}@${version}" >&2; rm -rf "$tgz" "$dir"; exit 1; }
  tar -xzf "$tgz" -C "$dir"
  # The platform package ships exactly one executable payload.
  out="$(find "$dir" -type f -name 'claude*' -size +1M | head -1)"
  [[ -n "$out" ]] || { echo "bump-pins: ERROR no binary found in claude-code-${plat}@${version}" >&2; rm -rf "$tgz" "$dir"; exit 1; }
  sha256_of "$out"
  rm -rf "$tgz" "$dir"
}

echo "Resolving pins (cooldown ${COOLDOWN_DAYS}d)..." >&2
read -r CC_PICK CC_LATEST CC_LATEST_DATE CC_AGE < <(npm_pick "@anthropic-ai/claude-code")
read -r SC_PICK SC_LATEST SC_LATEST_DATE SC_AGE < <(npm_pick "@aikidosec/safe-chain")

# Node: official release index. Report the newest release WITHIN THE PINNED MAJOR
# LINE — crossing a major (24 -> 26) is a deliberate platform decision, not drift,
# and flagging it as "BEHIND" would train the reader to ignore this report. The
# newest major is surfaced separately as information only.
NODE_IDX="$(mktemp)"
curl -fsSLo "$NODE_IDX" https://nodejs.org/dist/index.json \
  || { echo "bump-pins: ERROR fetching nodejs.org release index" >&2; exit 1; }
read -r NODE_LATEST NODE_NEWEST_MAJOR < <(
  NODE_VERSION="$NODE_VERSION" python3 - "$NODE_IDX" <<'PY'
import json, os, sys
rels = json.load(open(sys.argv[1]))
pinned_major = os.environ["NODE_VERSION"].split(".")[0]
def ver(r): return r["version"].lstrip("v")
def key(v): return tuple(int(x) for x in v.split("."))
same = [ver(r) for r in rels if ver(r).split(".")[0] == pinned_major]
newest_major = max({ver(r).split(".")[0] for r in rels}, key=int)
print(max(same, key=key) if same else "unknown", newest_major)
PY
)
rm -f "$NODE_IDX"

# ---------------------------------------------------------------------------
# --report — staleness surface. Answers the project question "are we current?"
# ---------------------------------------------------------------------------
if [[ "$MODE" == report ]]; then
  printf '\n%-22s %-12s %-12s %-12s %s\n' TOOL PINNED ELIGIBLE LATEST NOTE
  printf '%-22s %-12s %-12s %-12s %s\n' ---- ------ -------- ------ ----
  note() { [[ "$1" == "$2" ]] && echo "current" || echo "BEHIND"; }
  printf '%-22s %-12s %-12s %-12s %s\n' claude-code "$CLAUDE_CODE_VERSION" "$CC_PICK" "$CC_LATEST" \
    "$(note "$CLAUDE_CODE_VERSION" "$CC_PICK") (latest published $CC_LATEST_DATE)"
  printf '%-22s %-12s %-12s %-12s %s\n' safe-chain "$SAFE_CHAIN_VERSION" "$SC_PICK" "$SC_LATEST" \
    "$(note "$SAFE_CHAIN_VERSION" "$SC_PICK") (latest published $SC_LATEST_DATE)"
  printf '%-22s %-12s %-12s %-12s %s\n' "node (${NODE_VERSION%%.*}.x)" "$NODE_VERSION" - "$NODE_LATEST" \
    "$(note "$NODE_VERSION" "$NODE_LATEST") · newest major upstream is ${NODE_NEWEST_MAJOR}.x (major bumps are a deliberate decision, not drift)"
  printf '%-22s %-12s %-12s %-12s %s\n' python "$PY_VERSION" - - "release $PY_RELEASE (bump by hand; self-verifies via SHA256SUMS)"
  printf '%-22s %-12s %-12s %-12s %s\n' gh - - - "unpinned (apt; see Dockerfile note)"
  echo
  echo "Pinned versions are deliberate. Bump with: ./bump-pins.sh --write  (then review the PR/diff, make check, make test)"
  exit 0
fi

# ---------------------------------------------------------------------------
# --write / dry-run — resolve, hash, rewrite.
# ---------------------------------------------------------------------------
if [[ "$CC_PICK" == "$CLAUDE_CODE_VERSION" \
   && "$SC_PICK" == "$SAFE_CHAIN_VERSION" \
   && "$NODE_LATEST" == "$NODE_VERSION" ]]; then
  echo "Already at the newest cooldown-eligible pins (claude-code $CC_PICK, safe-chain $SC_PICK, node $NODE_VERSION). Nothing to do."
  exit 0
fi

[[ "$CC_PICK"     != "$CLAUDE_CODE_VERSION" ]] && echo "claude-code : $CLAUDE_CODE_VERSION -> $CC_PICK  (${CC_AGE}d old; latest is $CC_LATEST, held by cooldown)"
[[ "$SC_PICK"     != "$SAFE_CHAIN_VERSION"  ]] && echo "safe-chain  : $SAFE_CHAIN_VERSION -> $SC_PICK  (${SC_AGE}d old; latest is $SC_LATEST)"
# Node stays WITHIN the pinned major line — a major bump changes the platform and
# must be a human decision, so it is reported but never auto-applied. No separate
# hash pin is needed: the Dockerfiles verify the tarball against nodejs.org's own
# SHASUMS256.txt for this exact version at build time.
[[ "$NODE_LATEST" != "$NODE_VERSION"        ]] && echo "node        : $NODE_VERSION -> $NODE_LATEST  (within ${NODE_VERSION%%.*}.x; newest major upstream ${NODE_NEWEST_MAJOR}.x NOT auto-applied)"
true

if [[ "$CC_PICK" != "$CLAUDE_CODE_VERSION" ]]; then
  echo "Hashing claude native binaries (~70 MB per arch)..." >&2
  NEW_CC_ARM64="$(claude_binary_sha "$CC_PICK" linux-arm64)"
  NEW_CC_AMD64="$(claude_binary_sha "$CC_PICK" linux-x64)"
else
  NEW_CC_ARM64="$CLAUDE_CODE_SHA256_ARM64"
  NEW_CC_AMD64="$CLAUDE_CODE_SHA256_AMD64"
fi
echo "  claude linux-arm64 sha256: $NEW_CC_ARM64"
echo "  claude linux-x64   sha256: $NEW_CC_AMD64"

if [[ "$MODE" != write ]]; then
  echo
  echo "DRY RUN — nothing written. Re-run with --write to apply."
  exit 0
fi

# Rewrite tool-pins.env in place (values are alnum/dot/hex — sed-safe).
set_pin() {
  local key="$1" val="$2" tmp
  tmp="$(mktemp "$HERE/.tool-pins.XXXXXX")"
  sed "s|^${key}=.*$|${key}=${val}|" "$PINS" > "$tmp"
  grep -q "^${key}=${val}$" "$tmp" || { echo "bump-pins: ERROR failed to set $key" >&2; rm -f "$tmp"; exit 1; }
  chmod 0644 "$tmp"; mv "$tmp" "$PINS"
}
set_pin CLAUDE_CODE_VERSION      "$CC_PICK"
set_pin CLAUDE_CODE_SHA256_ARM64 "$NEW_CC_ARM64"
set_pin CLAUDE_CODE_SHA256_AMD64 "$NEW_CC_AMD64"
set_pin SAFE_CHAIN_VERSION       "$SC_PICK"
set_pin NODE_VERSION             "$NODE_LATEST"

# Mirror into both Dockerfiles so a plain `container build` needs no --build-arg.
for df in "${DOCKERFILES[@]}"; do
  [[ -f "$df" ]] || continue
  tmp="$(mktemp "$(dirname "$df")/.Dockerfile.XXXXXX")"
  sed -e "s|^ARG CLAUDE_CODE_VERSION=.*$|ARG CLAUDE_CODE_VERSION=${CC_PICK}|" \
      -e "s|^ARG CLAUDE_CODE_SHA256_ARM64=.*$|ARG CLAUDE_CODE_SHA256_ARM64=${NEW_CC_ARM64}|" \
      -e "s|^ARG CLAUDE_CODE_SHA256_AMD64=.*$|ARG CLAUDE_CODE_SHA256_AMD64=${NEW_CC_AMD64}|" \
      -e "s|^ARG SAFE_CHAIN_VERSION=.*$|ARG SAFE_CHAIN_VERSION=${SC_PICK}|" \
      -e "s|^ARG NODE_VERSION=.*$|ARG NODE_VERSION=${NODE_LATEST}|" \
      "$df" > "$tmp"
  chmod 0644 "$tmp"; mv "$tmp" "$df"
  echo "updated $(label "$df")"
done

echo
"$HERE/bump-pins.sh" --check
echo "Next: review the diff, then \`make test\` (boots the image and re-asserts the egress lock) before committing."

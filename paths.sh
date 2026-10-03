#!/usr/bin/env bash
# Shared target discovery for sync.sh and bump-pins.sh.
# Personal deployment paths belong in the ignored paths.local.tsv data file.

EGRESS_REPO="${EGRESS_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)}"
EGRESS_DEVCONTAINERS=("$EGRESS_REPO/.devcontainer" "$EGRESS_REPO/sandbox/.devcontainer")
EGRESS_PROJECT_LABELS=(LockBox sandbox)

# CI checks only repository-owned targets and never reads host deployment data.
if [[ "${EGRESS_SELF_ONLY:-0}" != 1 ]]; then
  _fleet_file="${EGRESS_FLEET_FILE:-$EGRESS_REPO/paths.local.tsv}"
  if [[ -n "${EGRESS_FLEET_FILE:-}" && ! -f "$_fleet_file" ]]; then
    echo "paths: configured fleet file is missing: $_fleet_file" >&2
    return 1
  fi
  if [[ -f "$_fleet_file" ]]; then
    while IFS= read -r _fleet_line || [[ -n "$_fleet_line" ]]; do
      [[ -n "$_fleet_line" && "$_fleet_line" != \#* ]] || continue
      _fleet_path="${_fleet_line%%$'\t'*}"
      _fleet_label=""
      if [[ "$_fleet_line" == *$'\t'* ]]; then
        _fleet_label="${_fleet_line#*$'\t'}"
      fi
      if [[ "$_fleet_path" != /* || "$_fleet_path" != */.devcontainer ||
            "$_fleet_label" == *$'\t'* || "$_fleet_label" == */* ]]; then
        echo "paths: invalid fleet entry; expected absolute .devcontainer path and optional label" >&2
        return 1
      fi
      for _fleet_existing in "${EGRESS_DEVCONTAINERS[@]}"; do
        if [[ "$_fleet_existing" == "$_fleet_path" ]]; then
          echo "paths: duplicate fleet target: $_fleet_path" >&2
          return 1
        fi
      done
      [[ -n "$_fleet_label" ]] || _fleet_label="$(basename "$(dirname "$_fleet_path")")"
      EGRESS_DEVCONTAINERS+=("$_fleet_path")
      EGRESS_PROJECT_LABELS+=("$_fleet_label")
    done < "$_fleet_file"
  fi
fi
EGRESS_LABEL_TARGETS=("${EGRESS_DEVCONTAINERS[@]}")
unset _fleet_file _fleet_line _fleet_path _fleet_label _fleet_existing

# Explicit labels keep generated content stable across differently named checkouts.
egress_project_label() {
  local dst="$1" i
  for i in "${!EGRESS_LABEL_TARGETS[@]}"; do
    if [[ "$dst" == "${EGRESS_LABEL_TARGETS[$i]}" ]]; then
      printf '%s\n' "${EGRESS_PROJECT_LABELS[$i]}"
      return
    fi
  done
  echo "paths: target has no registered label: $dst" >&2
  return 1
}

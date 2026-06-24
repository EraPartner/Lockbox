#!/usr/bin/env bash
# /usr/local/sbin/dev-sandbox-perms-fix
#
# Image-baked helper that performs ONLY the ownership / permission repairs the
# sandbox needs at start time. Invoked by the root ENTRYPOINT (the container has
# no sudo — it runs with no-new-privileges, and all privileged setup happens in
# the entrypoint). The repo copy at .devcontainer/perms-fix.sh is the source.
#
# Takes no arguments and performs no caller-parameterised operations.

set -euo pipefail

fix_dir_owner() {
  local dir="$1"
  local owner="$2"
  if [[ -d "$dir" ]] && [[ "$(stat -c %U "$dir")" != "$owner" ]]; then
    chown -R "$owner:$owner" "$dir"
  fi
}

# Named-volume mountpoints come up as root:root on first mount, regardless of the
# image-side directory perms. Repair to dev ownership so dev can write.
fix_dir_owner /home/dev/.claude  dev
fix_dir_owner /home/dev/.config  dev
fix_dir_owner /home/dev/.local   dev

exit 0

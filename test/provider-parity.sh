#!/usr/bin/env bash
# Static architecture check for the two supported model providers. This does not
# authenticate either CLI; it prevents provider wrappers and lifecycle gates from
# drifting away from the shared launchers and shared image guarantees.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$ROOT"

fail() { echo "provider-parity: $*" >&2; exit 1; }
require_literal() {
  local file="$1" literal="$2"
  grep -Fq -- "$literal" "$file" || fail "$file is missing: $literal"
}

# The tiny public entry points should remain structurally identical; provider
# selection is their only behavioral difference.
normalize_wrapper() { sed -e 's/Claude/CODE_PROVIDER/g' -e 's/claude/CODE_PROVIDER/g' -e 's/Codex/CODE_PROVIDER/g' -e 's/codex/CODE_PROVIDER/g' "$1"; }
cmp -s <(normalize_wrapper .devcontainer/bin/claude) <(normalize_wrapper .devcontainer/bin/codex) \
  || fail "LockBox provider wrappers have drifted"
cmp -s <(normalize_wrapper sandbox/.devcontainer/bin/dev-claude) <(normalize_wrapper sandbox/.devcontainer/bin/dev-codex) \
  || fail "generic sandbox provider wrappers have drifted"

for lifecycle in .devcontainer/post-create.sh .devcontainer/post-start.sh \
                 sandbox/.devcontainer/post-create.sh sandbox/.devcontainer/post-start.sh; do
  require_literal "$lifecycle" 'claude|codex)'
done

# Both launchers must use the canonical Codex authentication helper rather than
# growing separate credential-handling implementations again.
require_literal .devcontainer/bin/agent 'sandbox_ensure_codex_login'
require_literal sandbox/.devcontainer/bin/dev 'sandbox_ensure_codex_login'
require_literal launcher-common.sh 'sandbox_ensure_codex_login()'
require_literal launcher-common.sh 'refusing unsanitized ~/.claude.json because jq is unavailable'
if grep -Fq 'cp "$HOME/.claude.json"' launcher-common.sh; then
  fail "Claude staging retains a raw ~/.claude.json fallback"
fi

# One shared image installs and launch-pins both CLIs; each provider retains an
# isolated writable config volume and its required API endpoint.
for dockerfile in .devcontainer/Dockerfile sandbox/.devcontainer/Dockerfile; do
  require_literal "$dockerfile" 'npm install -g --no-fund --no-audit "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"'
  require_literal "$dockerfile" 'npm install -g --no-fund --no-audit "@openai/codex@${CODEX_VERSION}"'
  require_literal "$dockerfile" 'node npm claude codex bwrap gh git python3 safe-chain'
done
require_literal .devcontainer/bin/agent 'lockbox-claude:/home/dev/.claude'
require_literal .devcontainer/bin/agent 'lockbox-codex:/home/dev/.codex'
require_literal sandbox/.devcontainer/bin/dev 'dev-sandbox-claude-$HASH:/home/dev/.claude'
require_literal sandbox/.devcontainer/bin/dev 'dev-sandbox-codex-$HASH:/home/dev/.codex'
for allowlist in .devcontainer/allowlist.txt sandbox/.devcontainer/allowlist.txt; do
  require_literal "$allowlist" 'api.anthropic.com'
  require_literal "$allowlist" 'api.openai.com'
done

echo "provider-parity: Claude and Codex share launch, lifecycle, image, pin, and egress architecture"

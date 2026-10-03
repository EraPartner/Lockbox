# Provider configuration

The two in-repo sandboxes share configuration policy and isolation controls. Each
provider still uses its own native CLI, settings format, login and private volume.
Sibling launchers keep their existing staging policy until explicitly adapted.

## Common authored source

`LOCKBOX_CONFIG_SOURCE` applies to both launchers and defaults to
`~/.config/lockbox/agents`. This directory is authored input, not a live container
home. Its supported layout is:

```text
agents/
  AGENTS.md
  skills/<skill-name>/SKILL.md
  mcp.json
  claude/<allowed native entry>
  codex/<allowed native entry>
```

Only these native entries are eligible for import from the corresponding
common-source subdirectory:

| Provider | Authored entries |
|---|---|
| Claude | `settings.json`, `keybindings.json`, `agents/`, `rules/`, `commands/`, `skills/`, `status-line.sh`, `statusline/`, `plugins/` |
| Codex | `config.toml`, `AGENTS.md`, `agents/` |

The default reads only the authored common source. To include selected host native
entries, set `LOCKBOX_CONFIG_IMPORT_NATIVE=1`. This compatibility option considers
the exact list above under `~/.claude` or `~/.codex`, plus `~/.agents/skills`.
Source provider entries override matching host entries; shared `skills/` overlays
both. Common `AGENTS.md` supplies global
instructions for both providers. Claude receives a private native `CLAUDE.md`
adapter; Codex receives `AGENTS.md`.

With native import enabled, an absent common file falls back to
`~/.agents/AGENTS.md`, then `~/.codex/AGENTS.md`. Native host `CLAUDE.md` wrappers
are not imported. The default has no host instruction fallback.

Host Claude global `.claude.json` is never read. Declare shared MCP servers in
`mcp.json`; the importer creates native MCP configuration. Native Codex MCP
configuration can remain in its curated `config.toml`. Native options remain
provider-specific: shared input does not translate permission rules, subagent
semantics, plugins or every provider feature.

Host Python 3.11 or newer is required on every launch. The importer
materializes symlinks only when their resolved targets stay within curated roots.
It refuses broken links, cycles, private paths and escapes. A shared skill linked
to a separate checkout outside those roots must be copied into an authored root.
Credentials, `.env` files, local settings, sessions, history, projects and memory
are excluded. Imported settings remove user hooks and host trust/account metadata;
inline credential/environment fields are rejected. Plugin metadata is retained,
but host paths and macOS tools may not work in Linux. Both images bake the
root-owned Claude guard and managed settings. No host guard directory is required;
launch rejects a missing or stale baked policy and requires a rebuild.

## Initialization and explicit refresh

Configuration preparation runs after the container lifecycle barrier. First
initialization fills missing files and merges native configuration objects with
existing private values winning conflicts. Existing private skills and authored
files are preserved. Ordinary launches use the private configuration without
restaging host input.

After changing authored configuration, refresh deliberately:

```sh
LOCKBOX_CONFIG_REFRESH=1 /path/to/LockBox/.devcontainer/bin/claude
LOCKBOX_CONFIG_REFRESH=1 /path/to/LockBox/.devcontainer/bin/codex
# From the target project directory:
DEV_SANDBOX_CONFIG_REFRESH=1 /path/to/LockBox/sandbox/.devcontainer/bin/dev-claude
DEV_SANDBOX_CONFIG_REFRESH=1 /path/to/LockBox/sandbox/.devcontainer/bin/dev-codex
```

Refresh replaces present authored files and settings. Each present MCP server is
replaced as a whole, so removed arguments, environment or transport fields do not
linger. `.lockbox-config-managed.json` records ownership. Previously imported files
and settings absent from the new snapshot are removed only if their private values
still match that record. Locally changed entries remain private and a warning
reports the count. Private entries and imports predating the ownership record are
never claimed merely because they match authored input.

Replaced and deleted files get backups under the native directory's
`.lockbox-config-backup-*`; a failed publication restores files, ownership and the
readiness marker. Identical refreshes do not rewrite files or create backups. The
importer retains five completed backups that carry its ownership marker. Failed,
pending and unrecognized backups are preserved. An imported plugin can change
between a directory and a file only when all affected contents are unchanged
imports; private contents block the transition.

An interrupted import or incomplete rollback leaves
`.lockbox-config-transaction.json` and blocks further startup, even if an old
readiness marker exists. Close the profile's sessions, inspect the journal's
referenced backup, and restore the affected private files, ownership metadata and
readiness state inside the container. Review newly created files as well as backup
contents. Remove the journal only after the private profile is consistent. Do not
copy private backups to the host. Container import errors use fixed categories
without printing authored configuration values.

Host startup locks serialize policy changes, container lifecycle and imports for
each profile. They release before native login and the interactive session; file
locks also release on process exit. They do not prevent a running agent from
changing its private settings; refresh with that profile's other sessions closed.
Writable workspace roots must not overlap the host staging tree. Proxy readiness
runs in a shell that skips private startup files, so a login profile cannot skip
the check. The generic sandbox mounts a separate `agent-input` directory beside
its allowlist and inbound-port files; overlapping source mounts are unreliable in
Apple Container. Old mount layouts require container recreation. A missing host
snapshot is recreated for environment authorization even when the private profile
is already initialized; this does not refresh its private configuration.
There is no configuration export or host-memory copy-back.

The generic launcher keeps desired allowlist bytes separate from confirmed policy.
Changed or unconfirmed policy requires a successful container stop, followed by a
fresh start and proxy readiness check. Confirmation records the mounted bytes and
container generation. A failed transition cannot authorize an identical retry.
An unchanged confirmed policy reuses the warm container. A policy restart can
interrupt other sessions using that same container; close them before changing
its allowlist. Both launchers abort if the proxy does not become ready within
30 seconds. The generic doctor also checks direct egress and refuses Git push
credentials, matching the scoped doctor's isolation checks.

Claude keeps global state under `CLAUDE_CONFIG_DIR=/home/dev/.claude`. Codex uses
`CODEX_HOME=/home/dev/.codex` and `cli_auth_credentials_store = "file"`. Recreating
a container retains its named volumes. Old mount/configuration policy fingerprints
are refused with a rebuild hint. No legacy container global-file migration is part
of this policy.

## Native login

On the first interactive launch, Claude runs `claude auth login`; Codex runs
`codex login --device-auth`. Open the displayed URL in your host browser. Claude
may ask you to paste a returned code when a browser callback cannot reach the
container. Codex device login must be allowed by the account or workspace. Later
launches check the native login status and reuse the private cache.

Explicit environment authentication remains available:

| Provider | Variables | Behavior |
|---|---|---|
| Claude | `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` | Explicit per-process authentication when no private login is available |
| Codex | `OPENAI_API_KEY` or enterprise `CODEX_ACCESS_TOKEN` | Bootstrap through the matching official login command |

The in-repo launchers do not read Keychain, copy host login caches or bake provider
credentials into an image. `claude setup-token` prints a token; it does not create
the native persistent login used here. A noninteractive first launch requires an
existing private cache or supported environment authentication. An agent can read
its own credential; the private volume isolates it from the host, not from the
agent. Revoke a session if the sandbox is compromised.

## Shared MCP declarations

Model Context Protocol (MCP) servers use a direct name-to-server map in `mcp.json`.
Server names contain letters, digits, underscores or hyphens. Supported transports
are `stdio` and HTTPS `http`; no inline secrets belong in this file.

```json
{
  "local_tools": {
    "type": "stdio",
    "command": "example-mcp-server",
    "args": ["--mode", "read-only"],
    "env_vars": ["EXAMPLE_API_KEY"]
  },
  "remote_tools": {
    "type": "http",
    "url": "https://mcp.example.invalid/mcp",
    "bearer_token_env_var": "EXAMPLE_MCP_TOKEN",
    "env_http_headers": {"X-Tenant": "EXAMPLE_TENANT"}
  }
}
```

For `stdio`, allowed fields are `type`, `command`, `args`, and `env_vars`; omitting
`type` means `stdio`. For `http`, allowed fields are `type`, `url`,
`bearer_token_env_var`, and `env_http_headers`. URLs cannot contain credentials,
queries or fragments. Environment references use uppercase names. The launcher
takes the environment-name allowlist from the read-only host snapshot, not
agent-writable private configuration, and forwards declared names present in its
environment. It reports unset names without printing values. Protected execution
variables such as `PATH`, `HOME`, `BASH_ENV`, `SSH_AUTH_SOCK`, provider home
overrides and Git push credentials such as `GH_TOKEN`/`GITHUB_TOKEN` are refused.
Private configuration edits cannot authorize forwarding another host variable.
The importer itself lives in the protected `.devcontainer` launcher tree.

The adapter renders Claude environment/header substitutions and Codex environment
references. The launch report checks whether a stdio command exists; it does not
install it or prove the server works. Install required Linux commands in the image.
HTTP endpoints and any network access by stdio servers remain subject to the
allowlist. Adding a declaration never widens egress. Native-only MCP options should
stay in the corresponding curated provider configuration.

## Instructions, skills and verification limits

Project instructions use `AGENTS.md`. The pinned Claude CLI supports its builtin
`agents-md@builtin`; imported settings enable it with
`instructionFiles = "claude-md-and-agents-md"`, so a project Claude instruction
file does not suppress `AGENTS.md`. Codex reads its native AGENTS hierarchy.
Claude does not share Codex's `AGENTS.override.md` discovery semantics. Global
Claude instructions still use the native `CLAUDE.md` adapter inside the volume.

Skills share their Markdown body and standard `SKILL.md` metadata. Native skill
options and execution behavior can differ. Native settings, plugin metadata and
subagent definitions remain distinct; retaining a file does not prove a feature
is enabled or supported by the other CLI.

The launchers protect top-level and nested `.devcontainer` directories, Git state,
effective hooks paths and aliases with read-only overlays. Nested Git repositories,
submodules, unsupported linked layouts and mutable workspace Git includes are refused.
Both providers enforce the same strict pin gate
and forward piped input. These controls do not make all provider features equal.

Safe Chain's writable startup script is passed only to the final `dev` process.
It is absent from the container creation environment, so root startup does not
execute a script that an earlier agent session could modify.

`make test` builds and boots a disposable image and checks both providers.
After building the generic test image, `make native-launcher-test` exercises real
fresh starts, confirmed warm reuse, failed-stop retry and policy narrowing with
both providers on Apple Container. It uses disposable configuration and shells,
without account login. The readiness regression also checks a private login
profile cannot skip the trusted proxy check.
`IMAGE_KIND=lockbox make test` selects the scoped image. The native feature fixture
uses fresh profiles, a local MCP server and simulated model endpoints. Both CLIs
must discover the imported shared skill, connect MCP and load shared global guidance
plus project `AGENTS.md`. Claude also loads an imported local plugin through
`--plugin-dir`; this does not certify arbitrary marketplace metadata or plugins.
The fixture never uses an account credential or a live model response. Claude's
baked managed policy must match the guard and remain unwritable by `dev`.

Before deployment, rebuild the affected images, run each doctor with a real
private login and exercise both first-login flows. Those account checks are separate
from the disposable fixture. Squid's helper rejects absent or malformed visible SNI and ECH,
including GREASE, without decrypting TLS. The booted egress fixture checks CONNECT/SNI
equality through Squid using different allowlisted names. The generated firewall installs the root-owned companion before its readiness
sentinel; existing fleet Dockerfile firewall COPY integration is sufficient. Successful fleet
sync and image rebuilds are required before deployment. Portable fixtures do not prove these runtime results.

Provider references: [Claude authentication](https://code.claude.com/docs/en/authentication),
[Claude instructions](https://code.claude.com/docs/en/memory),
[Claude skills](https://code.claude.com/docs/en/skills),
[Claude MCP](https://code.claude.com/docs/en/mcp),
[Codex authentication](https://developers.openai.com/codex/auth),
[Codex instructions](https://developers.openai.com/codex/guides/agents-md),
[Codex skills](https://developers.openai.com/codex/skills), and
[Codex MCP](https://developers.openai.com/codex/mcp).

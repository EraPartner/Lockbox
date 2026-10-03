# Generic universal full-dev sandbox

One hardened container image, **any** project. Run `dev-claude` or `dev-codex` from inside any
directory and it routes *that* directory into an egress-locked sandbox and opens
Claude Code or OpenAI Codex there — no per-project `.devcontainer` required. This is the full-dev
counterpart to the commit-only `git-agent`: same security model, but the
workspace is **read-write** so you can edit and run code.

Use it for projects that don't need their own sandbox. Projects that require a
specific service stack can keep their own `.devcontainer`.

> **Not a VS Code devcontainer.** Despite living under `.devcontainer/`, there is
> **no** `devcontainer.json` here — this is an Apple `container` (`container build`
> + `container run`) image built and driven entirely by `bin/dev`. VS Code
> "Reopen in Container" is not supported; the `bin/dev-claude` and `bin/dev-codex`
> wrappers select a provider and invoke the shared `bin/dev` implementation.

## Usage

```sh
cd ~/Code/my-project
dev-claude                    # Claude, against the current project, RW
dev-codex                     # Codex with ChatGPT/device-code login
dev-codex --version           # args forward to Codex
DEV_SANDBOX_AGENT=codex dev   # equivalent environment-based selection
DEV_SANDBOX_PORTS="8787" dev-codex   # publish a container port to localhost
DEV_SANDBOX_SHELL=1 dev-codex        # bash shell with Codex's container state
```

Each target directory and provider gets its **own** container and private agent
volume (keyed by a hash of its path), so projects do not share agent state. The
launcher prints the selected provider and refuses stale containers whose saved
provider does not match or whose mount/configuration policy fingerprint is stale.
Both CLIs remain installed and integrity-checked; that does not make both providers
active in one session.

Plain `dev` defaults to Claude. Use `dev-codex` or `DEV_SANDBOX_AGENT=codex`
for Codex; invalid provider names still fail before startup.

## Mounted files and private state

| Thing | Mode | Purpose |
|---|---|---|
| Target directory at `/workspaces/project` | RW | Source edits |
| Git state, effective hooks targets and aliases | RO overlays | Protect host-executed Git paths |
| Top-level and nested `.devcontainer` directories | RO overlays | Protect sandbox definitions and launchers |
| Curated authored provider stage | RO | First import or explicit refresh; no host authentication |
| `/home/dev/.claude` or `/home/dev/.codex` | Provider-specific private named volume | Native settings, login and runtime state |
| `/home/dev/.config` and `/home/dev/.local` | Target-specific named volumes | Generic development state |
| Managed Claude guard and settings | Baked root-owned files | Independent Claude tool guard |
| Host `~/.gitconfig` | RO | Host Git configuration; supported include layouts only |
| Provider environment authentication | Explicit runtime input | Optional native login bootstrap or per-process authentication |
| Git push credential or SSH agent | Not forwarded | Publication belongs to git-agent |

## Shared configuration and native login

Both providers use the same authored source: `LOCKBOX_CONFIG_SOURCE`, defaulting
to `~/.config/lockbox/agents`. Shared `AGENTS.md`, `skills/` and `mcp.json` produce
native adapters. Source `claude/` or `codex/` entries preserve provider-specific
settings. Selected native host entries require `LOCKBOX_CONFIG_IMPORT_NATIVE=1`.
No host Claude guard directory or Git configuration file is required. Host Claude global
`.claude.json` is never read. User hooks are stripped; plugin metadata is retained,
but command availability, plugin discovery and endpoint access need verification.

First import preserves existing private files and values. Ordinary starts reuse
private state. Every provider launch requires host Python 3.11 or newer and Node.js
18 or newer. Host Node.js generates the reviewed policy identity; a missing or
unsupported runtime fails before staging configuration or building images.
After changing authored input, run `DEV_SANDBOX_CONFIG_REFRESH=1 dev-claude` or
`DEV_SANDBOX_CONFIG_REFRESH=1 dev-codex` from the target repository. Replaced files
receive private rollback backups. Retired imported entries are removed only when
unchanged from their ownership record; local edits remain private. Imports use host
and private-volume locks. Credentials,
history, sessions and memory are excluded. There is no export, legacy global-file
migration or host-memory copy-back. Close other sessions before refreshing and
close them before changing its allowlist as well. Host locks serialize startup and
release before login or an interactive session. Changed or unconfirmed allowlist
policy requires a successful stop and fresh start, followed by proxy readiness and
mounted-policy confirmation. An unchanged confirmed policy reuses the warm container.
Identical configuration refreshes avoid writes, and five completed importer-owned
backups are retained. Interrupted imports or incomplete rollback block startup
pending private recovery; see [provider configuration](../../docs/provider-configuration.md).
The authored input has its own `agent-input` mount source beside the policy files.
This avoids overlapping host mount sources in Apple Container. Earlier container
layouts require `DEV_SANDBOX_REBUILD=1`; the private provider volume is retained.

Claude uses native `claude auth login`; Codex uses `codex login --device-auth`.
Follow the displayed URL in a host browser and paste a returned code if requested.
No in-repo Keychain lookup or host auth-cache copying occurs. Explicit
`CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY` or
`OPENAI_API_KEY`/enterprise `CODEX_ACCESS_TOKEN` remain optional. A noninteractive
first launch needs a private login or supported environment authentication. Piped
standard input is forwarded to either CLI.

See [provider configuration](../../docs/provider-configuration.md) for exact
imports, precedence, MCP schema, safe links and native feature limits. Recreate
stale containers with `DEV_SANDBOX_REBUILD=1` and the selected launcher; private
named volumes persist. Real first-login, native discovery, doctor and booted egress
checks remain required runtime gates.

## Egress

Locked to the squid allowlist like every sandbox. Effective allowlist =
the shared base + this sandbox's `allowlist.extra.txt` (a general dev floor:
npm, PyPI, GitHub, Claude) + an optional **per-target overlay**:

- drop a `.sandbox-allowlist.txt` (one host per line) in the target repo, or
- reuse a target's existing `.devcontainer/allowlist.extra.txt`.

The launcher requires interactive confirmation for target-authored widening. A
noninteractive launch ignores that untrusted overlay and uses the trusted floor.

Example — a project needs another provider, so `.sandbox-allowlist.txt` might contain:

```
example-provider.invalid
```

The generic sandbox already includes the minimal OpenAI Codex endpoints in its
own overlay; they are deliberately not in the fleet-wide base.

## Security model

- `--cap-drop ALL` + minimal caps, no sudo/setuid (apple/container has no
  `--security-opt`; the VM boundary is the isolation control).
- Root entrypoint locks egress (default-deny, proxy-UID-only) **before** the
  proxy starts; fail-closed. Dev sessions run unprivileged.
- **Launch-integrity pins**: `node/npm/claude/codex/bwrap/gh/git/python3` are fingerprinted
  at build; `bin/dev` aborts if any drift before opening an agent. A real upgrade
  trips this — rebuild to re-pin with the selected provider, for example:
  `DEV_SANDBOX_REBUILD=1 dev-codex`.
  After one successful full check, the launcher may reuse a root-owned `/run`
  record for that running container boot. It is bound to the verifier, exact
  nine-entry manifest, service identity, clean `PATH`, and boot identifier. Missing
  verifiers/manifests fail closed; rebuild stale images.
- Workspace is RW and **no push credential** is present, so a compromised agent
  can alter local files and has no forwarded push credential. It can still send data to allowed hosts.
- **Host-executed Git paths and sandbox definitions are read-only.** The shared
  launcher resolves effective hooks paths, including supported Git configuration
  includes, and protects their writable workspace aliases. Top-level and nested
  `.devcontainer` directories are also overlaid read-only. Unsupported symlink,
  hardlink and include layouts fail at launch. A saved mount-policy fingerprint
  prevents stale container reuse. These controls close paths where a workspace
  edit could become code execution on the next host launch or Git operation.
  Verify actual mounts with the doctor; portable fixtures do not establish them.
- **Residual risk — other host-executed workspace files.** The RO lock covers the
  git paths because they can't be hand-enumerated but *can* be derived. Two
  classes are NOT locked, by design:
  - **Build / release / install scripts** (`package.json` scripts, `install.sh`,
    packaging post-install) run on the host at *release/build* time. They can't be
    RO-mounted without breaking the dev loop (the agent legitimately edits them).
    Mitigation is process: **review the diff before running any workspace build /
    release script on the host** — treat them like any other untrusted workspace
    content.
  - **Project-level Claude config** (`.claude/settings.json`, `settings.local.json`)
    defines hooks that execute if you run Claude Code *on the host* in this repo.
    Left RW so an in-container agent can edit project config normally. The host-side
    managed settings and guard baked into the container protect the container CLI.
    They do not govern host Claude sessions. Verify your host policy separately if
    you run Claude outside LockBox.

## Health

Inside the container: `dev-sandbox-doctor`.

## Canonical files

`init-firewall.sh`, `squid.conf`, `clienthello-policy.py`, `allowlist.txt`, and
`launcher-common.sh` are
**generated/vendored** by `LockBox/sync.sh` — do not hand-edit them
here. Edit the canonical copies in `LockBox/` (or this dir's
`allowlist.extra.txt`) and re-run `./sync.sh`.

The generated firewall installs the root-owned ClientHello companion before its
readiness sentinel, so existing fleet Dockerfile firewall COPY integration is
sufficient. Successful fleet sync, image rebuilds and runtime checks are required before deployment. The helper
rejects missing/malformed visible SNI and ECH, including GREASE, without TLS
decryption. The booted egress fixture checks CONNECT/SNI equality through Squid
using different allowlisted names.

The Claude policy bundle is baked into root-owned image paths. It includes the shared
command guard, post-edit monitor, bounded formatter and exact project opt-ins. The
launcher streams the reviewed host checker and verifies every dependency against
current source before lifecycle scripts or credentials. No host hook mount is used.
Rebuild after changing the canonical LockBox bundle; stale policy stops startup.

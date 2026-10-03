# LockBox

LockBox is a canonical egress policy and launcher toolkit for hardened coding-agent
sandboxes. It keeps the firewall, HTTPS allowlist proxy, and shared launcher logic in
one repository, then vendors reviewed copies into every managed `.devcontainer`.

The repository also contains two ready-to-use sandboxes:

- `.devcontainer/` is the environment for developing LockBox itself.
- `sandbox/.devcontainer/` is a general-purpose sandbox that can open another local
  project.

Both support Claude Code and OpenAI Codex through explicit provider entry points. The
selected provider changes the agent process and its private configuration volume, not
the network policy, read-only Git mounts, or launch-integrity checks.

## Why LockBox exists

Copying security scripts between repositories creates silent drift. A firewall fix can
land in one sandbox while older copies remain elsewhere. LockBox replaces those
independent copies with a small set of canonical files and a deterministic sync check.

LockBox provides:

- a fail-closed IPv4 and IPv6 egress firewall;
- a CONNECT-only Squid proxy with a hostname allowlist;
- a shared base allowlist with per-sandbox overlays;
- reusable host-side launcher helpers;
- provenance stamps on vendored files;
- pinned and verified agent toolchains for the in-repo images;
- checks for vendoring drift, provider parity, and accidentally staged secrets; and
- an optional smoke test that boots the image and proves the egress lock works.

## Security model

LockBox applies two independent outbound controls when a container starts:

1. `init-firewall.sh` sets default-DROP packet rules. Only the dedicated Squid user may
   create outbound connections. Other processes can reach the proxy over loopback but
   cannot connect directly to the network. IPv6 is default-deny, and cloud metadata
   addresses are blocked before the proxy exception.
2. `squid.conf` accepts HTTPS `CONNECT` requests only. It reads the TLS Server Name
   Indication (SNI), splices allowlisted connections without decrypting them, and
   terminates everything else. `clienthello-policy.py` also checks the visible TLS
   ClientHello and rejects missing or malformed SNI and Encrypted ClientHello (ECH),
   including GREASE. End-to-end TLS remains intact; LockBox does not install a
   man-in-the-middle certificate authority. The helper compares SNI with the CONNECT
   hostname; the booted egress fixture checks rejection of different allowlisted names.

Startup succeeds only after the packet rules are verified and
`/run/egress-firewall-ok` is written. If Squid stops, the packet layer still prevents
direct egress while the entrypoint supervises the proxy.

Additional controls include:

- a non-root `dev` user and no `sudo`;
- all set-user-ID and set-group-ID bits removed from the image;
- all Linux capabilities dropped, with only the small startup set added back;
- read-only overlays for top-level and nested `.devcontainer` directories, `.git`,
  and effective Git hooks paths and their workspace aliases;
- no Git push credential inside an ordinary sandbox;
- private, provider-specific configuration volumes; and
- a launch gate that rejects drift in pinned tools.

Read [`.devcontainer/README.md`](.devcontainer/README.md) for the full threat model and
runtime details.

## Requirements

The host launchers use [Apple's `container`](https://github.com/apple/container), not
Docker Compose or the Visual Studio Code Dev Containers extension. On macOS, install
the runtime and start its system service:

```sh
container system start
```

Docker can run the egress smoke test in continuous integration, but it is not the
primary host runtime. The repository itself is Bash and container infrastructure; it
has no application build step or root `package.json`. Every provider launch
requires host Python 3.11 or newer and Node.js 18 or newer. The launchers check
Node.js before staging configuration or building an image; it generates the reviewed
policy identity for both providers. The image retains its separately pinned Node.js.

## Quick start

When operating Git directly on the host, enable the tracked pre-commit checks once
after cloning:

```sh
make setup
```

Git does not preserve `core.hooksPath` across clones. Without this step, the tracked
secret audit and vendoring drift check do not run before host commits. Editing
sandboxes leave Git metadata read-only. The separate `git-agent` publication
environment keeps repository hooks disabled and reviews the explicit diff and
validation evidence before staging, signing, committing, and pushing.

To work on LockBox inside its own sandbox:

```sh
.devcontainer/bin/claude
.devcontainer/bin/codex
```

To open another project in the general-purpose sandbox, invoke the LockBox launcher
from that project's working directory:

```sh
cd /path/to/project
/path/to/LockBox/sandbox/.devcontainer/bin/dev-claude
/path/to/LockBox/sandbox/.devcontainer/bin/dev-codex
```

The shared `bin/dev` entry point defaults to Claude. An explicit environment value
selects the provider:

```sh
DEV_SANDBOX_AGENT=codex /path/to/LockBox/sandbox/.devcontainer/bin/dev
```

Each provider uses a separate container and native configuration volume. A launcher
refuses to reuse a container created for a different provider, inspected image,
mount or configuration policy. Image inspection must include an OCI SHA-256 digest;
missing or malformed descriptors fail the launch. The full descriptor is hashed
conservatively, so metadata changes can also require recreation. Existing containers
are inspected on the host before they start, including their actual image digest.
Cleanup applies only after the launcher selects a validated container or begins
creation. Recreate with `LOCKBOX_REBUILD=1` or
`DEV_SANDBOX_REBUILD=1`; named volumes retain private configuration and login state.

## Canonical and generated files

Edit the canonical source in this repository. Do not edit a vendored or generated copy
inside a managed `.devcontainer`.

| Path | Purpose |
|---|---|
| `init-firewall.sh` | Canonical packet-level egress lock |
| `squid.conf` | Canonical CONNECT-only SNI allowlist proxy |
| `base-allowlist.txt` | Minimum hostname set shared by every managed sandbox |
| `launcher-common.sh` | Shared host-side launcher, staging, mount, and cleanup helpers |
| `clienthello-policy.py` | Visible TLS ClientHello validation for the Squid splice gate |
| `claude-stage-items.txt` | Curated staging manifest used by sibling Claude launchers |
| `.devcontainer/agent-config.py` | Shared curated imports and native provider adapters for the in-repo launchers |
| `vendored-files.txt` | Manifest of files copied into each managed sandbox |
| `paths.sh` | Shared discovery of repository targets and local fleet configuration |
| `sync.sh` | Vendors canonical files and generates effective allowlists; `--target=/absolute/managed/.devcontainer` restricts regeneration to one registered target |
| `audit.sh` | Scans the Git index for secrets and private keys |
| `tool-pins.env` | Reviewed versions and hashes for baked tools |
| `bump-pins.sh` | Reports, validates, and deliberately updates tool pins |
| `test/egress-smoke.sh` | Boots an image and tests the enforced network policy |
| `VERSION` / `CHANGELOG.md` | Release identity and history |

Each vendored file contains the canonical content plus a deterministic stamp with the
LockBox version and source SHA-256. `./sync.sh --check` regenerates the expected copy
and compares it byte for byte.

## Allowlist model

Every effective allowlist is generated from two inputs:

```text
base-allowlist.txt
    + <managed .devcontainer>/allowlist.extra.txt
    = <managed .devcontainer>/allowlist.txt
```

- Put a host in `base-allowlist.txt` only when every managed sandbox needs it.
- Put project-specific package registries, APIs, and services in that sandbox's
  `allowlist.extra.txt`.
- Add a comment explaining why each new host is required.
- Never hand-edit `allowlist.txt`; it is generated.

After changing either input, run `./sync.sh`. Most managed images bake the effective
allowlist and must be rebuilt. The general-purpose sandbox bind-mounts its effective
list. Changed or unconfirmed policy requires a successful stop and fresh start;
the launcher confirms proxy readiness, mounted bytes and container generation
before opening a session. Unchanged confirmed policy reuses the warm container.
Such a restart interrupts other sessions using the same container.

The general-purpose sandbox can also consider a target workspace's
`.sandbox-allowlist.txt`. Because that file belongs to the project being opened, an
interactive launch asks for confirmation before widening egress. Non-interactive
launches ignore the untrusted overlay and remain fail-closed.

## Managing sandbox targets

`paths.sh` provides shared target discovery for syncing and pin checks. A fresh
clone manages only LockBox's two in-repository sandboxes. Personal deployment
locations are kept in `paths.local.tsv`, which Git ignores. Copy
`paths.example.tsv` to that file, replace the example path, and add one absolute
`.devcontainer` path per line. Paths may contain spaces. An optional stable project
label follows a literal tab; otherwise the parent directory name supplies the label.
Use a stable label when two differently named checkouts should generate identical
allowlist comments. The file is data and is never executed as shell code.

Register external sandboxes in that local file before relying on LockBox to update
them. Missing targets and duplicate entries are errors. No sibling repository is
assumed or modified by a fresh clone.

| Variable | Purpose |
|---|---|
| `EGRESS_FLEET_FILE` | Alternate local target file; an explicitly configured missing file is an error |
| `EGRESS_REPO` | LockBox checkout root; normally detected automatically |
| `EGRESS_SELF_ONLY=1` | Check only the two in-repository sandboxes; ignore local fleet data, as in CI |

`./sync.sh --allow-missing` can downgrade missing targets to warnings for an explicit,
temporary partial sync. Do not use it as the normal deployment path.

## Provider authentication and configuration

Both providers use official login inside their private native volumes. Claude uses
`claude auth login`; Codex uses `codex login --device-auth`. Open the displayed URL
in a host browser and follow the CLI prompts. Ordinary in-repo launches do not read
Keychain or copy host authentication caches. Explicit environment authentication is
also supported: `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` for Claude, and
`OPENAI_API_KEY` or enterprise `CODEX_ACCESS_TOKEN` for Codex. A noninteractive first
launch needs an existing private login or supported environment authentication.

`LOCKBOX_CONFIG_SOURCE` selects a common authored configuration directory, defaulting
to `~/.config/lockbox/agents`. It can hold `AGENTS.md`, `skills/`, `mcp.json`, and
provider-specific `claude/` and `codex/` entries. This is the only source by default.
`LOCKBOX_CONFIG_IMPORT_NATIVE=1` opts into selected authored native host files.
First initialization preserves existing private entries.
Ordinary starts reuse private state. Refresh deliberately with
`LOCKBOX_CONFIG_REFRESH=1` or `DEV_SANDBOX_CONFIG_REFRESH=1`; replaced files get
private rollback backups. Refresh removes retired imported entries only when their
private values still match the ownership record, and preserves local edits. Imports
and lifecycle changes are locked per profile. Identical refreshes avoid writes,
and five completed importer-owned backups are retained. Interrupted imports or
incomplete rollback block startup pending private recovery. Credentials, sessions,
history and memory are excluded.
There is no configuration export or host-memory copy-back.

Repository instructions use `AGENTS.md` for both pinned CLIs. Claude's builtin AGENTS
support is enabled even when a project also has Claude instruction files. Shared
global instructions become a native Claude `CLAUDE.md` adapter inside its volume.
Host Claude global `.claude.json` is never read: shared Model Context Protocol (MCP)
servers must be declared explicitly. User hooks are stripped from imported settings;
plugin metadata is retained, but Linux commands, plugin discovery and endpoint access
still need verification. Both images bake the shared command guard, post-edit monitor,
bounded formatter, exact project policy and managed settings as root-owned files.
The host streams a reviewed checker into the container and verifies every dependency
against current source before lifecycle scripts or credentials. A clean host needs no
Claude hook directory. Generic launches retain known project opt-ins and opt out for
unknown roots; publication launches disable automatic formatting.
Read [provider configuration](docs/provider-configuration.md)
for exact import entries, precedence, MCP declarations and login limits.

A running agent can access its own credential. Revoke it if the sandbox is suspected
to be compromised. Both launchers install cleanup before startup checks can abort.
Set `LOCKBOX_STOP_ON_EXIT=0` or `DEV_SANDBOX_STOP_ON_EXIT=0` to keep a container
running. Piped standard input is forwarded for either provider.

## Toolchain pinning

The in-repo images bake Claude Code, Codex, Node.js, Python, and safe-chain. They do not
fetch the latest agent CLI at runtime. The pin updater resolves versions and hashes
before a reviewed rebuild, and native agent binaries are verified by SHA-256.

Useful targets:

| Command | Purpose |
|---|---|
| `make pins-report` | Compare pinned, cooldown-eligible, and upstream versions; uses the network |
| `make pins` | Resolve and write reviewed, cooldown-eligible versions and hashes |
| `make pins-check` | Verify offline that pins match every managed image |

The pin updater defaults to a zero-day release cooldown. `COOLDOWN_DAYS` in
`tool-pins.env` controls the configured policy; updates still require review and
validation before publication.
Node.js remains within its pinned major version; crossing a major version is a
deliberate platform decision. Some operating-system packages are not version-pinned
because mirrors remove old builds, so the launch gate
records and verifies the binaries present in the built image instead.

## Making a change

Read [`REVIEW.md`](REVIEW.md) first. Ordinary editing sessions change and test the
working tree only; publication belongs to the separate `git-agent` environment.

For a canonical firewall, proxy, or launcher change:

```sh
# Edit the canonical root-level file, then propagate it.
./sync.sh

# Run the portable checks.
shellcheck *.sh .devcontainer/bin/* sandbox/.devcontainer/bin/*
make check
make audit
```

`make check` runs vendoring and pin checks plus provider parity, integrity-cache,
configuration, startup-lock, mount-policy, doctor, and ClientHello regression checks.
It requires Python 3.11 or newer. `make audit` checks the Git index, so review unstaged
changes separately and do not stage files from an editing session merely to run it.

For an allowlist change, edit the base or one target's overlay, then run the same sync
and checks. Rebuild the affected sandbox and run its doctor command before relying on
the new policy.

For changes to firewall rules, proxy semantics, capabilities, mounts, or allowed
egress, also run the functional test on a host with a supported container runtime:

```sh
make test
```

The smoke test uses local fixtures to check packet and TLS policy, read-only mounts,
native provider features, and refusal of non-CONNECT cleartext requests. Set
`CHECK_INTERNET=1` to add external endpoint probes; those probes do not gate success.
CI builds and boots both in-repo images. For generic launcher lifecycle changes, also run
`make native-launcher-test` on an Apple Container host with the generic test image
already built. Portable checks alone do not verify these runtime behaviors.

Before handing off a diff, record the changed paths, their SHA-256 hashes, completed
checks, and any required checks that could not run. After publication, confirm that
the pull request has the required **CI Complete** status and no blocking review.
Remove a pull request branch only after its work is merged or confirmed superseded,
and check its current head before deletion.

## Integrating a managed sandbox

A consuming Dockerfile copies the generated files to stable paths:

```dockerfile
COPY init-firewall.sh /usr/local/sbin/egress-firewall
COPY squid.conf /etc/squid/squid.conf
COPY allowlist.txt /etc/squid/allowlist.txt
```

The generated firewall embeds the canonical ClientHello companion and installs
it as a root-owned executable before publishing the readiness sentinel. Existing
fleet Dockerfile firewall COPY integration is sufficient. The standalone helper
is also vendored for inspection. Deployment requires successful fleet sync, affected image rebuilds,
Squid parse, booted egress and real first-login checks.

Its root entrypoint must run `/usr/local/sbin/egress-firewall`, verify
`/run/egress-firewall-ok`, start Squid, and only then continue the container lifecycle.
If the sandbox exposes a service, bake the allowed TCP ports into
`/etc/egress/inbound-ports`, one port per line. An absent file means no inbound service
ports are opened.

The host launcher must preserve the read-only `.devcontainer`, `.git`, and Git hooks
mounts and use the minimal capability set documented in
[`.devcontainer/README.md`](.devcontainer/README.md). Treat those details as security
invariants, not optional examples.

## Release process

For a release, update `VERSION` and `CHANGELOG.md`, run `./sync.sh` so every provenance
stamp changes, complete the required checks, and create the matching `v<VERSION>` tag
through the repository's authorized publication workflow.

Review [`REVIEW.md`](REVIEW.md) before proposing or publishing any change. It is the
security checklist for this repository.

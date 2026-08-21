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
   terminates everything else. End-to-end TLS remains intact; LockBox does not install
   a man-in-the-middle certificate authority.

Startup succeeds only after the packet rules are verified and
`/run/egress-firewall-ok` is written. If Squid stops, the packet layer still prevents
direct egress while the entrypoint supervises the proxy.

Additional controls include:

- a non-root `dev` user and no `sudo`;
- all set-user-ID and set-group-ID bits removed from the image;
- all Linux capabilities dropped, with only the small startup set added back;
- read-only overlays for `.devcontainer`, `.git`, and any relocated Git hooks path;
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
has no application build step or root `package.json`.

## Quick start

Enable the tracked pre-commit checks once after cloning:

```sh
make setup
```

Git does not preserve `core.hooksPath` across clones. Without this step, the tracked
secret audit and vendoring drift check do not run before commits.

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

The provider-neutral entry point is also available when selection must come from the
environment:

```sh
DEV_SANDBOX_AGENT=codex /path/to/LockBox/sandbox/.devcontainer/bin/dev
```

Each provider uses a separate container and native configuration volume. A launcher
refuses to reuse a container created for a different provider.

## Canonical and generated files

Edit the canonical source in this repository. Do not edit a vendored or generated copy
inside a managed `.devcontainer`.

| Path | Purpose |
|---|---|
| `init-firewall.sh` | Canonical packet-level egress lock |
| `squid.conf` | Canonical CONNECT-only SNI allowlist proxy |
| `base-allowlist.txt` | Minimum hostname set shared by every managed sandbox |
| `launcher-common.sh` | Shared host-side launcher, staging, mount, and cleanup helpers |
| `vendored-files.txt` | Manifest of files copied into each managed sandbox |
| `paths.sh` | Authoritative list of managed `.devcontainer` directories |
| `sync.sh` | Vendors canonical files and generates effective allowlists |
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
list and reloads Squid when the content changes.

The general-purpose sandbox can also consider a target workspace's
`.sandbox-allowlist.txt`. Because that file belongs to the project being opened, an
interactive launch asks for confirmation before widening egress. Non-interactive
launches ignore the untrusted overlay and remain fail-closed.

## Managing sandbox targets

`paths.sh` is the single source of truth for all `.devcontainer` directories managed by
`sync.sh`, `audit.sh`, and the pin checks. Register a new sandbox there before relying
on LockBox to update it. Missing targets are errors by default because silently
skipping a moved repository would leave stale security controls in place.

The checked-in file contains the local deployment layout. Adapt its target list for
your environment, or use its environment overrides where appropriate:

| Variable | Purpose |
|---|---|
| `CODE_ROOT` | Root directory used by managed target paths |
| `EGRESS_REPO` | LockBox checkout root; normally detected automatically |
| `EGRESS_SELF_ONLY=1` | Limit checks to the in-repo generic sandbox, as used in CI |

`./sync.sh --allow-missing` can downgrade missing targets to warnings for an explicit,
temporary partial sync. Do not use it as the normal deployment path.

## Provider authentication

Claude and Codex share the image and security boundary but keep separate runtime
credentials.

Claude receives a token at launch and uses a sanitized configuration stage. Raw host
configuration is not mounted into the container. Codex never mounts host `~/.codex`;
on its first interactive launch it uses the official device-code flow. An
`OPENAI_API_KEY` or enterprise `CODEX_ACCESS_TOKEN` can bootstrap the corresponding
non-browser login instead. The resulting Codex cache remains in its private container
volume.

No provider credential is baked into the image or copied into the workspace. A running
agent can access its own credential, so revoke that credential if the sandbox is
suspected to be compromised.

Repository guidance is exposed through `AGENTS.md` for Codex and `CLAUDE.md` for
Claude.

## Toolchain pinning

The in-repo images bake Claude Code, Codex, Node.js, Python, and safe-chain. They do not
fetch the latest agent CLI at runtime. Updates are resolved during a reviewed rebuild,
after a cooldown period, and the native agent binaries are verified by SHA-256.

Useful targets:

| Command | Purpose |
|---|---|
| `make pins-report` | Compare pinned, cooldown-eligible, and upstream versions; uses the network |
| `make pins` | Resolve and write reviewed, cooldown-eligible versions and hashes |
| `make pins-check` | Verify offline that pins match every managed image |

The default cooldown is seven days. Node.js remains within its pinned major version;
crossing a major version is a deliberate platform decision. Some operating-system
packages are not version-pinned because mirrors remove old builds, so the launch gate
records and verifies the binaries present in the built image instead.

## Making a change

For a canonical firewall, proxy, or launcher change:

```sh
# Edit the canonical root-level file, then propagate it.
./sync.sh

# Run the portable checks.
shellcheck *.sh .devcontainer/bin/* sandbox/.devcontainer/bin/*
make check
make audit
```

For an allowlist change, edit the base or one target's overlay, then run the same sync
and checks. Rebuild the affected sandbox and run its doctor command before relying on
the new policy.

For changes to firewall rules, proxy semantics, capabilities, mounts, or allowed
egress, also run the functional test on a host with a supported container runtime:

```sh
make test
```

The smoke test confirms that the firewall sentinel exists, an off-allowlist host is
blocked, an allowlisted host is reachable, and a non-CONNECT cleartext request is
refused.

## Integrating a managed sandbox

A consuming Dockerfile copies the generated files to stable paths:

```dockerfile
COPY init-firewall.sh /usr/local/sbin/egress-firewall
COPY squid.conf /etc/squid/squid.conf
COPY allowlist.txt /etc/squid/allowlist.txt
```

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

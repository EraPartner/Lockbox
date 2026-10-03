# LockBox devcontainer

Hardened, egress-locked sandbox for editing **LockBox itself** (the fleet's egress
hub) with either Claude Code or OpenAI Codex. It is a
development box, not a service host: it publishes no ports. Same fleet pattern —
`debian:bookworm-slim` (pinned by `@sha256`), non-root `dev` user (UID 1000), all
egress forced through the in-container squid SNI allowlist that this very repo
defines. (Note this is distinct from `sandbox/.devcontainer`, the generic `dev`
box also vendored in this repo — this one is scoped to editing LockBox.)

## What's inside

| Component            | Provenance                                                     |
| -------------------- | ------------------------------------------------------------- |
| GitHub CLI (`gh`)    | apt (cli.github.com)                                          |
| Node.js 24           | Static build into `/usr/local` (runs the baked Claude guard) |
| Python 3.12          | python-build-standalone into `/opt/python` (Claude plugin hooks) |
| `shellcheck`, `make` | apt / build-essential — lint + drive the repo's Makefile      |
| Claude Code          | `npm i -g @anthropic-ai/claude-code` (baked, pinned)          |
| OpenAI Codex         | `npm i -g @openai/codex` (baked, native binary hash-pinned)   |
| Bubblewrap           | apt (Codex Linux sandbox prerequisite, build-time fingerprinted) |
| safe-chain           | baked, version-pinned (screens npm/pip installs)               |

LockBox is bash/shell infra (no root `package.json`), so there is no `npm ci` /
build step — you edit scripts, lint with `shellcheck`, and self-check with
`./sync.sh --check` + `./audit.sh`. Note `make test` (the egress smoke test) boots
a container and so must run on the host, not nested inside this box.

## How to use — CLI only

This sandbox runs on **[apple/container](https://github.com/apple/container)** —
Apple's native macOS container runtime. There is no Docker, no docker-compose, and
no devcontainer CLI / Dev Containers extension; there is no `compose.yaml` and no
`devcontainer.json`. Prerequisite: `container` installed and the system VM started
(`container system start`).

The host entry points `.devcontainer/bin/claude` and `.devcontainer/bin/codex`
select a provider and call the shared `.devcontainer/bin/agent` implementation.
The bare `agent` entry point defaults to Claude. It forwards invocations into
provider-specific containers that share one image.
Both providers import curated authored configuration once and keep native login
and runtime state in separate private volumes. Both run an idempotent image build,
then create, start or reuse the selected container, verify pins and managed policy,
wait for proxy readiness, run lifecycle scripts, prepare configuration, and open the
selected CLI. Reuse refuses a
missing or mismatched provider, image descriptor or mount/configuration policy. Image
inspection must contain an OCI SHA-256 digest; errors fail closed. The descriptor
hash includes metadata, so metadata changes can also require recreation. A stopped
container is reused only after its host metadata passes validation. Cleanup
starts when the launcher selects a validated container or begins creation.
There is no
in-repo Keychain lookup. Piped input reaches either CLI without allocating a TTY.

The fish function `lockbox-claude` (in `~/.config/fish/functions/`) walks up from
`$PWD` to the repo (matching `.devcontainer/Dockerfile`), falls back to
`$LOCKBOX_HOME`, then runs that launcher. Use it anywhere:

```sh
lockbox-claude --dangerously-skip-permissions
```

For Codex, call the repository launcher directly or wrap it in a fish function:

```sh
.devcontainer/bin/codex
```

On first interactive use it starts `codex login --device-auth`. Complete the
one-time code in a host browser. Claude uses native `claude auth login` and may
ask you to paste a returned code. The resulting Codex login cache stays in the native
`lockbox-codex` volume; host `~/.codex` is never mounted.

To drop into a shell instead of an agent:

```sh
container exec -it --user dev lockbox-dev bash
```

To force a full rebuild (e.g. after changing the Dockerfile or allowlist) — this
rebuilds the image **and** recreates the container:

```sh
LOCKBOX_REBUILD=1 lockbox-claude --dangerously-skip-permissions
LOCKBOX_REBUILD=1 .devcontainer/bin/codex
```

By default the launcher powers the container's VM down on session exit so it stops
pinning its 4 GB. Set `LOCKBOX_STOP_ON_EXIT=0` to keep it warm for instant reuse.

## No published ports

This is a development box, not a service host: the launcher publishes no ports and
there is no `/etc/egress/inbound-ports` file. Egress is still forced through the
in-container proxy; nothing listens for inbound host connections.

## Network policy

Egress is enforced in two layers by the root entrypoint on every start:

1. **In-container SNI proxy** (`squid`, peek+splice). All outbound
   HTTP(S) must traverse `squid` on `127.0.0.1:3128`. squid peeks the
   TLS SNI and _splices_ allowed hostnames (tunnels without decrypting —
   end-to-end TLS preserved, no MITM) and terminates the rest. The visible
   ClientHello helper rejects missing or malformed SNI and Encrypted ClientHello
   (ECH), including GREASE; Squid's CONNECT-host fallback alone is insufficient.
   The helper compares SNI with the CONNECT hostname. This equality check still
   needs verification through a running Squid.
2. **`iptables` egress lock** (`init-firewall.sh`): default-deny, then only
   the `proxy` UID may originate outbound packets; everything else must use the
   proxy or be dropped. IPv6 is default-deny; denied egress is
   rate-limited-logged (`dmesg | grep lockbox-deny`).

The allowlist is **baked into the image** at `/etc/squid/allowlist.txt`. It is
generated by `LockBox/sync.sh` as the shared `base-allowlist.txt`
(Anthropic + Claude Code, `registry.npmjs.org`, GitHub, PyPI, Debian apt,
`nodejs.org`, `malware-list.aikido.dev`, …) plus LockBox's own
`allowlist.extra.txt`.

LockBox's overlay also grants only the Codex runtime endpoints needed for API
traffic and ChatGPT/device-code authentication (`api.openai.com`,
`auth.openai.com`, and `chatgpt.com`). They are deliberately not in the shared
base allowlist.

> **Everything routes through the proxy.** `HTTP(S)_PROXY` is set, and
> `NODE_USE_ENV_PROXY=1` makes Node ≥24's global `fetch` honor it too —
> so `claude`, `npm`, `git`, `gh`, `pip`, and app `fetch` all egress via
> squid. App calls to **allowlisted** hosts work; **LAN stays unreachable**
> (not allowlisted), so pollers still can't hit home-lab devices here.

To change the allowlist, edit LockBox's `allowlist.extra.txt` (or the shared
`base-allowlist.txt`), re-run `LockBox/sync.sh` to regenerate
`.devcontainer/allowlist.txt`, and **rebuild** (`LOCKBOX_REBUILD=1
lockbox-claude`) so it's re-baked into the image. `dev` can't re-run the
firewall (no sudo) — a rebuild recreates the container and re-applies it. squid is
supervised by the entrypoint: if it crashes, it's restarted (egress stays denied
while down — fail-closed).

**Supply-chain scanning.** The image bakes Aikido safe-chain and `BASH_ENV`
wires it into every shell, so `npm`/`pip` installs are
screened against `malware-list.aikido.dev` before running. Defense-in-depth
on top of the sandbox.

**Observability.** Blocked egress shows as a TLS/cert error or
`CONNECT 403` — that's the policy denying it. The definitive log is
`/var/log/squid/access.log` (`dev`-readable; `TCP_DENIED`/`NONE` = blocked).
`dmesg | grep lockbox-deny` catches proxy-bypass attempts but needs root.
Run `bash .devcontainer/bin/doctor` inside the box for a one-shot readiness check.

**Not covered:** Claude's WebSearch/WebFetch run Anthropic-side, not through the
proxy. The visible ClientHello policy rejects ECH, including GREASE. Clients
must disable it; the proxy does not decrypt the handshake.

**Launch-integrity gate.** The image bakes `lockbox-verify-pins`, which records a
SHA-256 of `node`, `npm`, `claude`, `codex`, `bwrap`, `gh`, `git`, and `python3` at build time. The
launcher runs it on every start and **aborts fail-closed** on fingerprint drift or
if the checker is missing (a stale pre-pin image) — rebuild to re-pin
(`LOCKBOX_REBUILD=1 lockbox-claude`).

Both in-repo launchers cache a successful full check for the current running
container boot. The root-owned `/run` record is bound to the boot identifier,
verifier, pin manifest, service identity, and clean `PATH`. A missing, stale,
malformed, or writable record is a cache miss. The full absolute-path verifier
then runs with startup hooks and pin overrides cleared. Missing verifiers and
manifests fail closed. Cache publication failure only loses the optimization.

**Isolation.** The container runs as the non-root `dev` user with **no sudo** and
all setuid/setgid bits stripped image-wide. Caps: `--cap-drop ALL`, then re-add
only `NET_ADMIN, CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID` (entrypoint
iptables/perms-fix + squid privilege-drops). apple/container has **no
`--security-opt`** mechanism — there is no `no-new-privileges` flag; the per-container
**VM boundary** is the isolation control instead. The container is allocated 4 GB
RAM and 4 CPUs (`-m 4g -c 4`), with `--tmpfs /tmp` and `--tmpfs /var/tmp`.

**Prerequisites and portability:** host launchers require macOS and Apple
Container; every provider launch needs host Python 3.11
or newer and Node.js 18 or newer. Missing or unsupported host Node.js fails before
configuration staging or image builds. Provider login uses the native CLI inside Linux. No Git credential or
SSH agent is forwarded.

## Persistence

| Source | Container path | Mode | Purpose |
|---|---|---|---|
| Host LockBox checkout | `/workspaces/LockBox` | RW bind | Source edits appear on the host |
| Host Git state and effective hook targets/aliases | Corresponding workspace paths | RO overlays | Prevent changes to host-executed Git paths |
| Top-level and nested `.devcontainer` directories | Corresponding workspace paths | RO overlays | Protect sandbox definitions and launchers |
| `lockbox-claude` | `/home/dev/.claude` | Private named volume | Claude settings, native global JSON, login and runtime state |
| `lockbox-codex` | `/home/dev/.codex` | Private named volume | Codex settings and native login cache |
| `~/.claude-sandbox/stage/lockbox-<provider>` | `/home/dev/.agent-stage-root` | RO bind | Curated authored snapshot, excluding host authentication |
| Managed Claude guard and settings | Managed container paths | Baked root-owned files | Independent Claude tool guard |

The entrypoint repairs native volume ownership. Host provider homes are never
mounted. Recreating a container retains named volumes. No legacy global-file
migration is needed; configuration, login state and memory remain private.

## Shared configuration and login

`LOCKBOX_CONFIG_SOURCE` defaults to `~/.config/lockbox/agents`. Shared `AGENTS.md`,
`skills/` and explicit `mcp.json` declarations are adapted to native Claude and
Codex files. Source `claude/` or `codex/` entries retain provider-specific settings,
agents and plugin metadata. Selected native host entries are considered only with
`LOCKBOX_CONFIG_IMPORT_NATIVE=1`. Host Claude
`.claude.json` is never read. User hooks are stripped; managed guards remain
root-owned in the image; a clean host needs no guard directory. Configuration retention does not prove Linux commands, plugin discovery
or MCP endpoint access work.

First import preserves private existing values. Ordinary launches reuse private
configuration. After changing authored input, refresh deliberately:

```sh
LOCKBOX_CONFIG_REFRESH=1 .devcontainer/bin/claude
LOCKBOX_CONFIG_REFRESH=1 .devcontainer/bin/codex
```

Refresh backs up replaced files under the provider's private
`.lockbox-config-backup-*` directory. Present settings take authored values; each
MCP server is replaced atomically. Retired imported entries are removed only when
unchanged from their ownership record. Local edits and private entries survive.
Credentials, history, sessions and
memory are excluded. There is no configuration export or host-memory copy-back.
Startup uses a host profile lock and aborts if proxy readiness times out. The lock
releases before login or an interactive session. Imports also use a private-volume
lock. Identical refreshes avoid writes, and five completed importer-owned backups
are retained. An interrupted import or incomplete rollback blocks startup pending
private recovery; see [provider configuration](../docs/provider-configuration.md).
Close other sessions before refreshing.

Claude uses `CLAUDE_CONFIG_DIR=/home/dev/.claude`, native `claude auth login`, and
optional explicit `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` authentication.
Codex uses `CODEX_HOME=/home/dev/.codex`, file credential storage, native device
login, and optional `OPENAI_API_KEY` or enterprise `CODEX_ACCESS_TOKEN` bootstrap.
A noninteractive first launch needs an existing private login or supported
environment authentication. The launchers do not read Keychain or host auth caches.

Both pinned CLIs read project `AGENTS.md`. Claude's builtin is enabled to read it
alongside Claude instruction files. Shared global instructions become a private
native Claude `CLAUDE.md` adapter. Project skills and provider-specific instructions
remain governed by each CLI's discovery rules. Read
[provider configuration](../docs/provider-configuration.md) for exact authored
entries, precedence, safe link handling, MCP schema and native feature limits.

Runtime validation is still required: rebuild each affected image, run its doctor,
exercise real first-login flows and native skill/plugin/MCP discovery, and run the
booted egress test. The generated firewall installs the root-owned ClientHello
companion before its readiness sentinel. Deployment requires successful fleet sync and rebuilds.
Portable checks do not establish those runtime results.

## Git (read-only inside; publication through git-agent)

The container can **read** git history but cannot change it. The repo's `.git`
is bind-mounted **read-only**, no git credential (`GH_TOKEN`/`GITHUB_TOKEN`) is
forwarded, and the host ssh-agent is **not** forwarded. So a compromised agent
can't rewrite history, push, or sign/authenticate as you over SSH.

| Operation                                   | Works?   | Notes                                                                            |
| ------------------------------------------- | -------- | -------------------------------------------------------------------------------- |
| `git status` / `diff` / `log` / `show`      | ✅       | Read-only on the bind-mounted repo (`safe.directory` is set)                     |
| `git commit` / `rebase` / `reset` / `amend` | ❌       | `.git` is read-only — fails with EROFS, by design                                |
| `git push` / `gh pr create`                 | ❌       | No credential in the container; `git push` errors with "could not read Username" |
| commit signing (ssh-agent)                  | ❌ (n/a) | No ssh-agent forwarded; signing belongs to the separate `git-agent` container     |

**Workflow:** make changes inside the container (they appear on the host via the
bind mount immediately), then leave this editing session and invoke the host-side `git-agent`
launcher. Its separate narrow container reviews, stages, signs, commits, and pushes the handed-off
diff. There is no Git authentication to set up in this editing container.

## Private memory and limitations

Provider memory stays in the private container volume. It is not seeded from or
copied back to the host. The workspace remains writable, so intentionally authored
repository instructions can still be edited through the normal worktree.

Host-specific tools and hardware are unavailable inside Linux. Direct LAN access
is denied by the egress policy. Changing the baked allowlist requires sync and a
rebuild. Unsupported Git include, symlink and hardlink layouts fail at launch;
use a supported checkout rather than weakening the mount policy.

## Safety note

The container runs as a non-root user (`dev`). A malicious project can still
exfiltrate anything visible to the selected agent, including its runtime
credential, to an allowlisted host. Treat this as _"the host is isolated from the
agent,"_ not _"the agent is isolated from a hostile repo."_ Use bypass-permission
flags only for trusted repositories.

**Why sandbox definitions and Git hooks are read-only.** Source is writable so
an agent can implement changes. Host launchers, Dockerfiles and Git hooks can
execute on the Mac during a later launch or publication. The launcher overlays
all discovered top-level and nested `.devcontainer` directories, Git state,
effective hooks targets and aliases read-only. Unsupported linked and Git include
layouts are refused, and a saved policy fingerprint blocks stale reuse. The
container has no `CAP_SYS_ADMIN`, so it cannot remove these overlays. Edit sandbox
definitions on the host and rebuild. Verify the real mounts with the doctor before
relying on a new policy; a portable fixture is not a live mount check.

Build, install and release scripts elsewhere in the writable workspace remain
editable. Review their diff before running them on the host.

The Claude policy bundle is baked into root-owned image paths. It includes the shared
command guard, post-edit monitor, bounded formatter and exact project opt-ins. The
launcher streams the reviewed host checker and verifies every dependency against
current source before lifecycle scripts or credentials. No host hook mount is used.
Rebuild after changing the canonical LockBox bundle; stale policy stops startup.

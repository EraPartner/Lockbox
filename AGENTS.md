# AGENTS.md — LockBox

Agent guide and single source of truth for working in this repo. **This is
security-critical infrastructure**: the files here are vendored into every
sandbox in the fleet, so a mistake propagates to all of them. Read `REVIEW.md`
before proposing or committing a change.

## Project

LockBox is the **canonical devcontainer egress lock + shared launcher helpers**
for hardened, egress-locked coding-agent sandboxes configured by each deployment. The
two in-repo images support both Claude Code and OpenAI Codex; sibling images may
remain provider-specific until they adopt the same CLI block. It is
bash/shell + container infra — **no `package.json`, no build step, no app**. You
edit shell scripts and container/proxy config, lint with `shellcheck`, and
self-check with `./sync.sh --check` + `./audit.sh`.

It plays **two roles at once**:

1. **The canonical source.** The root-level `init-firewall.sh`, `squid.conf`,
   `base-allowlist.txt`, `clienthello-policy.py`, and `launcher-common.sh` are the ONE copy of the egress
   firewall, the SNI-allowlist proxy, and the host-side launcher helpers. Edit
   them **here**, run `./sync.sh`, and they are vendored (content + a provenance
   stamp) into every managed `.devcontainer/` listed in `paths.sh`. Before this
   repo existed each container carried its own drifting copy.
2. **Two of its own sandboxes.** `.devcontainer/` is the box for editing LockBox
   itself (launched by `.devcontainer/bin/claude` or `.devcontainer/bin/codex`).
   `sandbox/.devcontainer/` is the **generic full-dev sandbox**
   (`DEV_SANDBOX_AGENT=claude|codex sandbox/.devcontainer/bin/dev`) that routes any project directory into a
   hardened container. Both are themselves sync targets.

The runtime is **Apple's `container`** (apple/container), not Docker/Compose:
there is no `compose.yaml` and no `devcontainer.json`. `make test` and the smoke
test can also drive `docker` (CI does).

## Before any task

1. **Know which layer you are touching.** A root-level canonical file
   (`init-firewall.sh`, `squid.conf`, `clienthello-policy.py`, `base-allowlist.txt`,
   `launcher-common.sh`)
   affects the WHOLE fleet — after editing it you MUST run `./sync.sh` or the
   pre-commit `sync.sh --check` will block the commit. A per-project
   `allowlist.extra.txt` affects one container. The baked `allowlist.txt` is
   **GENERATED — never hand-edit it.**
2. **Read `README.md`** (repo overview + allowlist model) and
   **`.devcontainer/README.md`** (the full threat model for the sandbox itself).
   `CHANGELOG.md` records why each hardening decision was made — check it before
   "fixing" something that looks odd; it is usually load-bearing.
3. `paths.sh` discovers managed devcontainers. External targets and stable labels
   belong in ignored `paths.local.tsv` (see `paths.example.tsv`). Trust the resulting
   `EGRESS_DEVCONTAINERS` array over any prose count.

## How egress is enforced (the core model)

Two independent, **fail-closed** layers, applied by the root entrypoint on every start:

1. **iptables/ip6tables egress lock** (`init-firewall.sh`) — default-DROP first, then only the
   `proxy` UID may originate outbound packets. Everything else uses the proxy over loopback or is
   dropped. IPv6 OUTPUT is default-deny. Cloud metadata (`169.254.169.254` and `fd00:ec2::254`) is
   dropped before the proxy-UID accept. The root entrypoint writes `/run/egress-firewall-ok` only
   after verifying the policy and key rules with `iptables -C`; a missing sentinel fails startup.
2. **In-container squid SNI proxy** (`squid.conf`, peek+splice, **CONNECT-only**, HTTPS-only) —
   splices allowed hostnames without decrypting (**end-to-end TLS preserved, no MITM / CA
   injection**) and terminates the rest. A non-CONNECT absolute-URI request is denied.
   `clienthello-policy.py` checks the visible handshake because Squid can otherwise fall back
   to the CONNECT hostname when SNI is absent. Missing or malformed SNI and Encrypted
   ClientHello (ECH), including GREASE, are rejected. The helper compares SNI with CONNECT;
   the booted egress fixture verifies this with different allowlisted names. The entrypoint supervises Squid; the packet layer
   keeps direct egress denied while the proxy is down.

Defense-in-depth on top: **safe-chain** screens `npm`/`pip` installs against
`malware-list.aikido.dev`, and the **launch-integrity gate** (`bin/verify-pins`) aborts the launch
on toolchain drift.

This section is the provider-neutral source of truth. Claude's path-scoped
`.claude/rules/egress-internals.md` is a compatibility reminder and must not carry a conflicting
copy of the mechanism.

## Allowlist model (base + overlay)

`base-allowlist.txt` (the shared floor every container gets) plus
`<project>/.devcontainer/allowlist.extra.txt` (one project's deltas) are concatenated by `sync.sh`
into the baked `.devcontainer/allowlist.txt`, which is **GENERATED — DO NOT EDIT**. **Keep the base
minimal — anything added there widens egress for all managed containers.** To change egress: edit
the base or an extra, run `./sync.sh`, then **rebuild** the affected container so the new allowlist
is re-baked and re-read. LockBox and sibling provider launchers bake the list into the image and
warn when a reused container is stale. The generic sandbox bind-mounts its list and
restarts for changed or unconfirmed policy before recording the container generation
and mounted hash. It accepts a target-workspace overlay only after interactive confirmation; noninteractive
launches ignore that untrusted overlay and fail closed.

## Security invariants (do not weaken without explicit sign-off)

- **Allowlist minimalism.** Don't widen `base-allowlist.txt` casually; prefer a
  per-project `allowlist.extra.txt`. Justify every new host in a comment.
- **Generated files stay generated.** Never hand-edit a baked `allowlist.txt` or a
  vendored `init-firewall.sh`/`squid.conf`/`clienthello-policy.py`/`launcher-common.sh`; `sync.sh --check`
  will flag the drift.
- **Container stays non-root.** `dev` (UID 1000), no sudo, all setuid/setgid bits
  stripped image-wide. `container run` uses `--cap-drop ALL` then re-adds only
  `NET_ADMIN CHOWN DAC_OVERRIDE FOWNER SETUID SETGID` (needed for the entrypoint's
  iptables/perms-fix/squid drop). apple/container has **no `--security-opt`** — the
  per-container VM boundary is the isolation control; don't assume `no-new-privileges`.
- **No secrets baked in.** Both providers log in through their native CLI and keep
  authentication in private named volumes. Explicit environment authentication is
  optional; the in-repo launchers do not read Keychain or host login caches. Host
  native configuration is copied only through the curated importer, never mounted.
  `audit.sh` scans the git **index** for tokens and private keys. No Git push
  credential enters an editing sandbox.
- **Anti-tamper mounts.** Top-level and nested `.devcontainer` directories are
  overlaid read-only on the writable workspace. `.git`, every effective
  `core.hooksPath`, and workspace aliases to those hook targets are read-only.
  Unsupported symlink, hardlink and Git include layouts are refused. A saved
  mount-policy fingerprint prevents stale container reuse. Preserve these controls
  when touching launcher arguments; host-executed hooks are a VM-to-host escape.
- **Reproducible/pinned image.** Base image pinned by `@sha256`; Node and Python
  pinned by version + SHA-256-verified download; Claude and Codex CLIs pinned and installed
  into a root-owned prefix the `dev` user can't overwrite. Bump deliberately.
- **`paths.sh` is the drift guard.** A managed container missing from that array
  silently stops receiving the egress lock (the exact bug this repo exists to
  prevent). Keep it current; a missing target is a hard failure in `sync.sh`.

## How the launchers consume it

The in-repo entry points are:

- `.devcontainer/bin/claude` and `.devcontainer/bin/codex` edit LockBox itself.
  They select the provider and call `.devcontainer/bin/agent`. Providers use
  separate containers and private config volumes, but share the same image,
  egress policy, lifecycle, read-only git mounts, and pin gate.
- `sandbox/.devcontainer/bin/dev-claude` and `bin/dev-codex` target the current
  repository. Plain `bin/dev` defaults to Claude; `DEV_SANDBOX_AGENT=codex`
  selects Codex. The bare `.devcontainer/bin/agent` also defaults to Claude.
- Both providers use native private login, with optional explicit environment
  authentication. Claude uses `claude auth login`; Codex uses device-code login.
- Sibling projects keep their existing launchers. The generated firewall installs
  the root-owned ClientHello companion before publishing its readiness sentinel.
  Existing Dockerfile firewall COPY integration is sufficient. Successful fleet sync,
  rebuilds and runtime checks are required before deployment.

`.devcontainer/agent-config.py` and `.devcontainer/agent-config-launcher.sh` implement both
in-repo providers' configuration policy. `LOCKBOX_CONFIG_SOURCE` defaults to
`~/.config/lockbox/agents`, with shared `AGENTS.md`, `skills/`, `mcp.json` and native
`claude/`/`codex/` adapters. The default reads only this source. Selected native host
entries require `LOCKBOX_CONFIG_IMPORT_NATIVE=1`.
First initialization preserves private existing values. Ordinary starts reuse private
state. `LOCKBOX_CONFIG_REFRESH=1` or `DEV_SANDBOX_CONFIG_REFRESH=1` refreshes present
files with private rollback backups. Retired owned entries are removed only when
unchanged from the ownership record; local edits and private entries survive.
Host startup locks serialize lifecycle and policy changes and release before login
or interactive sessions. Private-volume locks serialize imports. Identical refreshes
avoid writes; five completed importer-owned backups are retained. An interrupted
transaction or incomplete rollback leaves a journal that blocks startup until private
recovery. Credentials, sessions, history and memory are
excluded; nothing is exported or copied back to the host.

Claude uses `CLAUDE_CONFIG_DIR=/home/dev/.claude`. Codex uses
`CODEX_HOME=/home/dev/.codex` and file credential storage. No in-repo Keychain lookup
or legacy global-state migration is needed. Import runs after the lifecycle barrier
and requires host Python 3.11 or newer on every launch. Host policy generation also
requires Node.js 18 or newer, checked before staging or builds. Both images bake the
root-owned Claude guard and managed settings; launch rejects missing or stale policy.

Both pinned CLIs read project `AGENTS.md`; the Claude builtin is enabled to read it
alongside Claude instruction files. Shared global instructions use a private native
Claude `CLAUDE.md` adapter. Host Claude `.claude.json` is never imported. Shared Model
Context Protocol (MCP) servers must be declared explicitly. User hooks are stripped
and plugin metadata retained, but command availability, plugin discovery and endpoint
access are not guaranteed. MCP environment forwarding uses the read-only host
snapshot as its name allowlist; private state cannot authorize new host variables.
Git push credential variables are refused.
See [`docs/provider-configuration.md`](docs/provider-configuration.md) for exact
imports, precedence and limits. Both launchers install cleanup before startup checks,
forward piped standard input and enforce the same strict cached pin gate. Runtime
proxy, mount and real-login checks remain required before deployment.

## Adding / modifying a sandbox

- **Change the firewall, proxy, or launcher helpers:** edit the root-level
  canonical file, `./sync.sh`, rebuild the affected container(s). If squid.conf
  changed, also `squid -k parse`.
- **Change egress for one project:** edit its `allowlist.extra.txt`, `./sync.sh`,
  rebuild. For all projects: edit `base-allowlist.txt` (justify it), `./sync.sh`.
- **Onboard a new managed container:** add its absolute `.devcontainer` path to
  ignored `paths.local.tsv`, then `./sync.sh`.
- **Add a new canonical vendored file:** add its name to `vendored-files.txt` (the
  single source shared by `sync.sh` and CI), then `./sync.sh`.
- **Cut a release:** bump `VERSION`, `./sync.sh` (restamps every vendored copy),
  update `CHANGELOG.md`, tag `v<VERSION>`.

## Validate a change (commands)

```bash
make setup            # ONCE per clone: git config core.hooksPath .githooks (else the gate is silently skipped)
shellcheck *.sh .devcontainer/bin/* sandbox/.devcontainer/bin/*   # lint (CI: -e SC1090,SC1091)
bash -n <script>      # parse check (CI runs this over every shebanged file)
make check            # ./sync.sh --check — vendored copies + generated allowlists match canonical (no writes)
make audit            # ./audit.sh — scan the git index for baked secrets / private keys
squid -k parse        # squid.conf syntax (only if you touched it; CI stages stub certs + allowlist)
make test             # ./test/egress-smoke.sh — BOOTS the image, asserts the lock enforces (needs a runtime; HOST only)
make native-launcher-test # real generic launcher fresh/warm/retry policy checks; requires generic test image
bash .devcontainer/bin/doctor   # in-container readiness (proxy up, off-allowlist blocked, .devcontainer RO, no push token)
```

CI (`.github/workflows/ci.yml`, aggregated as the required **CI Complete** check)
runs: `shell` (bash -n + shellcheck), `secrets` (audit.sh), `secrets-scan`
(gitleaks), `vendored` (`EGRESS_SELF_ONLY=1 ./sync.sh --check`), `dockerfile`
(hadolint), `squid` (squid -k parse), `egress-test` (build + boot + smoke), and
`trivy-scan` (misconfig). CodeQL (`codeql.yml`) analyzes only the Actions workflows
(shell/Dockerfile have no analyzer). The local `.githooks/pre-commit` runs
`audit.sh` + `sync.sh --check` on every commit.

## Verification (scale to risk)

- **low** (a comment, a doc, a scoped `allowlist.extra.txt` host) = `shellcheck` +
  `make check`.
- **medium** (any canonical file, launcher logic) = `shellcheck` + `make check` +
  `make audit` + rebuild one container + `bin/doctor`.
- **high** (firewall/proxy semantics, capabilities, mounts, an egress-widening
  change) = all of the above **plus** `make test` (boot the image and prove the
  lock still blocks off-allowlist + refuses non-CONNECT cleartext). Egress/isolation
  changes are the security surface — never ship one on lint alone.

## Key paths

| Path | What |
|---|---|
| `init-firewall.sh` | Canonical iptables egress lock (proxy-UID-only, default-deny) |
| `squid.conf` | Canonical SNI-allowlist proxy (peek+splice, CONNECT-only) |
| `clienthello-policy.py` | Canonical visible ClientHello validation helper |
| `.devcontainer/agent-config.py` | Curated native imports and shared provider adapters |
| `base-allowlist.txt` | Shared egress floor — widening here hits every container |
| `launcher-common.sh` | Canonical host-side launcher helpers (staging, traps, RO mount policy) |
| `sync.sh` / `paths.sh` | Vendoring + allowlist generation · the managed-container list |
| `audit.sh` | Secret/private-key leak scan over the git index |
| `Makefile` | `setup` `sync` `check` `audit` `test` |
| `test/egress-smoke.sh` | Boot-and-assert the egress lock enforces |
| `.devcontainer/` | Sandbox for editing LockBox itself (Claude or Codex) |
| `.devcontainer/bin/agent` | Shared provider-neutral launcher implementation; use the Claude/Codex entry points |
| `sandbox/.devcontainer/` | Generic full-dev sandbox (`bin/dev`, selected by `DEV_SANDBOX_AGENT`) |
| `.devcontainer/Dockerfile` | Hardened image (debian-slim @sha256, non-root `dev`, pinned Node/Python/Claude/Codex) |
| `.devcontainer/entrypoint.sh` | Root privileged setup: perms → LOCK EGRESS → start squid → keep-alive |
| `.devcontainer/allowlist.txt` | GENERATED per project — do not hand-edit |
| `VERSION` / `CHANGELOG.md` | Single-source version stamped into vendored copies · release history |
| `.github/workflows/ci.yml` | The CI pipeline (see above) |

## Git / commits

LockBox's ordinary `.devcontainer` editing sandboxes only edit and test the worktree. They mount
`.git` read-only, carry no push credential, and must not access the host SSH agent. Start the
separate `git-agent` launcher on the host when the diff is ready; its narrow LockBox container
reviews and stages explicit paths, creates the signed commit, and pushes. Configure publication credentials and signing in the publication environment. No
personal key or signing hardware is prescribed by this repository. The default branch
is `main` and the recommended hook path is `.githooks`. Write clear messages (what + why); update `README.md`,
`.devcontainer/README.md`, `CHANGELOG.md`, and this file when behavior changes; bump `VERSION`, run
`./sync.sh`, and tag when releasing.

## When stuck

`README.md` → `.devcontainer/README.md` (threat model) → `CHANGELOG.md` (why a
guard exists) → ask the user rather than weaken an invariant. If a change would
widen egress, add a capability, or relax a mount, surface it explicitly and let the
user decide — do not route around it.

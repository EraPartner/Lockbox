# Changelog

All notable changes to LockBox are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/). The single-source version lives in
[`VERSION`](VERSION); `sync.sh` stamps it (with each canonical file's SHA-256) into
every vendored copy, so a baked container self-identifies its egress-lock generation.

## [Unreleased]

### Fixed — Claude migration health checks

- Host launchers check the required Node.js 18 or newer before staging or builds;
  documented prerequisites now include this policy-generation dependency.
- Missing authored configuration sources produce a warning without enabling native
  imports. Existing private profiles still require deliberate refresh.
- Command checks catch downloaded scripts passed into intermediate pipeline stages
  and recursive removal prefixed with unrelated Git text. Reads of the exact reviewed
  LockBox pin manifest remain available without allowing unrelated environment files.
- Post-edit monitoring uses reviewed roots, including launches from subdirectories,
  and tracks shared and local permission settings separately. Rust formatting writes
  only the requested file and does not traverse child modules.
- CI now runs image identity, bundle integrity, host prerequisites, post-edit behavior
  and single-file formatter regression suites alongside the existing checks.

### Changed — shared Claude security policy

- Claude now uses the reviewed shared strict command guard and post-edit monitor.
  Images bake the guard, monitor, formatter, exact-root policy, installer and verifier.
  Launchers stream the host verifier and reject stale or writable dependencies before
  lifecycle scripts and credentials; host hook mounts are no longer required.
- Generic image variants retain approved project formatting and permission-monitor
  choices. Unknown roots and the publication profile opt out of automatic formatting.
- Local mock API fixtures exercise actual Claude Bash denial, Write monitoring,
  instruction loading, shared skill discovery, plugin activation and MCP connection.

### Changed — Claude migration defaults

- Bare editing `agent` and generic `dev` dispatchers now select Claude when no
  provider override is set. Explicit Claude/Codex adapters and environment
  selections retain their behavior; invalid provider names still fail.
- Portable readiness fixtures verify the default without accessing native login
  state or starting a real container.

### Fixed — image and mount policy on container reuse

- Shared launch policy now includes the inspected image descriptor and requires
  an OCI SHA-256 digest. Missing, malformed or changed descriptors prevent stale
  container reuse; private named volumes remain intact on explicit recreation.
- Downstream editing launchers use the shared nested read-only mount planner and
  reject obsolete creation policies before starting stopped containers or running
  lifecycle scripts. Host metadata must confirm the actual image digest. Cleanup
  covers selected containers while failed host preflight preserves existing sessions.
  Piped input is preserved, and Safe Chain startup runs only as the dev user.
- The publication launcher retains its separate Git boundary and adds image policy
  verification. The Brain adapter includes its scoped and external mounts and no
  longer calls the removed host configuration export helper.
- Added image identity fixtures. Runtime image inspection, recreation and egress
  enforcement still require checks on a host with Apple Container access.

### Changed — dependency maintenance and contributor guidance

- Updated the generic sandbox's pinned Debian base digest and the pinned checkout,
  hadolint, Buildx, build-push and CodeQL Actions from the dependency update proposals.
  Preserved the current CI regression checks and both-image runtime matrix.
- README now describes the pin updater's zero-day cooldown default, editing and publication
  boundaries, index-only audit coverage, runtime checks and branch cleanup criteria.
- The launcher readiness fixture consumes managed-policy input like the real
  validator, preventing intermittent broken-pipe failures in authentication tests.

### Changed — portable deployment configuration

- Removed personal dotfiles-generated cloud agreements and their CI checker.
  Cloud setup no longer overwrites global Codex instructions or shell startup files.

- Fresh clones manage only the two in-repository sandboxes. External target paths
  and optional stable labels now live in ignored `paths.local.tsv` data, with a
  portable example and `EGRESS_FLEET_FILE` override. Missing targets still fail;
  CI ignores local deployment data. Active guidance no longer prescribes personal
  checkout names or signing hardware.

### Changed — shared provider configuration and native login

- The authored common source is the default. `LOCKBOX_CONFIG_IMPORT_NATIVE=1`
  explicitly opts into selected native host entries; the default has no host
  instruction fallback. Every provider launch requires host Python 3.11 or newer.
- Refresh tracks imported ownership, removes retired unchanged entries, preserves
  private edits, replaces each MCP definition atomically, and rolls back deletions
  and readiness metadata on failure. Host and private-volume locks reject
  overlapping imports. Launch reports distinguish configuration presence from
  native discovery and connection.
- Both images bake a reviewed root-owned Claude guard and managed policy, and
  reject stale policy at launch. The guard is a canonical vendored JavaScript
  file. A clean host needs neither a Claude guard directory nor a Git config file.
- Both in-repo providers use official login in private native volumes: Claude
  `auth login` and Codex device login. Explicit environment authentication remains
  available. In-repo launchers no longer fetch Claude tokens from Keychain, import
  host auth caches, migrate legacy container global state, or copy host memory back.
- `LOCKBOX_CONFIG_SOURCE` defaults to `~/.config/lockbox/agents`. Shared instructions,
  skills and explicit MCP declarations are rendered into native adapters alongside
  an exact allowlist of authored native entries. Host Claude global `.claude.json`
  is never read. Imported user hooks are stripped; plugin metadata remains subject
  to Linux command, discovery and egress availability. MCP environment forwarding
  trusts the read-only host snapshot, not private agent-writable settings, and
  refuses Git push credential variables. The importer is in the protected
  `.devcontainer` launcher tree.
- Initialization preserves private values; explicit `LOCKBOX_CONFIG_REFRESH=1` or
  `DEV_SANDBOX_CONFIG_REFRESH=1` applies present authored files with rollback backups.
  Private credentials, history, sessions and memory are excluded. Safe links within
  curated roots are materialized; broken, escaping or private links are refused.
  Every provider launch requires host Python 3.11 or newer.
- Both pinned CLIs use project `AGENTS.md`. Claude's builtin instruction option reads
  it alongside Claude instruction files; common global guidance is rendered as a
  private native `CLAUDE.md`. Codex keeps `CODEX_HOME` and file credential storage.
- Removed fleet fish configuration export commands and host export traps. Cleanup
  only stops containers; sibling launch-time staging remains separate.

### Fixed — launcher and proxy policy checks

- Generic allowlist changes now restart the container before policy confirmation;
  desired bytes cannot stand in for applied policy after a failed transition.
  Confirmation includes the container generation and mounted policy hash. Both
  launchers serialize startup and fail if proxy readiness times out.
- Writable workspaces cannot overlap private host staging. Readiness skips private
  login profiles. Generic authored input uses a separate sibling mount source:
  Apple Container omitted the original directory mount when its child policy
  files were mounted separately, blocking first imports. Stale layouts require
  recreation, while private profiles survive. Missing host snapshots are restored
  without refreshing initialized private configuration.
- Configuration rollback records only successful mutations, continues restoration
  after individual errors, and blocks startup after an interrupted or incomplete
  rollback. Path rewriting respects full path boundaries. Unchanged imported
  plugins may transition between files and directories. Identical refreshes avoid
  writes; completed importer-owned backups are bounded to five, while failed and
  unrecognized backups remain available. Import errors do not expose authored values.
- Generic doctor checks now reject unreachable provider endpoints, direct egress
  and Git push credentials. Startup guidance directs publication to git-agent.
  The host Claude fish wrapper now selects Claude explicitly. Retired fish export
  shims, five old export snapshots and the obsolete host export lock were removed.
- Booted fixtures now use the actual CLIs to check shared skills, local MCP
  connections, global guidance and project `AGENTS.md`, plus an imported Claude
  plugin enabled with its native directory option. Simulated local model endpoints
  avoid real account credentials and API requests. Managed policy ownership and
  content are verified inside both images.
- Native settings adaptation applies only to root CLI configuration files, so
  nested plugin configuration keeps its own schema and private edits.
- Proxy log rotation and graceful stop signal Squid as its owning UID.
  The disabled PID file makes `squid -k` unusable, and the root capability set
  does not include `CAP_KILL`. Generic allowlist changes require a fresh start.
  Cleanup is installed before container reuse checks, so a failed transition also
  stops the selected container under the normal stop-on-exit policy.
- Both in-repo launchers forward piped standard input and enforce the same strict
  cached pin gate. Missing verifiers or manifests fail closed.
- Safe Chain startup hooks are supplied only to dev processes, so root startup
  cannot execute an agent-writable hook left by an earlier session. Nested plugin
  MCP/settings imports reject inline credentials before snapshot publication.
- Read-only overlays protect nested `.devcontainer` directories and effective Git
  hooks paths and their aliases. Unsupported symlink, hardlink and Git include
  layouts and nested Git repositories are refused. Container reuse checks a mount/configuration fingerprint;
  policy changes require recreation while private named volumes remain intact.
- Squid requires a positive visible ClientHello helper result before splicing.
  Missing or malformed SNI and ECH, including GREASE, are rejected without TLS
  decryption. The booted egress fixture checks CONNECT/SNI equality through a
  running Squid with different allowlisted names. Deployment also requires native
  feature discovery and real first-login checks.
- The generated firewall embeds and installs the canonical ClientHello companion
  as a root-owned executable before publishing the readiness sentinel. Existing
  fleet Dockerfile firewall COPY integration is sufficient. Deployment requires
  successful fleet sync, affected image rebuilds and runtime verification.

### Fixed — Claude readiness
- Both in-repo images extract Python without preserving archive ownership and
  remove group/other write permissions from its tree. The cached integrity gate
  requires root-owned, non-writable binary ancestry; preserving release-archive
  ownership made the generic sandbox's Python fail that check despite a matching
  fingerprint. Smoke tests now show the verifier's diagnostic instead of hiding it.
- LockBox's doctor now requires a real HTTP status from the provider endpoint;
  failed curl requests no longer appear reachable.
- LockBox installs shutdown cleanup before provider, lifecycle, and integrity
  checks. Early failures stop the container by default without syncing config
  back to the host.
- The generic sandbox allows Safe Chain's malware and package-age feeds through
  `malware-list.aikido.dev` in its own overlay.
- Provider setup documentation describes curated native imports, shared MCP
  declarations, private login and configuration, and feature availability limits.

### Changed — sandbox launch path
- Both in-repo sandboxes cache a successful binary-pin verification for one running
  container boot in a root-owned `/run` record. Cache hits remain bound to the
  verifier, manifest, service identity, and clean execution environment; cold,
  stale, or insecure states still run the full checker and fail closed.
- Sibling Claude staging membership lives in the vendored
  `claude-stage-items.txt`; sibling helpers validate that manifest. The in-repo
  providers use the shared curated importer instead.

### Changed — TODO review
- Sibling Claude credential forwarding returns as soon as a Keychain token is added,
  instead of rescanning the launch environment to rediscover the appended flag.
- Git-backed sibling workflows now compare their vendored `.devcontainer` files and generated
  allowlist with LockBox's public canonical `main` branch. Brain is treated as a second VaultLens
  checkout so its generated allowlist uses the same stable repository label.
- The dependency-free staged-secret audit remains the local pre-commit gate, with gitleaks kept as
  the broader CI backstop. The separate Squid parse job and entrypoint certificate/database
  recovery remain in place because they provide faster configuration diagnostics and a cheap
  runtime fail-safe, respectively.

### Fixed — agent engineering checks
- Self-only vendoring checks now cover both in-repository containers. Missing canonical inputs
  fail before any target write. `sync.sh --target=/absolute/managed/.devcontainer` allows a
  bounded regeneration while refusing targets outside the managed list.
- Continuous integration checks provider parity, isolated sync regressions, both Dockerfiles,
  and generated cloud instructions. Publication review uses the separate git-agent handoff
  protocol and keeps repository hooks disabled inside the publisher.

### Fixed — Codex sandbox prerequisite
- All nine managed images now install Debian's `bubblewrap` package, so Codex finds
  its Linux sandbox helper on `PATH` instead of warning and falling back to its
  bundled copy. The launch-integrity manifest fingerprints `bwrap` alongside
  the agent CLIs and shared tools, and the offline fleet gate now checks the
  package, fingerprint, and doctor coverage to prevent another partial rollout.

### Changed — explicit provider selection
- LockBox's Claude and Codex entry points now call a shared provider-neutral
  `bin/agent` implementation. The generic sandbox also provides explicit
  `dev-claude` and `dev-codex` entry points. Plain `dev` now requires
  `DEV_SANDBOX_AGENT` instead of silently defaulting to Claude.
- Launchers now print the selected provider and reject a reused container whose
  creation-time `SANDBOX_AGENT` is missing or mismatched. Lifecycle and doctor
  checks no longer silently interpret missing provider state as Claude, and
  diagnostics distinguish the selected provider from the two installed CLIs.
- Codex authentication now lives in the canonical launcher helper instead of
  being duplicated across the LockBox and generic launchers. A new offline
  provider-parity check gates the shared wrappers, lifecycle acceptance, CLI
  installation and pinning, private config volumes, and effective API egress for
  both Claude and Codex as part of `make check`.

### Added — provider-neutral in-repo sandboxes
- Both in-repo images now bake a reviewed, version-pinned OpenAI Codex CLI beside
  Claude Code. The Docker build verifies the selected per-architecture native
  Codex payload SHA-256, and the launch-time manifest fingerprints `codex` too.
- `.devcontainer/bin/codex` launches a separate LockBox container, while the
  generic sandbox selects Codex with `DEV_SANDBOX_AGENT=codex`. Provider-specific
  container names remain compatible.
- Codex gets a private native `~/.codex` volume and official device-code login on
  first interactive use. API-key and enterprise access-token login can bootstrap
  from runtime environment variables. Host Codex config/auth is never mounted.
- Minimal OpenAI API/auth hosts were added only to the two in-repo allowlist
  overlays, avoiding a fleet-wide base-allowlist expansion. `AGENTS.md` now gives
  Codex/ChatGPT the same security-critical repository guidance as Claude.

### Added — toolchain pinning + automated bumps
- **`tool-pins.env`** — single source of truth for the baked toolchain. Claude
  Code, Node, and Python are tracked across **all nine managed devcontainers** in
  seven repos; safe-chain and Codex are tracked where an image bakes them. Targets
  come from `paths.sh`, the same single source `sync.sh` and
  `audit.sh` use, so onboarding a container stays a one-line edit in one file.
  Previously `NODE_VERSION`, `PY_VERSION`, `PY_RELEASE` and the claude version were
  hand-duplicated across all nine Dockerfiles with nothing asserting they matched —
  the toolchain equivalent of the drift `paths.sh` exists to prevent. They agreed on
  Node/Python by luck; the claude pin had already split (2.1.173 in seven of nine,
  2.1.207 in two). All nine now build the same reviewed, hash-verified binary.
  `SAFE_CHAIN_VERSION` is checked only where the ARG exists — the other seven still
  install safe-chain unpinned at runtime (follow-up: baking it there also means
  editing each repo's `post-create.sh`).
- **`bump-pins.sh`** — resolver + drift gate. `--check` (offline) asserts
  `tool-pins.env` equals every Dockerfile's `ARG` defaults; `--report` shows pinned
  vs cooldown-eligible vs upstream-latest; `--write` resolves, re-hashes and
  rewrites all of them. A bare run is a dry run. Wired into `.githooks/pre-commit`,
  `make check`, and the CI `vendored` job.
- **Cooldown window** (`COOLDOWN_DAYS`, default 7) — a release younger than this is
  never selected. Recent npm compromises (chalk/debug, nx, shai-hulud) were
  detected and unpublished within ~24h, so a one-week hold removes essentially all
  zero-day-publish exposure at the cost of a few days of features.
- **Claude binary hash pin** — the `@anthropic-ai/claude-code` npm package is a
  ~20 KB *wrapper*; the real executable ships in a per-arch optionalDependency
  (`claude-code-linux-{arm64,x64}`) whose postinstall copies it over
  `bin/claude.exe`. Pinning the version alone therefore left the actual binary
  resolved from the registry and unverified. Both Dockerfiles now assert the
  SHA-256 of the installed native binary against a reviewed pin, and fail the
  build on mismatch.
- **`.github/workflows/bump-pins.yml`** — weekly job that runs the resolver and
  opens a PR (via `gh`, adding no third-party action). Never auto-merged: the
  version + hash diff is the reviewed anchor. Dependabot cannot cover this — it
  tracks only Actions and the Docker base image.
- **`make pins` / `pins-check` / `pins-report`** targets.

### Changed — supply-chain hardening
- **safe-chain is baked into the image** instead of `npm install -g`-ed at runtime
  in `post-create.sh`. The old path was an unpinned registry fetch executed inside
  the security boundary *before* safe-chain could screen anything — so a compromised
  safe-chain release was the one package guaranteed to land unscreened. Its 8
  transitive deps still resolve at build time and are **not** hash-pinned; this is a
  reviewed version pin, not a closure pin.
- **`DISABLE_AUTOUPDATER=1`** set explicitly. The root-owned npm prefix already
  prevented Claude Code's self-updater from replacing the pinned binary, but left
  enabled it retried every session, burning allowlisted egress and logging a
  confusing permission failure. In these images an upgrade is a rebuild.
- **`safe-chain` added to `binary-pins.txt`**, so the launch-time `verify-pins` gate
  fingerprints it alongside node/npm/claude/gh/git/python3.
- **`.devcontainer/Dockerfile` PATH order fixed** — the dev-writable
  `/home/dev/.npm-global/bin` sat *second* on PATH, ahead of `/usr/local/bin` and
  the system dirs, so a compromised agent could shadow the `sha256sum`/`awk` that
  `verify-pins` itself shells out to and defeat the gate before it ran.
  `sandbox/.devcontainer/Dockerfile` already had the hardened ordering (last on
  PATH) plus the rationale comment; this back-ports it.

### Fixed — apple/container build blockers (found while verifying the above)
- **`test/egress-smoke.sh`** — the build context was passed unnormalised
  (`$HERE/../sandbox/.devcontainer`). apple/container rejects a context path
  containing `..` with `"<repo>/sandbox is not a child of <repo>/test/../sandbox/
  .devcontainer"` and never starts the build, so `make test` could not run on the
  fleet's own runtime (docker tolerates it, which is why CI never caught it).
  Now resolved with `cd … && pwd -P`.
- **Dockerfile size ceiling guard** in `bump-pins.sh --check`. apple/container
  documents a 16384-byte limit and rejects larger files cleanly, but in practice
  the builder crashes well below it with the undiagnosable `Error: unavailable:
  "Stream unexpectedly closed."` and zero build output. Bisected on 2026-07-19:
  12267/14268/14306/14801 build; 15307 and above fail. The gate now refuses
  >14801 bytes and warns within 512 of it. Both Dockerfiles were trimmed to fit.
- **The claude pin step restores `bin/claude.exe` only when needed.** Running
  `install.cjs` unconditionally copies a 250 MB binary and exhausted the default
  builder VM, crashing the build the same opaque way. The image ships npm 11, so
  postinstall already places the binary and the copy is skipped; the final
  on-PATH hash check still guarantees correctness on npm 12+, where it does run.

### Changed — performance (TODO.md Pass 8 findings, no behavior changes intended)
- **sync.sh** — each vendored reference copy is generated once up front instead of
  once (check) or twice (sync) per file *per target*, cutting 18–36 `gen_vendored`/
  `shasum` runs to 3; this is per-commit latency via the pre-commit `--check`
  (~1–2s on macOS). Atomic install + write-verify semantics kept.
- **audit.sh** — the secret scan is a single `git grep --cached -I -nE` over the
  whole index instead of ~5–6 processes + a temp file per tracked file; same
  index-not-worktree semantics, `file:line`-only reporting. Also fixed: a bare
  `id_rsa`/`id_ed25519` at the repo *root* now trips the filename check (the old
  `*/id_rsa` pattern required a leading directory).
- **launcher-common.sh** — plugin/statusline staging excludes `.git` at copy time
  (`tar --exclude .git`) instead of copying multi-MB git dirs and deleting them,
  on the interactive sandbox-start path.
- **Dockerfile** — the image-wide setuid/setgid strip moved before the
  frequently-edited COPY block (a routine allowlist/script edit no longer re-pays
  a ~10–40s full-filesystem traversal per rebuild); a scoped strip over the
  COPY'd paths remains the last layer for the same defense-in-depth coverage.
- **bin/dev** — the proxy-wait folded into the lifecycle `container exec` (one
  fewer container-CLI round-trip per launch, ~100–300ms on warm reuse); proxy
  polls are 0.2s instead of 1s here and in `post-create.sh`.
- **CI** — the `egress-test` image build uses buildx with the GHA layer cache +
  `SKIP_BUILD=1` (cache-hit builds drop from ~4–8 min to seconds); CodeQL
  triggers scoped to the workflow/config paths it actually analyzes; the weekly
  schedule now runs only the rule-DB–dependent scans (Trivy + gitleaks), with the
  gate tolerating the by-design skips; Trivy scans once (SARIF + gating in one
  pass); the `quality-gate`/`ci-complete` pair collapsed into a single
  "CI Complete" job.

## [0.1.0] — 2026-07-07

First tagged release. Consolidates the 2026-07-07 hardening pass (the research
findings tracked in `TODO.md`) plus provenance + test tooling.

### Security
- **init-firewall.sh** — the post-apply sentinel now also verifies the two
  non-proxy loopback-DNS `DROP` rules (a silent insert failure no longer reopens a
  loopback-resolver exfil channel while still claiming "verified"); IPv6 loopback-DNS
  parity; L3 drops for the cloud-metadata IP (v4 + v6) ahead of the proxy-UID ACCEPT;
  inbound-port range validation (1..65535); sentinel-write failure now fails closed.
- **squid.conf** — CONNECT-only (`deny !CONNECT`, dropped `allow allowed_dom`), closing
  a cleartext / no-SNI downgrade where a non-CONNECT absolute-URI request bypassed the
  SNI allowlist.
- **audit.sh** — scans the git **index** (`git show :<file>`) instead of the working
  tree, closing a stage-then-clean bypass; repo-local (no cross-repo false positives);
  reports `file:line` only (never the secret); adds legacy-OpenAI / Stripe / AWS-STS
  formats; `.pem` gated on content.
- **allowlist** — `raw.githubusercontent.com` + `objects.githubusercontent.com` demoted
  off the shared base floor to the generic-sandbox extras only.
- **launcher-common.sh** — refuse a worktree/submodule `.git` file (fail-closed) rather
  than RO-mounting an ineffective pointer; escape plugin-path `sed` substitutions.

### Added
- **Vendored provenance stamps** — each baked egress file carries a `LockBox v<VERSION>
  · canonical sha256:<hash>` trailer; the drift check regenerates and verifies it.
- **Functional egress smoke test** — `test/egress-smoke.sh` boots the image and asserts
  the firewall sentinel, an off-allowlist host is blocked, an on-allowlist host is
  reachable, and non-CONNECT cleartext is refused. `make test`; CI `egress-test` job.
- **CI** — a `squid -k parse` job and an `egress-test` job that *builds and boots* the
  image (catching build breaks and asserting the lock enforces); `EGRESS_SELF_ONLY`
  self-check that also regenerates + compares the sandbox allowlist; weekly cron;
  CodeQL concurrency.
- **Bootstrap** — `Makefile` (`make setup` enables the pre-commit hook) + README
  "Setup / first run" documenting the `CODE_ROOT`/`BRAIN_DC`/`EGRESS_*` overrides.
- Shared `vendored-files.txt` manifest (single source for sync.sh + CI).

### Fixed
- **bin/dev** — `PORT_FLAGS` empty-array expansion crashed every fresh container
  create under `set -u` on bash 3.2; the on-exit stop/sync trap installed too late,
  orphaning the VM on an early abort; missing RO bind-mount sources gave opaque errors.
- **bin/doctor** — the Anthropic reachability check could never fail (`000` matched);
  `gh` missing from the toolchain loop; hard-fail on an absent token in a standalone exec.

### Changed
- **Vendoring invariant** — vendored copies are now *canonical content + a verified
  provenance stamp*, regenerated and compared (like `allowlist.txt`), rather than
  byte-identical. `sync.sh` writes allowlists atomically, normalizes them to `0644`,
  de-dupes extras against the base, and fails closed if the host count drops to zero.
- **Dockerfile** — the frequently-edited COPY block moved after cert-gen + pin-gen
  (keeping the setuid strip last) so a routine allowlist edit no longer invalidates the
  expensive layers.

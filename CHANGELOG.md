# Changelog

All notable changes to LockBox are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/). The single-source version lives in
[`VERSION`](VERSION); `sync.sh` stamps it (with each canonical file's SHA-256) into
every vendored copy, so a baked container self-identifies its egress-lock generation.

## [Unreleased]

### Added — toolchain pinning + automated bumps
- **`tool-pins.env`** — single source of truth for the baked toolchain
  (claude-code, safe-chain, Node, Python) across **all nine managed devcontainers**,
  in seven repos. Targets come from `paths.sh`, the same single source `sync.sh` and
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

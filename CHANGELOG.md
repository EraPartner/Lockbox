# Changelog

All notable changes to LockBox are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/). The single-source version lives in
[`VERSION`](VERSION); `sync.sh` stamps it (with each canonical file's SHA-256) into
every vendored copy, so a baked container self-identifies its egress-lock generation.

## [Unreleased]

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

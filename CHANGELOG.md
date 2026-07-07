# Changelog

All notable changes to LockBox are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/). The single-source version lives in
[`VERSION`](VERSION); `sync.sh` stamps it (with each canonical file's SHA-256) into
every vendored copy, so a baked container self-identifies its egress-lock generation.

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
- **CI** — `docker build` and `squid -k parse` jobs; `EGRESS_SELF_ONLY` self-check that
  also regenerates + compares the sandbox allowlist; weekly cron; CodeQL concurrency.
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

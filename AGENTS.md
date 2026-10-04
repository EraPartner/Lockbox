# AGENTS.md — LockBox

Agent guide and source of truth for this repo. **This is security-critical
infrastructure.** Its files are vendored into every managed sandbox, so a mistake
reaches all of them. Read `REVIEW.md` before proposing or committing a change.

The global working agreement applies (signing, publication, safety); this file lists
LockBox-specific rules only.

## Project

LockBox is the **canonical devcontainer egress lock and shared launcher helpers** for
hardened coding-agent sandboxes. It is shell and container infrastructure: no build
step, no app. The runtime is **Apple's `container`**, not Docker Compose (no
`compose.yaml`, no `devcontainer.json`). `make test` can also drive `docker` (CI does).

It has two roles:

1. **Canonical source.** The root files listed in `vendored-files.txt` are the one copy
   of the firewall, proxy, launcher helpers and baked Claude policy. Edit them here and
   run `./sync.sh`. It vendors them, with a provenance stamp, into every managed
   `.devcontainer/` found by `paths.sh`.
2. **Two sandboxes.** `.devcontainer/` edits LockBox itself (`.devcontainer/bin/claude`
   or `bin/codex`). `sandbox/.devcontainer/` is the generic full-dev sandbox for any
   project (`bin/dev-claude`, `bin/dev-codex`, or `bin/dev` with
   `DEV_SANDBOX_AGENT=claude|codex`). Both are also sync targets.

## Before any task

1. **Know which layer you touch.** A file in `vendored-files.txt` affects the whole
   fleet; run `./sync.sh` after editing it, or the pre-commit drift check blocks the
   commit. A per-project `allowlist.extra.txt` affects one container. A baked
   `allowlist.txt` is **generated; never hand-edit it.**
2. Read `README.md` and `.devcontainer/README.md` (threat model). Check `CHANGELOG.md`
   before "fixing" something odd; it records why each hardening decision exists.
3. Managed targets come from `paths.sh`; external ones go in the ignored
   `paths.local.tsv` (see `paths.example.tsv`). Trust `EGRESS_DEVCONTAINERS` over prose.

## How egress is enforced

Two independent, **fail-closed** layers, applied by the root entrypoint on every start:

1. **iptables/ip6tables lock** (`init-firewall.sh`). Default DROP first. Only the
   `proxy` UID may send outbound packets. IPv6 output is default-deny. Cloud metadata
   (`169.254.169.254`, `fd00:ec2::254`) is dropped before the proxy-UID accept.
   `/run/egress-firewall-ok` is written only after `iptables -C` verifies the rules; a
   missing sentinel fails startup.
2. **Squid SNI (Server Name Indication) proxy** (`squid.conf`): peek and splice, CONNECT-only, HTTPS-only, no
   TLS interception. `clienthello-policy.py` rejects missing or malformed SNI, ECH
   (Encrypted ClientHello, including GREASE), and SNI that differs from the CONNECT
   host. Direct egress stays denied while the proxy is down.

Also: **safe-chain** screens `npm`/`pip` installs, and `bin/verify-pins` aborts launch on
toolchain drift. This section is the provider-neutral source of truth;
`.claude/rules/egress-internals.md` must not carry a conflicting copy.

## Allowlist model

`sync.sh` joins `base-allowlist.txt` (shared floor) and a project's
`.devcontainer/allowlist.extra.txt` into the generated `allowlist.txt`. After a change,
run `./sync.sh` and **rebuild** the affected container. The generic sandbox accepts a
target-workspace overlay only after interactive confirmation; noninteractive launches
ignore it and fail closed.

## Security invariants (do not weaken without explicit user sign-off)

- **Allowlist minimalism.** A base entry widens egress everywhere. Prefer a
  per-project extra. Justify every new host in a comment.
- **Generated files stay generated.** Never hand-edit a baked `allowlist.txt`, a
  vendored copy, or a pin. `./sync.sh --check` and `./bump-pins.sh --check` flag drift.
- **Non-root container.** User `dev` (UID 1000), no sudo, setuid/setgid stripped.
  `--cap-drop ALL`, then only `NET_ADMIN CHOWN DAC_OVERRIDE FOWNER SETUID SETGID`.
  apple/container has no `--security-opt`; the per-container VM is the boundary.
- **No secrets baked in.** Native CLI login into private volumes. Host config enters
  only through the curated importer, never a mount. No Git push credential enters an
  editing sandbox.
- **Anti-tamper mounts.** All `.devcontainer` directories, `.git`, every effective
  `core.hooksPath` and aliases to hook targets are read-only. Host-executed hooks are a
  VM-to-host escape; preserve this when changing launcher arguments.
- **`paths.sh` is the drift guard.** A container missing from it silently loses the
  egress lock. A missing target is a hard failure in `sync.sh`.

## Provider configuration

`.devcontainer/agent-config.py` and `agent-config-launcher.sh` import curated host
config into private volumes; credentials, sessions and history are never imported.
See `docs/provider-configuration.md` for sources, refresh and limits.

## Common changes

- **Firewall, proxy or helpers:** edit the canonical root file, `./sync.sh`, rebuild.
  If `squid.conf` changed, also run `squid -k parse`.
- **Onboard a container:** add its path to `paths.local.tsv`, then `./sync.sh`.
- **New vendored file:** add it to `vendored-files.txt` (read by `sync.sh` and CI).
- **Tool pins:** versions and hashes live in `tool-pins.env`, mirrored as Dockerfile
  `ARG` defaults. `make pins-report` shows staleness, `./bump-pins.sh` dry-runs, and
  `make pins` resolves, rehashes and rewrites both. Review the diff, then run
  `make check` and `make test`. The weekly `bump-pins.yml` workflow opens a PR.
- **Release:** bump `VERSION`, `./sync.sh` (restamps copies), update `CHANGELOG.md`,
  tag `v<VERSION>`.

## Validate a change

The `Makefile` is authoritative; `make help` lists every target.

```bash
make setup     # once per clone: core.hooksPath=.githooks (else the gate is skipped)
shellcheck -e SC1090,SC1091 *.sh .devcontainer/bin/* sandbox/.devcontainer/bin/*
make check     # sync + pin drift, provider parity, pin and Python tests (Python 3.11+)
make audit     # scan the git index for secrets and private keys
make test      # boot the image and prove the lock (host only, needs a runtime)
make native-launcher-test     # generic launcher policy; needs the generic test image
bash .devcontainer/bin/doctor # in-container readiness
```

`.githooks/pre-commit` runs `audit.sh`, `sync.sh --check` and `bump-pins.sh --check`.
CI (`.github/workflows/ci.yml`, required check **CI Complete**) adds gitleaks,
hadolint, `squid -k parse`, image builds with the egress test, and Trivy.

Scale verification to risk:

- **Low** (doc, one scoped extra host): `shellcheck` + `make check`.
- **Medium** (canonical file, launcher logic): add `make audit`, rebuild one container,
  run `bin/doctor`.
- **High** (firewall or proxy semantics, capabilities, mounts, egress widening): add
  `make test`. Never ship an egress or isolation change on lint alone.

## Git and docs

Default branch is `main`. When behavior changes, update `README.md`,
`.devcontainer/README.md`, `CHANGELOG.md` and this file.

## When stuck

Read `README.md`, `.devcontainer/README.md`, then `CHANGELOG.md`. If a change would
widen egress, add a capability or relax a mount, surface it and let the user decide.

# LockBox

**Canonical devcontainer egress lock + shared launcher helpers for the sandbox fleet.**

Single source of truth for the egress firewall, the SNI-allowlist proxy, and the
host-side launcher helpers used by the Vision, Watchman, Brain, Napoleon-relay,
git-agent, and generic `sandbox` devcontainers. Previously each
container carried its own copy of `init-firewall.sh` + `squid.conf`; now you edit
them **here** and run `./sync.sh` — one edit instead of six.

The managed-container list lives in `paths.sh` (shared by `sync.sh` and `audit.sh`)
so it exists in ONE place — keep it current if a project moves, or the vendored
copies silently stop updating. That drift is exactly the bug that left 3 of 4
containers stale before this repo was introduced.

## Setup / first run

On a fresh clone, once:

```sh
make setup     # enable the tracked pre-commit gate: git config core.hooksPath .githooks
```

Without this the leak-audit + drift pre-commit gate is **silently skipped** — git
does not carry `core.hooksPath` across a clone. (`make help` lists the other
targets: `sync`, `check`, `audit`.)

`sync.sh` / `audit.sh` locate the managed sibling devcontainers via `paths.sh`,
which defaults to this machine's layout. Override in the environment if yours
differs:

| Variable | Default | Meaning |
|---|---|---|
| `CODE_ROOT` | `/Users/computer/Code` | Parent of the sibling repos (Vision, Watchman, Napoleon-relay, git-agent) |
| `BRAIN_DC` | `…/Brain/.devcontainer` | Brain's devcontainer (lives outside `CODE_ROOT`) |
| `EGRESS_REPO` | this repo's root (auto) | LockBox root; the in-repo `sandbox/.devcontainer` target derives from it |
| `EGRESS_SELF_ONLY` | `0` | `1` restricts sync/check to the in-repo `sandbox/.devcontainer` only (used by CI) |

A missing sibling target is a HARD failure in `sync.sh` (pass `--allow-missing` to
downgrade to a warning).

## Files

- `init-firewall.sh` — iptables default-deny; egress allowed only for the squid
  proxy UID. Identical everywhere — per-project bits are data files it reads:
  - `/etc/squid/allowlist.txt` — the hostname allowlist. **Generated** by `sync.sh`
    from `base-allowlist.txt` + the project's `allowlist.extra.txt`.
  - `/etc/egress/inbound-ports` — optional, one TCP port per line, for projects
    that publish services (Vision/Watchman). Absent = no inbound (Brain/git-agent).
- `squid.conf` — peek+splice SNI-allowlist proxy. Identical everywhere.
- `base-allowlist.txt` — the shared egress floor (Anthropic API + GitHub) needed by
  every container. Keep minimal; adding here widens egress for all six.
- `launcher-common.sh` — shared host-side launcher helpers (claude-config staging,
  Keychain credential forwarding, autosync-on-exit trap) sourced by every launcher
  (`bin/claude`, `bin/agent`, `bin/git-agent`, `bin/dev`). Vendored into each
  `.devcontainer/` by `sync.sh` — edit it HERE, not the copies.
- `paths.sh` — the canonical list of managed `.devcontainer` dirs, sourced by both
  `sync.sh` and `audit.sh`.
- `sync.sh` — vendors `init-firewall.sh` / `squid.conf` / `launcher-common.sh` into
  each project's `.devcontainer/` and generates each project's baked `allowlist.txt`
  from `base-allowlist.txt` + that project's `allowlist.extra.txt`. Each vendored copy
  is the canonical content plus a deterministic **provenance stamp** (`LockBox
  v<VERSION> · canonical sha256:…`), so a baked container self-identifies its
  egress-lock generation; the drift check regenerates and verifies that stamp rather
  than expecting byte-identical files.
- `VERSION` / `CHANGELOG.md` — the single-source version stamped into the vendored
  copies, and the release history. Bump `VERSION`, run `./sync.sh`, tag `v<VERSION>`.

  Not vendored: each project's `bin/verify-pins` (launch-integrity check) is a
  per-project, self-contained copy because its baked pin-manifest path differs.
- `audit.sh` — leak check: scans this repo's STAGED / tracked files via the git
  index (`git show :<file>`, so a stage-then-clean can't sneak a secret past it —
  and untracked/sibling files don't cause false positives) for hardcoded secrets
  and private keys, exiting non-zero on a hit. The sandboxes forward credentials at
  runtime and must never bake them in; it runs as the pre-commit gate and in CI.
  Catches GitHub / Anthropic / OpenAI / Stripe / AWS / Slack token formats and
  `BEGIN … PRIVATE KEY` blocks (public `.pem` certs are gated on content, not
  extension, so they don't false-positive).

## Allowlist model (base + overlay)

Each project keeps only its *deltas* in `.devcontainer/allowlist.extra.txt`; the
common Anthropic/GitHub hosts live once in `base-allowlist.txt`. `sync.sh`
concatenates them into the baked `.devcontainer/allowlist.txt` (which is
GENERATED — do not hand-edit). To change egress: edit `base-allowlist.txt` (all
containers) or a project's `allowlist.extra.txt` (one container), then `./sync.sh`
and rebuild. squid ignores `#`-comment and blank lines, so comments are safe.

## Toolchain pins (staying current, safely)

The images bake a Claude CLI, Node, Python and safe-chain. **They never fetch
"latest" at runtime**, and that is deliberate: a runtime `npm i -g` would make the
npm registry a trusted input *inside* the security boundary with no human in the
loop, and it would defeat `bin/verify-pins` (which fails closed on SHA-256 drift
and cannot pin a hash that is unknown before the build). Instead "latest" happens
at **build** time, behind review:

| | |
|---|---|
| `tool-pins.env` | single source of truth for every baked tool version + hash |
| `make pins-report` | pinned vs cooldown-eligible vs upstream latest — *"are we stale?"* |
| `make pins` | resolve, re-hash and rewrite the pins (`./bump-pins.sh` alone = dry run) |
| `make pins-check` | offline gate: `tool-pins.env` must match every Dockerfile `ARG` |

The Dockerfiles carry the pins as `ARG` defaults so a plain `container build` needs
no extra flags; `pins-check` (run by `.githooks/pre-commit`, `make check`, and CI)
fails if the two ever diverge. `.github/workflows/bump-pins.yml` runs the resolver
weekly and opens a PR — never auto-merged, because the version + hash diff *is* the
reviewed anchor for what the image may execute.

Two things worth knowing:

- **Cooldown.** `COOLDOWN_DAYS` (default 7) refuses any release younger than that.
  Recent npm compromises were caught and unpublished within ~24h, so the hold costs
  a few days of features and removes nearly all zero-day-publish exposure.
- **The claude pin is a *binary* hash, not just a version.** The
  `@anthropic-ai/claude-code` package is a ~20 KB wrapper; the real executable
  ships in a per-arch optionalDependency that its postinstall copies into place. So
  the build asserts the SHA-256 of the installed native binary. A hash change at an
  *unchanged* version means the registry served different bytes — investigate, do
  not merge.

Node stays within its pinned major line; crossing a major is a platform decision
and is reported but never applied automatically. `gh` and the apt packages stay
unpinned on purpose (mirrors drop old versions, which would break cache-miss
rebuilds); `verify-pins` covers them at launch instead.

## Workflow

The fleet runs on Apple's `container` (not Docker/Compose). Each project's
`.devcontainer/bin/<launcher>` does `container build` + `container run`; there are
no `compose.yaml` files (the old Docker Compose configs are archived under
`~/.claude-sandbox/docker-compose-archive/`).

```sh
# edit init-firewall.sh / squid.conf / base-allowlist.txt here, OR a project's
# .devcontainer/allowlist.extra.txt, then:
./sync.sh

# rebuild the affected container so the baked copies update — force-recreate via
# the launcher's REBUILD env (each launcher rebuilds its own image, cached/fast):
#   NAPOLEON_REBUILD=1 napoleon-claude   ·   WATCHMAN_REBUILD=1 watchman-claude
#   DEV_SANDBOX_REBUILD=1 dev            ·   GIT_AGENT_REBUILD=1 git-agent
#   VISION_REBUILD=1 vision-claude       ·   BRAIN_REBUILD=1 brain-claude
# or directly:  container build -t <image> <project>/.devcontainer

# before committing — verify nothing baked a secret in:
./audit.sh

# optionally — boot the image and prove the egress lock still enforces end-to-end
# (needs a container runtime; off-allowlist blocked + non-CONNECT cleartext refused):
make test
```

## Per-project wiring (set once)

Each project's Dockerfile bakes the synced files to GENERIC paths:
`COPY init-firewall.sh /usr/local/sbin/egress-firewall` and
`COPY squid.conf /etc/squid/squid.conf`. Its entrypoint calls
`/usr/local/sbin/egress-firewall` and checks `/run/egress-firewall-ok`; its
post-start hook checks the same sentinel. Projects with inbound services bake an
`/etc/egress/inbound-ports` file (via the Dockerfile).

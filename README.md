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
- `sync.sh` — copies `init-firewall.sh` / `squid.conf` / `launcher-common.sh` into
  each project's `.devcontainer/` and generates each project's baked `allowlist.txt`
  from `base-allowlist.txt` + that project's `allowlist.extra.txt`.

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
```

## Per-project wiring (set once)

Each project's Dockerfile bakes the synced files to GENERIC paths:
`COPY init-firewall.sh /usr/local/sbin/egress-firewall` and
`COPY squid.conf /etc/squid/squid.conf`. Its entrypoint calls
`/usr/local/sbin/egress-firewall` and checks `/run/egress-firewall-ok`; its
post-start hook checks the same sentinel. Projects with inbound services bake an
`/etc/egress/inbound-ports` file (via the Dockerfile).

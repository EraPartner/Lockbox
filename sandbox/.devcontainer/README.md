# Generic universal full-dev sandbox

One hardened container image, **any** project. Run `dev-claude` or `dev-codex` from inside any
directory and it routes *that* directory into an egress-locked sandbox and opens
Claude Code or OpenAI Codex there — no per-project `.devcontainer` required. This is the full-dev
counterpart to the commit-only `git-agent`: same security model, but the
workspace is **read-write** so you can edit and run code.

Use it for projects that don't have (and don't need) their own sandbox — e.g.
`scrim` and other ad-hoc work. Heavy, stack-specific projects (Vision's
Postgres+Bun, Watchman) keep their own `.devcontainer`.

> **Not a VS Code devcontainer.** Despite living under `.devcontainer/`, there is
> **no** `devcontainer.json` here — this is an Apple `container` (`container build`
> + `container run`) image built and driven entirely by `bin/dev`. VS Code
> "Reopen in Container" is not supported; the `bin/dev-claude` and `bin/dev-codex`
> wrappers select a provider and invoke the shared `bin/dev` implementation.

## Usage

```sh
cd ~/Code/scrim
dev-claude                    # Claude, against the scrim repo, RW
dev-codex                     # Codex with ChatGPT/device-code login
dev-codex --version           # args forward to Codex
DEV_SANDBOX_AGENT=codex dev   # equivalent environment-based selection
DEV_SANDBOX_PORTS="8787" dev-codex   # publish a container port to localhost
DEV_SANDBOX_SHELL=1 dev-codex        # bash shell with Codex's container state
```

Each target directory and provider gets its **own** container and private agent
volume (keyed by a hash of its path), so projects do not share agent state. The
launcher prints the selected provider and refuses stale containers whose saved
provider does not match. Both CLIs remain installed and integrity-checked; that
does not make both providers active in one session.

Plain `dev` without `DEV_SANDBOX_AGENT` fails with selection instructions. It
does not guess from the calling terminal or silently default to one provider.

## What's mounted / forwarded

| Thing | Mode | Note |
|---|---|---|
| target dir → `/workspaces/project` | **RW** | full dev |
| `.git` + `core.hooksPath` dir (e.g. `.githooks`) | RO | host-executed hooks — see Security model |
| `~/.claude` (sanitized stage) | RO | Claude sessions only; seeded in with no host secrets |
| `~/.codex` | private named volume | Codex config and runtime login cache; host config is not mounted |
| PreToolUse guard + managed-settings | RO | Claude sessions only; un-disableable safety hook |
| `~/.gitconfig` | RO | commit *identity* only |
| Provider auth | runtime only | Claude token env, or Codex device/API login in its private volume |
| git push token / ssh signing | **not forwarded** | push via `git-agent` |

## Egress

Locked to the squid allowlist like every sandbox. Effective allowlist =
the shared base + this sandbox's `allowlist.extra.txt` (a general dev floor:
npm, PyPI, GitHub, Claude) + an optional **per-target overlay**:

- drop a `.sandbox-allowlist.txt` (one host per line) in the target repo, or
- reuse a target's existing `.devcontainer/allowlist.extra.txt`.

Example — a project needs another provider, so `.sandbox-allowlist.txt` might contain:

```
example-provider.invalid
```

The generic sandbox already includes the minimal OpenAI Codex endpoints in its
own overlay; they are deliberately not in the fleet-wide base.

## Security model

- `--cap-drop ALL` + minimal caps, no sudo/setuid (apple/container has no
  `--security-opt`; the VM boundary is the isolation control).
- Root entrypoint locks egress (default-deny, proxy-UID-only) **before** the
  proxy starts; fail-closed. Dev sessions run unprivileged.
- **Launch-integrity pins**: `node/npm/claude/codex/gh/git/python3` are fingerprinted
  at build; `bin/dev` aborts if any drift before opening an agent. A real upgrade
  trips this — rebuild to re-pin with the selected provider, for example:
  `DEV_SANDBOX_REBUILD=1 dev-codex`.
- Workspace is RW and **no push credential** is present, so a compromised agent
  can alter local files but cannot push or exfiltrate beyond the allowlist.
- **Host-executed git paths are locked RO.** Git hooks run on your *Mac* (you
  commit/push on the host — `.git` is RO in-container and no push token is
  present). So `.git` **and** the effective `core.hooksPath` dir (Vision /
  Watchman / Brain / this repo relocate hooks to `.githooks`, *outside* `.git`)
  are bind-mounted `:ro` over the RW workspace by `sandbox_git_ro_mounts`
  (`launcher-common.sh`). Without this an agent could overwrite
  `.githooks/pre-commit`, and your next host `git commit` would run its code as
  you, with Keychain + ssh-agent in reach — a VM→host escape that bypasses the
  hypervisor. If `core.hooksPath` is set but the dir is absent, an empty RO dir
  is overlaid so the agent can't *create* one either (verified live: the write
  gets `EROFS`; the only cost is a spurious empty `<hooksPath>/` left in the
  workspace — cosmetic, and only in that misconfigured edge case).
- **Residual risk — other host-executed workspace files.** The RO lock covers the
  git paths because they can't be hand-enumerated but *can* be derived. Two
  classes are NOT locked, by design:
  - **Build / release / install scripts** (`package.json` scripts, `install.sh`,
    packaging post-install) run on the host at *release/build* time. They can't be
    RO-mounted without breaking the dev loop (the agent legitimately edits them).
    Mitigation is process: **review the diff before running any workspace build /
    release script on the host** — treat them like any other untrusted workspace
    content.
  - **Project-level Claude config** (`.claude/settings.json`, `settings.local.json`)
    defines hooks that execute if you run Claude Code *on the host* in this repo.
    Left RW so an in-container agent can edit project config normally. The host-side
    `managed-settings.json` + `claude-guard` (both mounted RO here) are expected to
    neutralize project-level hooks; verify that on your host if you rely on it.

## Health

Inside the container: `dev-sandbox-doctor`.

## Canonical files

`init-firewall.sh`, `squid.conf`, `allowlist.txt`, and `launcher-common.sh` are
**generated/vendored** by `LockBox/sync.sh` — do not hand-edit them
here. Edit the canonical copies in `LockBox/` (or this dir's
`allowlist.extra.txt`) and re-run `./sync.sh`.

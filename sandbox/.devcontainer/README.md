# Generic universal full-dev sandbox

One hardened container image, **any** project. Run `dev` from inside any
directory and it routes *that* directory into an egress-locked sandbox and opens
`claude` there — no per-project `.devcontainer` required. This is the full-dev
counterpart to the commit-only `git-agent`: same security model, but the
workspace is **read-write** so you can edit and run code.

Use it for projects that don't have (and don't need) their own sandbox — e.g.
`scrim` and other ad-hoc work. Heavy, stack-specific projects (Vision's
Postgres+Bun, Watchman) keep their own `.devcontainer`.

> **Not a VS Code devcontainer.** Despite living under `.devcontainer/`, there is
> **no** `devcontainer.json` here — this is an Apple `container` (`container build`
> + `container run`) image built and driven entirely by `bin/dev`. VS Code
> "Reopen in Container" is not supported; `bin/dev` (invoked as `dev`) is the only
> entry point.

## Usage

```sh
cd ~/Code/scrim
dev                       # claude, against the scrim repo, RW
dev --version             # args forward to claude
DEV_SANDBOX_PORTS="8787" dev   # publish a container port to localhost
DEV_SANDBOX_SHELL=1 dev        # bash shell instead of claude
```

Each target directory gets its **own** container and private home volumes (keyed
by a hash of its path), so projects never share Claude state.

## What's mounted / forwarded

| Thing | Mode | Note |
|---|---|---|
| target dir → `/workspaces/project` | **RW** | full dev |
| `.git` + `core.hooksPath` dir (e.g. `.githooks`) | RO | host-executed hooks — see Security model |
| `~/.claude` (sanitized stage) | RO | seeded in; no host secrets |
| PreToolUse guard + managed-settings | RO | un-disableable safety hook |
| `~/.gitconfig` | RO | commit *identity* only |
| Claude LLM token (Keychain) | env | `dev-sandbox-claude-code-token` |
| git push token / ssh signing | **not forwarded** | push via `git-agent` |

## Egress

Locked to the squid allowlist like every sandbox. Effective allowlist =
the shared base + this sandbox's `allowlist.extra.txt` (a general dev floor:
npm, PyPI, GitHub, Claude) + an optional **per-target overlay**:

- drop a `.sandbox-allowlist.txt` (one host per line) in the target repo, or
- reuse a target's existing `.devcontainer/allowlist.extra.txt`.

Example — scrim talks to OpenAI upstream, so `~/Code/scrim/.sandbox-allowlist.txt`:

```
api.openai.com
```

(`api.anthropic.com` is already in the base.)

## Security model

- `--cap-drop ALL` + minimal caps, no sudo/setuid (apple/container has no
  `--security-opt`; the VM boundary is the isolation control).
- Root entrypoint locks egress (default-deny, proxy-UID-only) **before** the
  proxy starts; fail-closed. Dev sessions run unprivileged.
- **Launch-integrity pins**: `node/npm/claude/gh/git/python3` are fingerprinted
  at build; `bin/dev` aborts if any drift before opening claude. A real upgrade
  trips this — rebuild to re-pin: `DEV_SANDBOX_REBUILD=1 dev`.
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

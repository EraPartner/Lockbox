# Generic universal full-dev sandbox

One hardened container image, **any** project. Run `dev-claude` from inside any
directory and it routes *that* directory into an egress-locked sandbox and opens
`claude` there — no per-project `.devcontainer` required. This is the full-dev
counterpart to the commit-only `git-agent`: same security model, but the
workspace is **read-write** so you can edit and run code.

Use it for projects that don't have (and don't need) their own sandbox — e.g.
`scrim` and other ad-hoc work. Heavy, stack-specific projects (Vision's
Postgres+Bun, Watchman) keep their own `.devcontainer`.

## Usage

```sh
cd ~/Code/scrim
dev-claude                       # claude, against the scrim repo, RW
dev-claude --version             # args forward to claude
DEV_SANDBOX_PORTS="8787" dev-claude   # publish a container port to localhost
DEV_SANDBOX_SHELL=1 dev-claude        # bash shell instead of claude
```

Each target directory gets its **own** container and private home volumes (keyed
by a hash of its path), so projects never share Claude state.

## What's mounted / forwarded

| Thing | Mode | Note |
|---|---|---|
| target dir → `/workspaces/project` | **RW** | full dev |
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
  trips this — rebuild to re-pin: `DEV_SANDBOX_REBUILD=1 dev-claude`.
- Workspace is RW and **no push credential** is present, so a compromised agent
  can alter local files but cannot push or exfiltrate beyond the allowlist.

## Health

Inside the container: `dev-sandbox-doctor`.

## Canonical files

`init-firewall.sh`, `squid.conf`, `allowlist.txt`, and `launcher-common.sh` are
**generated/vendored** by `devcontainer-egress/sync.sh` — do not hand-edit them
here. Edit the canonical copies in `devcontainer-egress/` (or this dir's
`allowlist.extra.txt`) and re-run `./sync.sh`.

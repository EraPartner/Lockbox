# AGENTS.md — LockBox

Agent guide and single source of truth for working in this repo. **This is
security-critical infrastructure**: the files here are vendored into every
sandbox in the fleet, so a mistake propagates to all of them. Read `REVIEW.md`
before proposing or committing a change.

## Project

LockBox is the **canonical devcontainer egress lock + shared launcher helpers**
for a fleet of hardened, egress-locked coding-agent sandboxes (Vision, Watchman,
Brain, Napoleon-relay, git-agent, dotfiles, VaultLens, and a generic dev box). The
two in-repo images support both Claude Code and OpenAI Codex; sibling images may
remain provider-specific until they adopt the same CLI block. It is
bash/shell + container infra — **no `package.json`, no build step, no app**. You
edit shell scripts and container/proxy config, lint with `shellcheck`, and
self-check with `./sync.sh --check` + `./audit.sh`.

It plays **two roles at once**:

1. **The canonical source.** The root-level `init-firewall.sh`, `squid.conf`,
   `base-allowlist.txt`, and `launcher-common.sh` are the ONE copy of the egress
   firewall, the SNI-allowlist proxy, and the host-side launcher helpers. Edit
   them **here**, run `./sync.sh`, and they are vendored (content + a provenance
   stamp) into every managed `.devcontainer/` listed in `paths.sh`. Before this
   repo existed each container carried its own drifting copy.
2. **Two of its own sandboxes.** `.devcontainer/` is the box for editing LockBox
   itself (launched by `.devcontainer/bin/claude` or `.devcontainer/bin/codex`).
   `sandbox/.devcontainer/` is the **generic full-dev sandbox**
   (`DEV_SANDBOX_AGENT=claude|codex sandbox/.devcontainer/bin/dev`) that routes any project directory into a
   hardened container. Both are themselves sync targets.

The runtime is **Apple's `container`** (apple/container), not Docker/Compose:
there is no `compose.yaml` and no `devcontainer.json`. `make test` and the smoke
test can also drive `docker` (CI does).

## Before any task

1. **Know which layer you are touching.** A root-level canonical file
   (`init-firewall.sh`, `squid.conf`, `base-allowlist.txt`, `launcher-common.sh`)
   affects the WHOLE fleet — after editing it you MUST run `./sync.sh` or the
   pre-commit `sync.sh --check` will block the commit. A per-project
   `allowlist.extra.txt` affects one container. The baked `allowlist.txt` is
   **GENERATED — never hand-edit it.**
2. **Read `README.md`** (repo overview + allowlist model) and
   **`.devcontainer/README.md`** (the full threat model for the sandbox itself).
   `CHANGELOG.md` records why each hardening decision was made — check it before
   "fixing" something that looks odd; it is usually load-bearing.
3. `paths.sh` is the authoritative list of managed devcontainers. `README.md`
   prose still says "six" in places — trust `paths.sh`'s `EGRESS_DEVCONTAINERS`
   array over any prose count.

## How egress is enforced (the core model)

Two independent, **fail-closed** layers, applied by the root entrypoint on every start:

1. **iptables/ip6tables egress lock** (`init-firewall.sh`) — default-DROP first, then only the
   `proxy` UID may originate outbound packets. Everything else uses the proxy over loopback or is
   dropped.
2. **In-container squid SNI proxy** (`squid.conf`, peek+splice, **CONNECT-only**, HTTPS-only) —
   splices allowed hostnames without decrypting (**end-to-end TLS preserved, no MITM / CA
   injection**) and terminates the rest.

Defense-in-depth on top: **safe-chain** screens `npm`/`pip` installs against
`malware-list.aikido.dev`, and the **launch-integrity gate** (`bin/verify-pins`) aborts the launch
on toolchain drift.

Full mechanism — sentinel verification, IPv6 and cloud-metadata handling, ECH,
squid supervision, allowlist composition, baked vs bind-mounted — is documented
in `.claude/rules/egress-internals.md`. Codex/ChatGPT agents must read it directly
when touching those files; Claude loads it through its path-scoped rule.

## Allowlist model (base + overlay)

`base-allowlist.txt` (the shared floor every container gets) plus
`<project>/.devcontainer/allowlist.extra.txt` (one project's deltas) are concatenated by `sync.sh`
into the baked `.devcontainer/allowlist.txt`, which is **GENERATED — DO NOT EDIT**. **Keep the base
minimal — anything added there widens egress for all managed containers.** To change egress: edit
the base or an extra, run `./sync.sh`, then **rebuild** the affected container so the new allowlist
is re-baked and re-read.

## Security invariants (do not weaken without explicit sign-off)

- **Allowlist minimalism.** Don't widen `base-allowlist.txt` casually; prefer a
  per-project `allowlist.extra.txt`. Justify every new host in a comment.
- **Generated files stay generated.** Never hand-edit a baked `allowlist.txt` or a
  vendored `init-firewall.sh`/`squid.conf`/`launcher-common.sh`; `sync.sh --check`
  will flag the drift.
- **Container stays non-root.** `dev` (UID 1000), no sudo, all setuid/setgid bits
  stripped image-wide. `container run` uses `--cap-drop ALL` then re-adds only
  `NET_ADMIN CHOWN DAC_OVERRIDE FOWNER SETUID SETGID` (needed for the entrypoint's
  iptables/perms-fix/squid drop). apple/container has **no `--security-opt`** — the
  per-container VM boundary is the isolation control; don't assume `no-new-privileges`.
- **No secrets baked in.** Claude credentials are forwarded at runtime. Codex
  credentials are created at runtime by official login and remain in the private
  native `~/.codex` volume; host `~/.codex` is never mounted. `audit.sh` scans the
  git **index** for tokens and private keys. No git push credential enters a sandbox.
- **Anti-tamper mounts.** `.devcontainer` is re-mounted **read-only over** the RW
  workspace so an in-container agent can't rewrite the host launcher/Dockerfile and
  escape on the next launch. `.git` is mounted **read-only**, and any relocated
  `core.hooksPath` (e.g. `.githooks`) is locked RO too — a host-executed git hook
  is a VM→host escape. Preserve all three when touching launcher run args.
- **Reproducible/pinned image.** Base image pinned by `@sha256`; Node and Python
  pinned by version + SHA-256-verified download; Claude and Codex CLIs pinned and installed
  into a root-owned prefix the `dev` user can't overwrite. Bump deliberately.
- **`paths.sh` is the drift guard.** A managed container missing from that array
  silently stops receiving the egress lock (the exact bug this repo exists to
  prevent). Keep it current; a missing target is a hard failure in `sync.sh`.

## How the fish launchers consume it

The in-repo entry points are:

- `.devcontainer/bin/claude` and `.devcontainer/bin/codex` edit LockBox itself.
  They use separate containers and private provider config volumes, but share the
  same image, egress policy, lifecycle, read-only git mounts, and pin gate.
- `sandbox/.devcontainer/bin/dev` targets the current repository. Claude remains
  the compatibility default; set `DEV_SANDBOX_AGENT=codex` for Codex.
- Claude credentials are forwarded at runtime. Codex uses its private `~/.codex`
  volume and starts official device-code login on first interactive use; an
  `OPENAI_API_KEY` or enterprise `CODEX_ACCESS_TOKEN` can bootstrap login instead.
- Sibling projects keep their existing launchers and vendored egress helpers.

## Adding / modifying a sandbox

- **Change the firewall, proxy, or launcher helpers:** edit the root-level
  canonical file, `./sync.sh`, rebuild the affected container(s). If squid.conf
  changed, also `squid -k parse`.
- **Change egress for one project:** edit its `allowlist.extra.txt`, `./sync.sh`,
  rebuild. For all projects: edit `base-allowlist.txt` (justify it), `./sync.sh`.
- **Onboard a new managed container:** add its `.devcontainer` path to the
  `EGRESS_DEVCONTAINERS` array in `paths.sh`, then `./sync.sh`.
- **Add a new canonical vendored file:** add its name to `vendored-files.txt` (the
  single source shared by `sync.sh` and CI), then `./sync.sh`.
- **Cut a release:** bump `VERSION`, `./sync.sh` (restamps every vendored copy),
  update `CHANGELOG.md`, tag `v<VERSION>`.

## Validate a change (commands)

```bash
make setup            # ONCE per clone: git config core.hooksPath .githooks (else the gate is silently skipped)
shellcheck *.sh .devcontainer/bin/* sandbox/.devcontainer/bin/*   # lint (CI: -e SC1090,SC1091)
bash -n <script>      # parse check (CI runs this over every shebanged file)
make check            # ./sync.sh --check — vendored copies + generated allowlists match canonical (no writes)
make audit            # ./audit.sh — scan the git index for baked secrets / private keys
squid -k parse        # squid.conf syntax (only if you touched it; CI stages stub certs + allowlist)
make test             # ./test/egress-smoke.sh — BOOTS the image, asserts the lock enforces (needs a runtime; HOST only)
bash .devcontainer/bin/doctor   # in-container readiness (proxy up, off-allowlist blocked, .devcontainer RO, no push token)
```

CI (`.github/workflows/ci.yml`, aggregated as the required **CI Complete** check)
runs: `shell` (bash -n + shellcheck), `secrets` (audit.sh), `secrets-scan`
(gitleaks), `vendored` (`EGRESS_SELF_ONLY=1 ./sync.sh --check`), `dockerfile`
(hadolint), `squid` (squid -k parse), `egress-test` (build + boot + smoke), and
`trivy-scan` (misconfig). CodeQL (`codeql.yml`) analyzes only the Actions workflows
(shell/Dockerfile have no analyzer). The local `.githooks/pre-commit` runs
`audit.sh` + `sync.sh --check` on every commit.

## Verification (scale to risk)

- **low** (a comment, a doc, a scoped `allowlist.extra.txt` host) = `shellcheck` +
  `make check`.
- **medium** (any canonical file, launcher logic) = `shellcheck` + `make check` +
  `make audit` + rebuild one container + `bin/doctor`.
- **high** (firewall/proxy semantics, capabilities, mounts, an egress-widening
  change) = all of the above **plus** `make test` (boot the image and prove the
  lock still blocks off-allowlist + refuses non-CONNECT cleartext). Egress/isolation
  changes are the security surface — never ship one on lint alone.

## Key paths

| Path | What |
|---|---|
| `init-firewall.sh` | Canonical iptables egress lock (proxy-UID-only, default-deny) |
| `squid.conf` | Canonical SNI-allowlist proxy (peek+splice, CONNECT-only) |
| `base-allowlist.txt` | Shared egress floor — widening here hits every container |
| `launcher-common.sh` | Canonical host-side launcher helpers (staging, Keychain, traps, RO git mounts) |
| `sync.sh` / `paths.sh` | Vendoring + allowlist generation · the managed-container list |
| `audit.sh` | Secret/private-key leak scan over the git index |
| `Makefile` | `setup` `sync` `check` `audit` `test` |
| `test/egress-smoke.sh` | Boot-and-assert the egress lock enforces |
| `.devcontainer/` | Sandbox for editing LockBox itself (Claude or Codex) |
| `sandbox/.devcontainer/` | Generic full-dev sandbox (`bin/dev`, selected by `DEV_SANDBOX_AGENT`) |
| `.devcontainer/Dockerfile` | Hardened image (debian-slim @sha256, non-root `dev`, pinned Node/Python/Claude/Codex) |
| `.devcontainer/entrypoint.sh` | Root privileged setup: perms → LOCK EGRESS → start squid → keep-alive |
| `.devcontainer/allowlist.txt` | GENERATED per project — do not hand-edit |
| `VERSION` / `CHANGELOG.md` | Single-source version stamped into vendored copies · release history |
| `.github/workflows/ci.yml` | The CI pipeline (see above) |

## Git / commits

Commit & push happen on the **host** (the sandboxes mount `.git` read-only and
carry no push credential). This repo **signs commits** (`commit.gpgsign=true`,
`gpg.format=ssh`, Secure-Enclave `sk` key); default branch is `main` and
`core.hooksPath=.githooks`. Write clear messages (what + why); update
`README.md`/`.devcontainer/README.md`/`CHANGELOG.md`/this file when behavior
changes; bump `VERSION` + re-`sync.sh` + tag when releasing.

## When stuck

`README.md` → `.devcontainer/README.md` (threat model) → `CHANGELOG.md` (why a
guard exists) → ask the user rather than weaken an invariant. If a change would
widen egress, add a capability, or relax a mount, surface it explicitly and let the
user decide — do not route around it.

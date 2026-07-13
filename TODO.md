# TODO — Research Findings & Improvements

This file is the shared work list for LockBox improvements. It is written
incrementally by research agents so progress survives interruptions.

## Implementation status — 2026-07-07

**56 of 57 findings implemented and committed; 1 deferred.** All checked items below
(`[x]`) landed in the commits mapped here; canonical changes were propagated to the
six devcontainers via `./sync.sh` (the five sibling repos — Vision, Watchman,
Napoleon-relay, git-agent, Brain — were committed separately in their own repos).

| Commit | Area |
|---|---|
| `a0f5972` | `bin/dev`: bash-3.2 `PORT_FLAGS` crash, orphaned-VM trap, mount pre-flight |
| `1116219` | `audit.sh`: scan the git INDEX (closes stage-then-clean bypass), regex/reporting |
| `7655091` | `init-firewall.sh`: DNS-drop verification, IPv6 + metadata hardening, port guard |
| `9fcaf89` | `squid.conf`: CONNECT-only (closes cleartext/no-SNI downgrade) |
| `1304523` | `launcher-common.sh`: sed-escape plugin paths, refuse worktree `.git` |
| `a5d44da` | `sync.sh`/`paths.sh`: atomic+fail-closed allowlist gen, dedup, self-check mode |
| `91538a8` | allowlist: demote `raw`/`objects.githubusercontent.com` off the shared floor |
| `78b1c7f` | sandbox: doctor checks, Dockerfile layer order, perms-fix/README/post-start docs |
| `d9ed6bb` | CI: allowlist-regen check, gate hardening, hook bootstrap |
| `67a7a51` | top-level README: Setup/first-run section, corrected `audit.sh` description |
| `2f0476e` | `VERSION` (0.1.0) + `CHANGELOG` |
| `133082a` | vendored **provenance stamps** (version + canonical SHA-256); invariant → regenerate-and-compare |
| `a21a84f` | **functional egress test** (`test/egress-smoke.sh`, `make test`) + CI `egress-test` boot job |

**Provenance stamp / functional test / versioning** (originally deferred) were done
in the follow-up: the stamp is a *content* SHA-256 + version (a git-commit SHA can't
be self-referential and isn't reproducible for `--check`), so the invariant became
"regenerate canonical+stamp and compare" — verifiable, self-certifying, and churns
only on real change. The egress test's gating assertions are internet-independent, so
it runs as a CI gate.

**Runtime-validated by booting the image under Apple `container`** (not just linted):
the `init-firewall.sh` `iptables -C` conjunction + new v4/v6 DNS + metadata DROP rules
(sentinel written), and the `squid.conf` CONNECT-only change (off-allowlist blocked,
non-CONNECT cleartext → HTTP 403) — `test/egress-smoke.sh` passed 5/5, including
against the stamped image. The CI `egress-test`/`squid -k parse` jobs run this in
Actions on the next push.

**Deferred (the 1 remaining `[ ]` item), with rationale:**

- **Fleet drift CI in sibling repos** (`ci.yml:95-116`) — requires authoring +
  testing new CI workflows across five heterogeneous repos (Napoleon-relay is not
  even a git repo; git-agent has zero workflows). The `sync.sh` propagation keeps
  them in sync now, and each vendored copy now carries a version/SHA stamp; LockBox's
  own CI verifies its in-repo copy via `EGRESS_SELF_ONLY=1 sync.sh --check`.
  Recommended as a follow-up fleet task.

Note: **[P3] `base-allowlist.txt:24` `api.github.com`** is checked off as ACCEPTED —
it is inherent to `gh`/git in every container, and moving it to per-`gh` extras would
only relocate the same POST-capable capability, not remove it.

## How to use this file (for the next agent)

- Each finding has a checkbox, a severity tag, and file references.
- Severity: **[P1]** bug / broken behavior · **[P2]** risky / should fix soon · **[P3]** cleanup / nice-to-have.
- When you fix an item, check it off and note the commit hash next to it.
- Do NOT delete existing items; append new findings under the matching section.

## Research progress tracker

- [x] Pass 1: Core shell scripts (init-firewall.sh, launcher-common.sh, sync.sh, paths.sh, audit.sh, squid.conf, base-allowlist.txt) — DONE (findings below)
- [x] Pass 2: Sandbox devcontainer (sandbox/.devcontainer/*: Dockerfile, entrypoint, post-create/start, bin/*, perms-fix) — DONE (findings below)
- [x] Pass 3: CI / DevOps (.github/workflows/*, dependabot, .githooks/pre-commit, .trivyignore, codeql config) — DONE (findings below)
- [x] Pass 4: Design / functionality / docs (README, vendoring/sync design, allowlist management, performance) — DONE (findings below)
- [x] Pass 5 (deep dive): egress-enforcement chain security/correctness — init-firewall.sh, squid.conf, entrypoint.sh, bin/verify-pins (bypass routes, rule ordering, races, fail-open paths) — DONE 2026-07-05 (findings below; corrected the Pass 1 squid.conf:46 item)
- [x] Pass 6 (deep dive): launcher & container runtime — launcher-common.sh (full), bin/dev, bin/doctor, post-create.sh, post-start.sh, perms-fix.sh, Dockerfile — DONE 2026-07-05 (findings below; 2 lifecycle P2s)
- [x] Pass 7 (deep dive): tooling & repo hygiene — sync.sh, paths.sh, audit.sh, base-allowlist.txt, allowlist.extra.txt, .githooks/pre-commit, CI workflows, README — DONE 2026-07-05 (findings below; leak-gate scans worktree not index)

Passes 1–4 are complete (2026-07-05); passes 5–7 are deeper second-opinion sweeps over the
same files (new findings only, deduped against passes 1–4). A future agent picking this up
should finish any unchecked pass above, then work the unchecked items below, starting with
the P2s. See "Suggested starting order" at the end.

---

## Findings

### Pass 1 — Core shell scripts

Severity note from the researcher: `init-firewall.sh` verifies exactly four invariants post-apply
(v4/v6 `OUTPUT DROP`, proxy-UID ACCEPT, `EGRESS_DENY` jump); failures of those four are fail-closed,
failures of anything *not* verified are fail-open. That drives the P2 below.

#### Bugs

- [x] **[P2]** `init-firewall.sh:62-63, 80, 87` — Post-apply verification (lines 103-113) checks only four invariants; the non-proxy loopback DNS drops, the inbound-port ACCEPTs, and `extra-rules.sh` are never verified, yet with `set -uo pipefail` (no `-e`) a mid-script `iptables -A` failure does not abort. *Failure:* under a Docker embedded resolver (127.0.0.11), if line 62's loopback-DNS DROP fails to insert, non-proxy loopback DNS is silently reopened as a data-exfil channel while line 108 still prints "verified" and drops the OK sentinel. *Fix:* add the two DNS-drop rules to the `iptables -C` verification conjunction before writing the sentinel.
- [x] **[P3]** `init-firewall.sh:79-80` — Inbound-port validation `^[0-9]+$` accepts `0` and values above 65535. *Failure:* an `inbound-ports` line of `99999` passes the regex, `iptables --dport 99999` errors out, and (no `-e`) the port is silently never opened, so the published service is unreachable with no diagnostic. *Fix:* tighten the guard to 1..65535 and warn on skip.
- [x] **[P3]** `init-firewall.sh:107-108` — `: > "$SENTINEL" 2>/dev/null || true` swallows a sentinel-write failure but still falls through to the "verified" success message. *Failure:* if `/run` is not writable the firewall is applied but the sentinel is absent, yet the log claims success; the entrypoint's wait-for-sentinel then stalls with misleading logs. *Fix:* branch to the error path when the sentinel write fails.
- [x] **[P3]** `launcher-common.sh:36` — `sed -i '' -e "s#$HOME/.claude#...#g"` interpolates `$HOME`/`$src` unescaped with `#` as delimiter. *Failure:* a host `$HOME` containing `#` or a sed metacharacter produces a malformed `s###` expression, so plugin path rewriting silently fails and the container's `installed_plugins.json` keeps host paths. *Fix:* escape the interpolated values or do the substitution with jq/awk.

#### Performance

- [x] **[P3]** `audit.sh:52-55` — `scan_one` spawns one `grep -nE` per pattern (11 patterns) for every scanned file → 11× subprocess count. *Failure:* pre-commit gate is noticeably slow on large trees. *Fix:* combine patterns into a single alternation `grep -nE '(p1|p2|...)'` (per-pattern labels are unused anyway).
- [x] **[P3]** `audit.sh:19-23, 75` + `paths.sh:32` — Repo root `$HERE` is scanned recursively AND `sandbox/.devcontainer` (inside the repo) is scanned again via `EGRESS_DEVCONTAINERS`. *Failure:* files under `sandbox/.devcontainer` scanned twice; real findings reported twice. *Fix:* dedupe `ROOTS` (skip devcontainer entries already under `$HERE`).

#### Design

- [x] ~~**[P3]** `squid.conf:46` — `http_access allow allowed_dom` is dead config~~ **← CORRECTED by Pass 5: NOT dead config. It is reachable by non-CONNECT absolute-URI forward-proxy requests and permits a cleartext/no-SNI downgrade. See the Pass 5 P2 item below; treat that one as the live finding and disregard this "dead config" characterization.** (Live fix landed in `9fcaf89`.)
- [x] **[P3]** `sync.sh:90` — In `--check` mode the `synced` counter increments per target even though nothing is written, so the success message mislabels a verify-only run as "N vendored copies synced". *Fix:* separate `checked` counter for check-mode messaging.

#### Functionality

- [x] **[P3]** `audit.sh:22-23` — Per-project install-script list is hardcoded to `Vision/install.sh` and `Watchman/install.sh`. *Failure:* currently no gap (only those two exist), but when another managed project adds an `install.sh` it is silently excluded from the secret scan the script claims to enforce "per-project". *Fix:* derive candidate install scripts from `EGRESS_DEVCONTAINERS`/project roots.
- [x] **[P3]** `launcher-common.sh:191` — `sandbox_git_ro_mounts` gates on `[[ -e "$repo/.git" ]]`, true also when `.git` is a *file* (worktree/submodule), then mounts `$repo/.git` RO without following the `gitdir:` pointer. *Failure:* latent (all current repos use a `.git` dir) — but a worktree/submodule conversion would leave the real gitdir writable, silently reopening the host-hook escape this function exists to close. *Fix:* resolve `git rev-parse --git-common-dir` and mount that RO, or refuse with an error.

### Pass 2 — Sandbox devcontainer (`sandbox/.devcontainer/`)

Context established by the researcher:
- Vendored copies (`init-firewall.sh`, `squid.conf`, `launcher-common.sh`) are **byte-identical** to the top-level canonical versions — no drift.
- `devcontainer.json` is absent **intentionally**: the image is "devcontainer-FREE" (`Dockerfile:1`), built/run entirely by `bin/dev` via apple/container. It works without it; the naming is just misleading (see Functionality).

#### Bugs

- [x] **[P2]** `sandbox/.devcontainer/bin/doctor:42` — The Anthropic reachability check can never fail: on connection failure curl still prints `000` via `-w '%{http_code}'`, and `000` matches `^[0-9]`, so `ok "Anthropic API reachable"` always fires. *Failure:* during a total egress/proxy outage doctor still reports the API reachable; the "could not reach" branch is dead code. *Fix:* capture the code and test `[[ "$code" =~ ^[1-5] ]]` (or check curl's exit status), treating `000` as unreachable.
- [x] **[P3]** `sandbox/.devcontainer/perms-fix.sh:5-6` — Comment claims the container "runs with no-new-privileges", contradicting `entrypoint.sh:7-8` and `README.md:57` which state apple/container has *no* such equivalent. *Failure:* a reader trusts a hardening control that does not exist. *Fix:* delete the clause to match the entrypoint/README wording.

#### Performance

- [x] **[P3]** `sandbox/.devcontainer/Dockerfile:114-126` — The COPY block for lifecycle scripts/`allowlist.txt`/entrypoint sits *before* cert generation (133-140), binary-pin generation (149-159), and the image-wide setuid strip (162). *Failure:* every allowlist edit (regenerated by `sync.sh` in the normal loop) invalidates and re-runs cert-gen and a full-filesystem setuid scan on rebuild. *Fix:* move the COPY block after cert-gen and pin generation, keeping the setuid strip last.
- [x] **[P3]** `sandbox/.devcontainer/Dockerfile:27-32,44` — apt packages and `gh` installed unpinned. *Failure:* cache-miss rebuilds pull different versions non-reproducibly; toolchain drift re-pins silently. *Fix:* pin critical apt versions, or document the reproducibility trade-off.

#### Design

- [x] **[P3]** `sandbox/.devcontainer/bin/dev:216-219` vs `:231` — If `verify-pins` reports drift, `bin/dev` exits *before* `sandbox_install_autosync_trap` installs the stop-on-exit trap. *Failure:* a drift-abort leaves the just-started container running and pinning RAM, contrary to documented "cleans up after itself" behavior. *Fix:* stop the container in the abort path, or install the trap before the pin gate.
- [x] **[P3]** `sandbox/.devcontainer/bin/doctor:21` — Toolchain-presence loop checks `node npm claude git python3` but omits `gh`, even though `gh` is in the pin manifest and the pin-success line (`:30`) claims it matches fingerprints. *Failure:* a missing/broken `gh` is not reported by doctor. *Fix:* add `gh` to the loop.

#### Functionality

- [x] **[P2]** `sandbox/.devcontainer/README.md:3,17-20,63` — README documents the launcher as `dev-claude`, but the shipped script is `bin/dev` (invoked as `dev`); nothing installs a `dev-claude` command. *Failure:* copy-pasting `dev-claude ...` from the README gives "command not found". *Fix:* replace all `dev-claude` occurrences with `dev` (or ship the alias).
- [x] **[P2]** `sandbox/.devcontainer/bin/dev:189-191` — RO bind-mount sources `$HOME/.claude/hooks/guard.mjs`, `$HOME/.claude/hooks/managed-settings.json`, `$HOME/.gitconfig` have no existence pre-flight (unlike the guarded overlay paths). *Failure:* on a fresh host lacking any of these, `container run` fails at line 163 with an opaque runtime error. *Fix:* pre-check each source and error clearly or skip with a warning.
- [x] **[P3]** `sandbox/.devcontainer/bin/doctor:50` — Claude-token check reads `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_*`, but those are injected only into the final exec via `EXEC_ENV` (`bin/dev:248`), not `container run -e`. *Failure:* doctor run from a separate `container exec` reports "no Claude token" even though the launched session has it. *Fix:* document that doctor must run inside the launched session, or forward the token into the container env.
- [x] **[P3]** `sandbox/.devcontainer/` + `README.md:1` — A `container build`-based image under a `.devcontainer/` directory with no `devcontainer.json` is misleading. *Failure:* users expecting VS Code "Reopen in Container" find no support. *Fix:* state in the README that this is an apple/container image and `bin/dev` is the only entry point.

### Pass 3 — CI / DevOps

#### Bugs

- [x] **[P2]** `.github/workflows/ci.yml:108` — The `vendored` job only `cmp`s the three verbatim files and never regenerates/compares the generated `allowlist.txt` (unlike `sync.sh --check`, sync.sh:85-88). *Failure:* someone edits `base-allowlist.txt` without re-running `sync.sh`; `sandbox/.devcontainer/allowlist.txt` goes stale but CI stays green, so a container ships with a wrong egress allowlist. *Fix:* in the `vendored` job, regenerate the sandbox allowlist from `base-allowlist.txt` + `allowlist.extra.txt` and `cmp` against the committed copy (mirror `gen_allowlist`). **Related:** the pre-commit-hook gap below makes this worse.
- [x] **[P2]** `.github/workflows/ci.yml:24-27` — `pull_request` uses `paths-ignore: [docs/**, "*.md"]`; a PR changing only root-level `.md` files skips the whole workflow. *Failure:* with "CI Complete" as a required branch-protection check, a README-only PR never produces that check and sits permanently "Expected — Waiting", blocking merge. *Fix:* drop `paths-ignore` on `pull_request` (keep it on `push`), or add an always-passing fallback job named "CI Complete" for skipped paths.
- [x] **[P3]** `.github/workflows/ci.yml:203,223` — The quality-gate/ci-complete aggregation greps `needs.*.result` for `(failure|cancelled)` only, so a `skipped` required job passes the gate. *Failure:* a future `if:` condition on a gated job that evaluates false yields a green gate despite the check never running. *Fix:* treat `skipped` as a gate failure too.

#### DevOps gaps

- [x] **[P2]** `.githooks/pre-commit:2-5` — Nothing installs the hook path; `core.hooksPath=.githooks` lives only in this machine's `.git/config` and isn't carried by a clone. *Failure:* a fresh contributor commits with no hook active, so the leak-audit + drift check (the only place `sync.sh --check` runs) is silently skipped — compounding the `allowlist.txt` CI gap above. *Fix:* add a bootstrap step (documented `git config core.hooksPath .githooks`, `make setup`, or committed installer) and mark it required in the README.
- [x] **[P3]** `.github/workflows/codeql.yml:10` — No `concurrency:` group (ci.yml has one at 30-32). *Failure:* rapid PR pushes queue overlapping CodeQL runs, wasting minutes. *Fix:* add a `concurrency` block with `cancel-in-progress: true` keyed on `github.ref`. (Also a fleet-consistency item.)
- [x] **[P3]** `.github/workflows/ci.yml:18` — No `schedule:` trigger, so Trivy misconfig and gitleaks run only on push/PR while CodeQL runs weekly. *Failure:* a dormant repo is never re-scanned against updated policy/rule databases. *Fix:* add a weekly cron to ci.yml (or to the trivy/secrets jobs).
- [x] **[P3]** `.github/workflows/ci.yml:191-197` — "CI Complete" gate depends on a hand-maintained `needs:` list. *Failure:* a job added later but not appended to `needs` can fail while the gate reports green. *Fix:* document the invariant next to the list (or derive it dynamically).
- [x] **[P3]** `.github/dependabot.yml:6-22` — Covers the right ecosystems but sets no `open-pull-requests-limit`, labels, or commit-message prefix. *Failure:* none functional; PR hygiene only. *Fix:* add limits/labels per fleet convention.

#### Coverage gaps

- [x] **[P2]** `.github/workflows/ci.yml:118-133` — The Dockerfile is hadolint-linted and Trivy-scanned but never built in CI. *Failure:* a Dockerfile that lints clean but fails `docker build` (bad apt package, broken pin, unreachable base tag) merges and only breaks on the next developer rebuild. *Fix:* add a build job (`docker build` without push) for the devcontainer image.
- [x] **[P3]** `.github/workflows/ci.yml:38-64` — `squid.conf` gets no validation at all (it isn't shell, so the shell job ignores it), and the iptables logic has no functional test. *Failure:* a semantically-broken `squid.conf` passes CI and only fails at container start. *Fix:* add a `squid -k parse` step against `squid.conf`.
- [x] **[P3]** `.github/workflows/ci.yml:108` — Vendored file list duplicated from `sync.sh`'s `VENDORED` array (sync.sh:65). *Failure:* a fourth canonical file added to `sync.sh` is silently not drift-checked by CI. *Fix:* source the list from one shared location.

#### Consistency

- [x] **[P3]** `ci.yml:24` vs `codeql.yml:16-17` — CI's `pull_request` has no `branches` filter while CodeQL restricts to `main`. *Failure:* PRs targeting a non-main base run CI but not CodeQL — inconsistent security posture. *Fix:* align the trigger scopes.

#### Checked and found clean (do not re-investigate)

- `.trivyignore` — sole entry `AVD-DS-0002` is justified and current; nothing stale.
- `audit.sh` coverage in CI — the `secrets` job runs `./audit.sh` over all committed files; gitleaks adds the history backstop.
- Workflow permissions — top-level `permissions: {}` with per-job least privilege; correct.
- Action pinning — every action SHA-pinned with version comment in both workflows; matches fleet convention.
- Documented tradeoffs (hadolint threshold/DL3008 ignore, Trivy vuln-scanner off, CI not running full `sync.sh --check`) are deliberate and explained in-file.

### Pass 5 — Egress-enforcement chain deep dive (init-firewall.sh, squid.conf, entrypoint.sh, verify-pins)

Context: second-opinion security sweep of the enforcement chain. Headline result **overturns a
Pass 1 finding**: `squid.conf:46` is NOT dead config. The core egress lock itself (OUTPUT
default-DROP + proxy-UID-only ACCEPT) was independently re-verified as sound — no P1 bypass.

#### Bugs

- [x] **[P2]** `squid.conf:46` (with `31`, `43`) — `http_access allow allowed_dom` is reachable by any non-CONNECT, absolute-URI forward-proxy request, e.g. `curl -x http://127.0.0.1:3128 http://github.com:443/`. Line 42 (dst≠metadata) passes, line 43 passes (443 ∈ `Safe_ports`), lines 44-45 are skipped (not CONNECT), line 46 matches `allowed_dom` → ALLOWED. Squid then opens a **cleartext** L7 HTTP connection to the origin on :443; `ssl_bump`/peek/splice never run, so the SNI allowlist (the real enforcement) is entirely bypassed and only the weaker URL-authority `dstdomain` applies. *Failure:* violates the file's own "HTTPS-only egress, no cleartext HTTP" invariant (lines 27-30) — squid originates cleartext request lines/headers/POST bodies on the wire, with no SNI/TLS check. **Impact is bounded to allowlisted destinations only** (squid connects to the authority it just matched against `allowed_dom`), so this is a cleartext/no-SNI *downgrade*, not a full allowlist bypass; no current allowlist host speaks plaintext HTTP on 443, so realized harm today is low — but the config asserts a guarantee it does not provide. *Fix:* delete line 46 (serves no purpose in a CONNECT-only design), or add `http_access deny !CONNECT` ahead of it so only spliced CONNECT tunnels are ever permitted.

#### Design

- [x] **[P3]** `init-firewall.sh:47` vs `62-63` — IPv6/IPv4 loopback-DNS hardening parity gap. Lines 62-63 drop non-`proxy`-UID DNS (udp/tcp dport 53) on IPv4 loopback to close the "loopback resolver = exfil channel" concern, but line 47 (`ip6tables -A OUTPUT -o lo -j ACCEPT`) grants blanket IPv6 loopback egress to every UID with no DNS restriction. *Failure:* if the base image ever runs a resolver bound to `::1:53` (systemd-resolved/dnsmasq can), a non-proxy process regains exactly the loopback-DNS exfil channel the IPv4 rules close. **Reachability today is near-nil** — Docker's embedded resolver is IPv4 (127.0.0.11) and apple/container's is external, so no `::1:53` listener exists in either target runtime. *Fix:* mirror lines 62-63 for ip6tables (drop non-proxy `::1` dport 53 udp/tcp) before the `-o lo ACCEPT` on line 47.
- [x] **[P3]** `init-firewall.sh` (absence) / `squid.conf:40-42` — the link-local metadata IP is denied only inside squid (`http_access deny metadata`), never at the firewall, and the proxy-UID blanket ACCEPT permits `proxy` to reach `169.254.169.254` at L3. Pure defense-in-depth, not a live hole (the squid ACL currently blocks it and the IP isn't allowlisted). *Failure:* if the squid ACL were ever removed/reordered, nothing else stops SSRF-style metadata reads (e.g. via a poisoned/rebinding allowlisted name). *Fix:* add `iptables -A OUTPUT -d 169.254.169.254/32 -j DROP` (and `ip6tables` for `fd00:ec2::254/128`) ahead of the proxy-UID ACCEPT so the block survives a squid-config failure.

#### Checked and found clean (do not re-investigate)

- **Core egress lock holds — no P1.** OUTPUT default-DROP + proxy-UID-only ACCEPT is complete; a non-proxy process cannot establish any new outbound flow (SYN hits `EGRESS_DENY`), so the `OUTPUT ... ESTABLISHED` rule can only match reply packets of INPUT-initiated inbound-port flows, not arbitrary egress. IPv6 fully dropped except loopback.
- **CONNECT to IP literal / non-443 port** — `squid.conf:43-45` (`deny !Safe_ports` 443-only, then `allow CONNECT allowed_dom SSL_ports`, then `deny CONNECT all`) blocks CONNECT to any IP or non-allowlisted host and any port ≠443, before `ssl_bump` runs.
- **SNI/Host (CONNECT-authority) mismatch** — fail-closed: `http_access` gates on the CONNECT authority (`allowed_dom`); `ssl_bump splice` gates on the ClientHello SNI (`allowed_sni`); a mismatch yields `terminate all` or a CONNECT deny. ECH/absent-SNI → no splice → terminate.
- **Request smuggling / Host-vs-URL split** — squid parses the absolute-URI authority for both `dstdomain` and the upstream target, so there is no Host/URL divergence to exploit.
- **Cloud metadata via DNS rebinding** — `dst 169.254.169.254/32` is evaluated after resolution and denied first, so a hostname resolving to the metadata IP is still blocked at the squid layer.
- **verify-pins TOCTOU / unhashed JS payload** — low risk: pinned tools and the `@anthropic-ai/claude-code` JS live in a root-owned prefix the unprivileged `dev` user cannot write; dev's writable npm prefix is last on PATH; `type -P` plus path-equality catches earlier-PATH shadow binaries; any `sha256sum` failure is fail-closed.
- **entrypoint fail-open on firewall failure** — `entrypoint.sh:42` swallows a non-zero firewall exit, but `init-firewall.sh` only exits non-zero after policies are already DROP, so squid starting afterward cannot egress if the lock is incomplete; fail-closed.
- **DROP vs REJECT** — DROP everywhere is an intentional stealth choice; only cost is a proxy-bypassing in-container tool hangs to timeout rather than failing fast (UX, not security).

### Pass 6 — Launcher & container-runtime deep dive (launcher-common.sh, bin/dev, bin/doctor, post-*, Dockerfile)

Context: second-opinion robustness sweep. Two lifecycle P2s found; both cluster with the known
orphan-container item (#6 in Pass 2) — the fix for one closes the other. bash-3.2 compatibility is
a stated project target (launcher-common.sh header) and the local `/bin/bash` is 3.2.57, which
`#!/usr/bin/env bash` resolves to for any user without a newer bash earlier on PATH.

#### Bugs

- [x] **[P2 — effectively P1 on bash 3.2]** `sandbox/.devcontainer/bin/dev:170` (default is `PORT_FLAGS=()` at :105) — `"${PORT_FLAGS[@]}"` is expanded **unguarded**, unlike every other array in the file (`:89`, `:184`, `:248` all use the `${arr[@]+"${arr[@]}"}` form). Under `set -u` (`:17`) an empty array expanded as `"${arr[@]}"` is a fatal "unbound variable" on **bash 3.2** — the project's stated compat target and the actual system `/bin/bash` (3.2.57). *Failure:* verified — `set -euo pipefail; PORT_FLAGS=(); : "${PORT_FLAGS[@]}"` aborts with `PORT_FLAGS[@]: unbound variable` under `/bin/bash`. This is the **default** path: `DEV_SANDBOX_PORTS` unset ⇒ `PORT_FLAGS=()` ⇒ the `container run` block (163-194) dies at line 170, so **every fresh container create fails** on a 3.2 host. It only works for the author because their `env bash` is 5.x. *Fix:* `${PORT_FLAGS[@]+"${PORT_FLAGS[@]}"}` to match line 184.
- [x] **[P2]** `sandbox/.devcontainer/bin/dev:202-205` — the lifecycle-replay `container exec` (post-create once + post-start every start) runs with **no `|| true`** and inner `set -e`, while the stop-on-exit trap is not installed until line 231. Any nonzero exit here aborts the `set -e` launcher **after** the container was created/started (160/163) but **before** the trap → the ~6 GB VM is orphaned, running, pinning RAM, with no cleanup. This **generalizes** known #6 (which named only the verify-pins abort at :216): every hard abort in the window 160→231 orphans the VM, and line 202 is an *earlier and more-likely* trigger than 216 — `post-start.sh:27-33` deliberately `exit 1`s when `/run/egress-firewall-ok` is missing, exactly the failure most likely to fire in the field. (Line 199's proxy-wait has `|| true` and won't abort; line 202 does not.) *Failure:* firewall apply returns non-zero (entrypoint:42 is non-fatal, sentinel absent) → post-start exits 1 → launcher aborts at 202 → orphaned running container. *Fix:* install the autosync/stop trap immediately after `CID="$NAME"` (~line 196), before the proxy-wait and lifecycle exec — this closes **both** #6 and this instance; a narrow fix at 216 leaves 202 open.

#### Design

- [x] **[P3]** `sandbox/.devcontainer/post-start.sh:12,16` — inconsistent config-merge direction on every warm start: the `dot-claude` tree uses `rsync --update` (host-newer-wins), but `.claude.json` is merged `.[1] * .[0]` with `.[0]` = container (container-wins), so host `.claude.json` edits never propagate to a warm container despite the "auto-pull host config on every start" comment. Plausibly **intentional** (protects in-container session/project state from being clobbered by the static staged copy each restart) — flagged for awareness, confirm intent and either fix the direction or update the comment to say `.claude.json` is deliberately container-authoritative.

#### Checked and found clean (do not re-investigate)

- **EXIT trap vs. signals** (`bin/dev:231` / `launcher-common.sh:118`) — suspected `trap … EXIT` (no INT/TERM/HUP) would skip cleanup on terminal-close/kill. Empirically FALSE: on `/bin/bash` 3.2 the EXIT trap fires on SIGHUP/SIGTERM/SIGINT even with no explicit signal traps. Stop-on-exit cleanup is signal-robust.
- **CONTAINER_CWD prefix-strip** (`bin/dev:224-225`) — `${HOST_PWD#"$TARGET"}` is safe (no false `/a/bc` vs `/a/b` match): `TARGET` is the git top-level of `PWD` (or `PWD`), both resolved with `pwd -P`, so `HOST_PWD` is always a true descendant of `TARGET`.
- **Pin-gen before setuid-strip** (`Dockerfile:149-162`) — stripping setuid bits (162) after recording pins (149-159) does not invalidate them: `sha256sum` hashes content, `chmod -s` changes only mode bits.
- **PATH shadowing of pinned tools** (`Dockerfile:97,111`) — dev-writable `/home/dev/.npm-global/bin` is intentionally **last** on PATH; safe-chain's runtime `npm i -g` targets that dev prefix, so a compromised agent can't shadow claude/npm/node/git/gh/python3.
- **verify-pins vs. safe-chain shims** (`bin/dev:216`) — invoked with `-e BASH_ENV=` (clears the shim loader) and by absolute root-owned path, so shims can't wrap `type -P`/`sha256sum` to fake a pass.
- **HASH derivation** (`bin/dev:42`) — `pipefail` (:17) makes a missing `shasum` propagate through the pipeline so the `|| echo target` fallback triggers; HASH is never silently empty.
- **Overlay egress gate** (`bin/dev:60-94`) — fails closed: non-interactive launches ignore the workspace-supplied overlay (`USE_OVERLAY=0`); grep-empty pipelines are `|| true`-guarded correctly.

### Pass 4 — Architecture / allowlist / docs / functionality

Context: all 5 sibling repos' vendored copies were verified byte-identical *today*, so the
architecture findings are latent fragility, not live breakage.

#### Architecture

- [ ] **[P2 · DEFERRED]** `.github/workflows/ci.yml:95-116` / `paths.sh:27-34` — The only drift gate for the 5 *external* sibling repos is running `./sync.sh --check` on this one Mac (requires all targets checked out side-by-side); the receiving repos have no equivalent check (Napoleon-relay and git-agent have zero workflows; Vision/Watchman CI never references the canonical files). *Failure:* someone hand-edits `Vision/.devcontainer/squid.conf`; no automation anywhere catches it and the "single source of truth" silently forks — exactly the pre-repo "3 of 4 stale" bug the header warns about. *Fix:* add a drift check to each receiving repo's CI that fetches LockBox's canonical files and `cmp`s them, or generate vendored copies at container-build time instead of committing them.
- [x] **[P3]** `init-firewall.sh` / `squid.conf` / `launcher-common.sh` (vendored copies) — No version/hash/sync-timestamp stamp, so a baked image has no provenance tying it to a canonical revision. *Failure:* debugging a stale container, you cannot tell which generation of the egress lock it baked without manual `cmp`. *Fix:* have `sync.sh` prepend `# synced from LockBox @ <git-sha>` (like `gen_allowlist` already does for allowlist.txt).
- [x] **[P3]** `paths.sh:14-15` — `CODE_ROOT`/`BRAIN_DC` default to this user's exact machine layout; env overrides exist but are documented nowhere. *Failure:* a second contributor cannot run `sync.sh` without reverse-engineering paths.sh. *Fix:* document the overrides in a README setup section, or auto-discover siblings relative to `EGRESS_REPO`.

#### Allowlist

- [x] **[P2]** `base-allowlist.txt:26-27` — `raw.githubusercontent.com` and `objects.githubusercontent.com` are multi-tenant hosts (any public repo / release asset) on the shared floor for *all six* containers, including the deliberately tight ones. *Failure:* an in-container agent can pull attacker-controlled content (payload in any public repo) through the egress lock; these hosts aren't required by git push/fetch (`github.com` + `codeload.github.com` are), only ad-hoc `curl`. *Fix:* demote both to the extras of the projects that actually curl raw files. (Note: the `.githubusercontent.com` wildcard was already deliberately scoped off — this narrows two residual hosts.)
- [x] **[P3]** `base-allowlist.txt:24` — `api.github.com` on the shared floor is POST-capable, and git-agent forwards a push/API token. *Failure:* a compromised token-bearing agent can exfiltrate via GitHub API writes (gist/commit) to a firewall-permitted host. *Fix:* accept as inherent to `gh` and document the residual, or move it to extras of containers that run `gh`.
- [x] **[P3]** `sync.sh:43-62` — `gen_allowlist` concatenates base + extra with no dedup; a host in both files appears twice in the baked allowlist. *Failure:* harmless to squid, but misleads a human auditor and makes the raw-file and `sort -u` host counts disagree. *Fix:* dedup extras against base, or document it. (Comment/blank-line/exact-match semantics verified correct otherwise.)

#### Documentation

- [x] **[P3]** `paths.sh:22` — Comment claims `~/Code/sandbox` remains as a compat symlink, but it does not exist on disk. *Failure:* documented `cd ~/Code/sandbox` shortcut fails. *Fix:* recreate the symlink or delete the stale claim.
- [x] **[P3]** `README.md` (top-level) — No setup/contributing section and no `CLAUDE.md`; the edit→sync→rebuild loop is documented but not how a fresh clone bootstraps (undocumented `CODE_ROOT`/`BRAIN_DC` overrides, uninstalled `core.hooksPath` gate). *Failure:* a new machine/contributor cannot reproduce the working setup from docs alone. *Fix:* add a "Setup / first run" section covering path overrides + hook install. (Broader than, and related to, the pass-3 pre-commit item.)
- [x] **[P3]** `README.md:79-84` — "Per-project wiring" describes COPY paths / inbound-ports files that live in sibling repos, unverifiable from here; can silently rot. *Fix:* none required now; consider linking one reference Dockerfile.

#### Functionality

- [x] **[P2]** repo root — No test harness of any kind (no test files, no Makefile, no test job); a security-critical egress firewall + SNI proxy ships with zero automated proof that blocking works. *Failure:* a logic regression in `init-firewall.sh`/`squid.conf` that still lints/parses (e.g. mis-ordered `http_access`) reaches production containers un-caught. *Fix:* add one functional test that boots the image and asserts an off-allowlist host is refused while an on-allowlist host connects (needs privileged execution; complements the pass-3 `squid -k parse` item).
- [x] **[P3]** repo root — No versioning: no git tags, no CHANGELOG. *Failure:* combined with unstamped vendored copies, no way to reference or roll back "the egress lock as of version X" across the fleet. *Fix:* lightweight tags + CHANGELOG, or at minimum the sync-SHA stamp above.
- `.DS_Store` — checked and clean: present in the working tree but gitignored and untracked. No action.

### Pass 7 — Tooling & repo-hygiene deep dive (sync.sh, audit.sh, paths.sh, allowlists, pre-commit, CI)

Context: second-opinion sweep of the sync/vendoring, secret-scan gate, and CI logic. Headline is
a P2 in the leak gate: it scans the **working tree**, not the git index, so it is both bypassable
and over-scoped. CI shell-injection and `sync.sh --check` correctness were checked and cleared.

#### Bugs

- [x] **[P2]** `audit.sh:6,64-71` + `.githooks/pre-commit:14-15` — the leak gate scans the **working tree** (`find "$root" -type f`, :70), never the git index, despite line 6 claiming it "scans the committed … files." One root cause, two confirmed failure modes:
  - *Bypass (false-negative):* `git add secret.txt` → overwrite the worktree copy to remove the secret → `git commit`. The commit records the staged blob **with** the secret, but audit scans the clean worktree and passes. Demonstrated in a throwaway repo (staged `AKIA…EXAMPLE`, worktree cleaned → audit PASSES). A two-command sequence walks straight through the gate.
  - *Over-scope (false-positive):* `find` has no tracked-file filter, so it flags **untracked/gitignored** files (a local `.env`, dropped `id_rsa`, scratch `secrets.json`). Worse, `ROOTS` (`:19-24`) includes `"${EGRESS_DEVCONTAINERS[@]}"` — six worktrees in *different* git repos — so a stray key in Vision/Watchman/Brain blocks an unrelated **LockBox** commit with a baffling `secret in ../Vision/…` message.
  - *Fix:* scan staged content — `git diff --cached --name-only -z` piped to `git show :"$f"` per file — instead of `find` over the filesystem. Closes the bypass and scopes the FP surface to what's actually being committed in this repo.
- [x] **[P3]** `audit.sh:27-39` — regex set misses real leakable formats. Confirmed unmatched: legacy OpenAI keys (`sk-`+48 chars — only `sk-proj-`/`sk-ant-` covered), Stripe live keys (`sk_live_`/`rk_live_`), AWS STS temp keys (`ASIA…`), the 40-char AWS secret access key. *Failure:* a committed legacy OpenAI or Stripe key sails through. *Fix:* add `sk-[A-Za-z0-9]{40,}`, `sk_live_[A-Za-z0-9]{20,}`, `rk_live_…`, `ASIA[0-9A-Z]{16}`.
- [x] **[P3]** `audit.sh:58-61` — the private-key-file case matches `*.pem`, which is also the extension for **public** certs, chains, CSRs. *Failure:* committing a legitimate public `.pem` (e.g. a pinned CA cert) trips a false "private-key file committed" hit and blocks the commit. *Fix:* gate `.pem` on content (`grep -q 'PRIVATE KEY'`), keep `id_rsa`/`id_ed25519`/`.key` as-is.

#### Design

- [x] **[P3]** `audit.sh:45,53-54` — `report()` echoes `$hit`, the full `grep -nE` matched line (`N:…secret…`), to stdout. In CI the `secrets` job runs `./audit.sh`, so a hit prints the literal secret into the build log. *Failure:* a matched secret (including from an untracked local file, per the over-scope bug) is written to CI logs / terminal scrollback, widening exposure. *Fix:* report only `file:line`, never the matched text.
- [x] **[P3]** `audit.sh:19-24,74-77` — README.md and TODO.md live under `$HERE` and are scanned for secret patterns like any other text file. *Failure (latent — clean today):* a documented example token, or the literal phrase `-----BEGIN … PRIVATE KEY-----` in this repo's own docs, would block *all* commits with a false hit. *Fix:* scope the scan to the security-critical file set (shell/conf/install scripts), not arbitrary docs.
- [x] **[P3]** `sync.sh:40-41,103` — the `domains()` comment says "for the safety check," but there is no safety check: `domains()` only prints a host count. Nothing guards against an accidentally-emptied `base-allowlist.txt` silently generating a 0-host allowlist (fail-closed — breaks every container — but with zero warning). *Fix:* add a real guard (fail if generated host count drops to zero / below a floor), or correct the misleading comment.

#### Functionality

- [x] **[P3]** `sync.sh:43-62,102` — `gen_allowlist` writes `allowlist.txt` non-atomically (`{ … } > "$out"` truncates in place) and, unlike the vendored files (`:99` `cmp` verify), the generated output is never verified after writing. *Failure:* an interrupt / disk-full / TOCTOU-deleted extra mid-generation leaves a truncated `allowlist.txt` — egress-safe (fewer hosts = more restrictive) but silently breaks the container until re-sync, with no signal. *Fix:* write to a temp file in the same dir and `mv` into place; optionally re-`cmp`.
- [x] **[P3]** `sync.sh:96-101` — `chmod +x "$dst/init-firewall.sh"` (:101) runs unconditionally in sync mode, outside the per-file loop whose line-97 guard (`[[ -f "$HERE/$f" ]] || continue`) skips a missing canonical. *Failure:* if canonical `init-firewall.sh` is absent, the `cp` is skipped but `chmod` still targets a nonexistent file → `set -e` aborts with a confusing chmod error instead of a clear message. *Fix:* guard the chmod on the target existing / on the copy having run.
- [x] **[P3]** `audit.sh:47-50,68-70` — `find … -type f` doesn't follow symlinks and `scan_one` returns early on binary files (:50). *Failure:* a symlinked secret file, or a binary/DER-encoded `.key`, escapes the scan. Low likelihood here. *Fix:* if staying with a filesystem scan, add `-L`/handle `-type l`; treat the private-key-*filename* case before the binary early-return.

#### Checked and found clean (do not re-investigate)

- **`sync.sh --check`** (`:76-92`, `:107-114`) — byte-exact and sound. Regenerates a fresh allowlist from *current* base+extra into a `mktemp` and `cmp`s against the committed copy; `gen_allowlist` is deterministic (`cat`+`echo`), so it cannot pass on a stale file nor fail spuriously. Missing target → `skipped++` → exit 1. (Counter-mislabel is known Pass-4 item.)
- **CI shell-injection** — `ci.yml:202,222` interpolate `${{ toJson(needs.*.result) }}` into a `run:` block (the injection *shape*), but the values are GitHub-controlled status enums, not attacker-controllable. No `github.event.*`/`head_ref`/PR-title reaches any `run:` block in either workflow. No exploitable injection.
- **audit.sh self-scan** — the pattern strings (`:28-38`) do not self-match when audit.sh scans itself (each regex's literal text has a `[`/non-class char right after the prefix, breaking the match). No self-false-positive.
- **paths.sh sourcing** — no side effects beyond variable assignment; the `$(cd … && pwd -P)` at :23 runs in a subshell; `${BASH_SOURCE[0]}` resolves repo root correctly; all defaults use `${VAR:-…}`, safe under `set -u`.
- **Space-in-path handling** — `BRAIN_DC` contains a space; every consumer quotes the array expansion. Correct throughout.
- **Allowlist wildcard breadth** — squid consumes the file via `dstdomain`/`ssl::server_name` (`squid.conf:22-23`); entries have **no** leading dot, so each matches its exact host only (no subdomain wildcarding). Tighter than the base-comment's "leading dot" note implies. (raw/objects breadth is the known Pass-4 item.)
- **pre-commit exit propagation** — `set -euo pipefail` (`:11`) + direct invocation of `audit.sh` / `sync.sh --check` means a non-zero from either aborts the hook and blocks the commit. Correct.

### Pass 8 — Performance review (2026-07-10)

Dedicated performance sweep (wall-clock, subprocess count, CI minutes, image-build time,
container start latency). Deduped against earlier passes — the following performance items
were already found and are NOT repeated below: audit.sh grep-per-pattern (Pass 1),
devcontainer double-scan (Pass 1), Dockerfile COPY-before-cert-gen layer ordering (Pass 2),
unpinned apt (Pass 2), CodeQL missing concurrency group (Pass 3).

Progress tracker (for the next agent if interrupted):

- [x] Pass 8a: Core shell tooling perf — audit.sh, sync.sh, paths.sh, launcher-common.sh, init-firewall.sh, .githooks/pre-commit, Makefile, test/egress-smoke.sh — DONE 2026-07-10 (findings below)
- [x] Pass 8b: Sandbox runtime perf — Dockerfile, entrypoint.sh, post-create.sh, post-start.sh, bin/dev, bin/doctor, bin/verify-pins, perms-fix.sh, squid.conf (runtime tuning) — DONE 2026-07-10 (findings below)
- [x] Pass 8c: CI wall-clock/minutes — .github/workflows/ci.yml, codeql.yml (caching, checkout depth, job graph, redundant work) — DONE 2026-07-10 (findings below)

Findings are appended per sub-pass below as they complete.

#### Pass 8a — Core shell tooling (audit.sh, sync.sh, paths.sh, launcher-common.sh, init-firewall.sh, pre-commit, Makefile, egress-smoke)

- [x] **[P2]** — DONE 2026-07-12: reference copies are generated ONCE into a scratch `REF_DIR` before the target loop (one `gen_vendored`/`sha256_of` per canonical file); check mode `cmp`s against the reference, sync mode installs it via same-dir temp + `mv` (atomicity kept) and `cmp`s for the write-verify. `sync.sh:167-198` — The regenerated reference vendored copy is target-independent (it depends only on the canonical file's content, version, and hash), yet `gen_vendored` is re-run inside the per-target loop: in `--check` mode once per file per target (6 targets × 3 files = 18 runs, lines 170-177), and in sync mode **twice** per file per target (write at line 194 + verify at line 195 = 36 runs). Each run spawns `mktemp`, a `dirname` subshell, and `sha256_of` — and on the macOS host `sha256_of` falls back to `shasum`, a Perl script with ~40-60ms startup each. That is 18-36 shasum invocations where 3 suffice. *Impact:* roughly 1-2s of avoidable latency per run of `sync.sh` or `sync.sh --check` on macOS — and `--check` runs on **every commit** via `.githooks/pre-commit`, so this is per-commit latency. *Fix:* before the target loop, generate each canonical file's reference copy once into a scratch temp (one `gen_vendored` per file, 3 total); in check mode `cmp` that reference against each target's copy; in sync mode `cp` the reference into place (with the right mode) and `cmp` for the write-verify. Hash memoization alone (cache `sha256_of` per canonical path) would also capture most of the win with a smaller diff.
- [x] **[P3]** — DONE 2026-07-12: secret scan is now one `git grep --cached -I -nE` over the whole index (file:line via `cut`); filename checks loop over `git ls-files` only, blob extraction only for `*.pem`. Also fixed en passant: bare `id_rsa`/`id_ed25519` at the repo ROOT now match (the old `*/id_rsa` pattern required a leading dir). `audit.sh:58-87` — Distinct from the already-fixed per-pattern-grep issue: the scan still spawns ~5-6 processes **per tracked file** (`mktemp`, `git show`, `grep -Iq`, `grep -nE | cut`, `rm`) and writes every staged blob to a temp file that is then read up to 3 times (binary probe at line 74, secret scan at line 78, and the `.pem` grep at line 71). With 36 tracked files that is ~200 fork/execs plus 36 temp-file write/read cycles on every commit, scaling linearly with repo growth; on macOS (slow fork) this is a few hundred ms of the pre-commit gate. *Fix:* do the whole secret scan in one process with `git grep -I -nE "$SECRET_RE" --cached` (searches the index directly — same semantics as `git show :<f>` per file — skips binaries via `-I`, no temp files), post-processing its `file:line:` output for the file:line-only report; keep the filename checks (`id_rsa`/`.key`) as a loop over `git ls-files` output, extracting blobs only for `*.pem` files (typically zero).
- [x] **[P3]** — DONE 2026-07-12: `statusline`/`plugins` are staged via `tar --exclude .git -cf - | tar -xf -` (`.git` never copied); plain items keep `cp -a`. `launcher-common.sh:31-33` — `sandbox_stage_claude_config` copies `plugins/` and `statusline/` wholesale with `cp -a` (line 31) **including** their `.git` directories, then deletes those `.git` dirs with `find ... -exec rm -rf` (line 33). Plugin/marketplace checkouts are git clones whose `.git` can be many MB, so every launcher start pays full copy I/O for data that is immediately deleted, on the interactive sandbox-start path. *Fix:* exclude at copy time, e.g. for the `plugins`/`statusline` entries use `tar -C "$src" --exclude .git -cf - "$item" | tar -C "$dst/dot-claude" -xf -` (bsdtar on macOS and GNU tar both support `--exclude`; bash-3.2 safe), keeping `cp -a` for the plain-file items.

Checked and found clean (Pass 8a — do not re-investigate):

- `audit.sh` known perf items verified fixed in current code: single combined `SECRET_RE` alternation (lines 36-50), no double-scan of sandbox/.devcontainer (scans `git ls-files` once; paths.sh no longer sourced).
- `sync.sh:71-81` `_emit_extras_deduped` is O(extras × base) with a pipeline per extra line — extras are a handful of lines; single-digit ms, not worth churn.
- `sync.sh` repeated `domains()` calls and the per-target allowlist mktemp/mv — negligible / needed for atomicity.
- `paths.sh` — one subshell, nothing to optimize.
- `launcher-common.sh` other functions (`sandbox_forward_llm_creds`, `sandbox_git_ro_mounts`, `sandbox_warn_stale_allowlist`, printf|sed escapes) — fine; the `find` calls at :33/:46 are bounded to the staged dot-claude tree.
- `init-firewall.sh` — ~30 sequential `iptables` execs; `iptables-restore` would save well under 100ms of a boot dominated by container start + squid, at the cost of the readable rule-by-rule `-C` verification structure. No sleeps/polling.
- `test/egress-smoke.sh` — 1s-interval polls are appropriate; run time dominated by image build; assertions 3-5 inherently sequential.
- `.githooks/pre-commit` — sequential audit + `sync.sh --check` is the right call (parallelizing interleaves failure output for ~1s saved, less once the sync.sh P2 lands).
- `Makefile` — trivial delegation targets.

#### Pass 8b — Sandbox runtime (Dockerfile, entrypoint, post-*, bin/dev, doctor, verify-pins, perms-fix, squid.conf)

- [x] **[P3]** — DONE 2026-07-12: image-wide strip moved to just before the COPY block; a scoped strip over `/usr/local/{sbin,bin}`, `/usr/local/share/dev-sandbox`, `/etc/squid` is the new last layer. `sandbox/.devcontainer/Dockerfile:178-180` — the image-wide setuid/setgid strip (`find / -xdev -type f -perm /6000 -exec chmod -s {} +`) is the last RUN, placed *after* the frequently-edited COPY block (lines 164-176). The deliberate reordering that put cert-gen/pin-gen before the COPYs (known Pass-2 fix) stopped short of this layer: every routine `sync.sh` allowlist or lifecycle-script edit still re-runs a full-filesystem traversal of the ~1.5 GB image (bookworm-slim + build-essential + Node + CPython ≈ low hundreds of thousands of inodes) inside the apple/container builder VM. *Impact:* roughly 10-40s added to every routine rebuild — likely the single most expensive step left on that rebuild path. *Fix:* move the image-wide find to just before the COPY block (after the last package/tool install), and keep last-layer coverage of the COPY'd files with a cheap scoped pass, e.g. `find /usr/local/sbin /usr/local/bin /usr/local/share/dev-sandbox /etc/squid -xdev -type f -perm /6000 -exec chmod -s {} +` — same defense-in-depth guarantee (the COPY'd paths are also already explicitly `chmod 0755`, which clears suid), at near-zero cost.
- [x] **[P3]** — DONE 2026-07-12: proxy-wait folded into the top of the step-6 lifecycle exec (one `container exec`, barrier semantics kept); poll is `sleep 0.2` × 150 here and in `post-create.sh`; verify-pins exec left separate (clean-shell isolation), as recommended. `sandbox/.devcontainer/bin/dev:229-235` — the proxy-wait is a standalone `container exec ... bash -lc` that runs on EVERY launch, immediately followed by a second `container exec` for the lifecycle replay. On the common warm-reuse path the wait succeeds on its first `/dev/tcp` probe, so the entire exec (CLI process + XPC round-trip + login shell reading /etc/profile) is pure overhead, ~100-300ms per launch in the interactive path. On cold starts the `sleep 1` poll granularity (also in `post-create.sh:11-14`) overshoots squid readiness by ~0.5s on average. *Impact:* one redundant container-CLI round-trip on every single `dev` invocation, plus ~0.5-1s avg extra on cold starts. *Fix:* fold the wait loop into the top of the step-6 lifecycle exec (one `container exec` doing wait → post-create/post-start) and use `sleep 0.2` in the poll. Do NOT simply delete the wait — it is the barrier that prevents `post-start.sh`'s `/run/egress-firewall-ok` check (post-start.sh:37-43) from racing the still-running entrypoint on a `container start` of a stopped container; it just doesn't need to be its own exec. (Merging verify-pins too is possible but would sacrifice the deliberate clean-shell `-e BASH_ENV=` isolation at bin/dev:246 — not recommended.)

Checked and found clean (Pass 8b — do not re-investigate):

- `Dockerfile` layer ordering otherwise: the known Pass-2 reorder is verified in place — cert-gen (128-135), volume-dir pre-create (138-139), pin-gen (147-157) all precede the COPY block; a routine allowlist edit re-runs only cheap COPYs, two chmod layers, and (per finding above) the find. Node/Python are SHA256-verified pinned-URL downloads, docs excluded, no GitHub-API resolution; the two `apt-get update` layers are the standard keyring pattern and only hit on cache-miss rebuilds.
- `.dockerignore` — host-only files (bin/dev, launcher-common.sh, README.md, allowlist.extra.txt) excluded from build context, so editing the launcher does not bust the build cache.
- `entrypoint.sh` warm-start path — cert-gen and ssl_db init guarded by existence checks (:45, :54) so RSA keygen never re-runs; keep-alive loop uses a 30s `sleep 30 & wait` tick (trap-responsive) with only pgrep + stat + chmod per tick; access-log rotation capped at 50 MB; per-boot `chown -R` (:61) is metadata-only over a handful of files.
- `bin/dev` build skip verified: `container image inspect` gate at :134 means the builder VM spins up only on missing image or `DEV_SANDBOX_REBUILD=1`, and the builder is stopped afterward to release ~2 GB RAM. `squid -k reconfigure` (:154-157) fires only when the staged allowlist checksum actually changed. Warm-reuse path is otherwise minimal in `container` CLI calls.
- `bin/verify-pins` — hashes 6 binaries (~200-300 MB) once per launch, sub-second; appropriate cost for a fail-closed integrity gate.
- `perms-fix.sh` — `chown -R` runs only on top-level owner mismatch (first volume mount); no-op on warm starts.
- `post-create.sh` — safe-chain npm install is once-per-container and intentionally synchronous before the first agent session.
- `post-start.sh` per-start cost — `rsync -a --update` is metadata-bound on a converged tree; one jq merge; four rm -rf of usually-absent dirs. Negligible.
- `squid.conf` runtime tuning — `cache deny all` + splice-only CONNECT means no object cache, no bumping; sslcrtd helpers idle (a few MB, not worth tuning for one user); `shutdown_lifetime 1 second` already minimizes stop latency; built-in positive DNS cache adequate; removed `dns_v4_first` is safe latency-wise because squid 6.x uses Happy Eyeballs, so the ip6tables-DROPped IPv6 attempt costs ~250ms once per host, not a connect_timeout stall.
- `bin/doctor` — network probes bounded by `--max-time` 12/8; six `--version` invocations are diagnostic-path only.

#### Pass 8c — CI wall-clock / runner minutes (ci.yml, codeql.yml, codeql-config, dependabot)

- [x] **[P2]** — DONE 2026-07-12: `docker/setup-buildx-action` + `docker/build-push-action` (both SHA-pinned) build `dev-sandbox:ci` with `cache-from/to: type=gha,mode=max` and `load: true`; the smoke test runs with `SKIP_BUILD=1`. `.github/workflows/ci.yml:165-182` — The `egress-test` job rebuilds the full sandbox image from scratch on every run with zero Docker layer caching (`test/egress-smoke.sh:41` does a plain `docker build`). The Dockerfile installs `build-essential` + squid via apt, downloads Node 24 and python-build-standalone tarballs, and runs `npm install -g @anthropic-ai/claude-code` — its own comments (lines 124-163) are explicitly layer-ordered so that routine changes only touch the final cheap COPY layers, but CI throws that ordering away by never persisting cache. *Impact:* realistically 4-8 min of build time per CI run (every PR push, main push, and weekly schedule); this is the long pole of the pipeline and the bulk of its runner minutes. *Fix:* the script already supports `SKIP_BUILD=1` — add `docker/setup-buildx-action` plus a build step using the GHA cache backend (`docker buildx build --load --cache-from type=gha --cache-to type=gha,mode=max -t dev-sandbox:ci sandbox/.devcontainer`), then run `RUNTIME=docker IMAGE=dev-sandbox:ci SKIP_BUILD=1 ./test/egress-smoke.sh`. Cache-hit builds drop to seconds for the common case where only the COPY'd scripts/allowlist changed.
- [x] **[P3]** — DONE 2026-07-12: `paths-ignore` replaced with `paths: [".github/workflows/**", ".github/codeql/**"]` on both push and pull_request; weekly schedule unchanged. `.github/workflows/codeql.yml:10-20` — CodeQL analyzes only the `actions` language (the matrix at line 45 and the header comment both say the workflows are the *entire* analyzable surface), yet push/PR triggers fire on every non-docs change. A PR touching only shell scripts or the Dockerfile runs a full CodeQL job that cannot produce different results. *Impact:* ~1.5-3 wasted minutes per non-workflow PR run and per main push — likely the majority of runs. *Fix:* replace `paths-ignore` with `paths: [".github/workflows/**", ".github/codeql/**"]` on both `push` and `pull_request`; the weekly schedule (line 22) already covers query-pack updates. Safe because "CI Complete" (not CodeQL) is the required branch-protection check, so a skipped run can't deadlock merges.
- [x] **[P3]** — DONE 2026-07-12: the six deterministic jobs carry `if: github.event_name != 'schedule'`; the (now single) gate job branches its grep pattern on event name so `skipped` still fails PR/push runs but is tolerated on schedule. `.github/workflows/ci.yml:33-36` — The weekly schedule's stated purpose (comment, lines 34-35) is re-scanning against updated Trivy/gitleaks rule DBs, but `schedule` triggers all eight jobs, including the expensive `egress-test` boot and the deterministic `shell`/`secrets`/`vendored`/`dockerfile`/`squid` jobs whose output cannot change on a dormant repo. *Impact:* roughly 8-14 wasted runner-minutes per week (~35-60/month), dominated by egress-test; on a low-traffic repo the schedule may be most of total CI spend. *Fix:* add `if: github.event_name != 'schedule'` to the six deterministic jobs, and make `quality-gate` tolerate their `skipped` result when `github.event_name == 'schedule'` (e.g. branch the grep pattern on event name) so the gate's skipped-equals-failure invariant still holds for PR/push runs.
- [x] **[P3]** — DONE 2026-07-12: single scan with `format: sarif`, `output: trivy-results.sarif`, `exit-code: "1"`; the `if: always()` SARIF upload kept. `.github/workflows/ci.yml:203-223` — The Trivy misconfig scan runs twice back-to-back over identical inputs (same scan-type/scan-ref/scanners/severity), once for the gating table output and once to produce SARIF. *Impact:* ~20-40s duplicated per run (second action install + full re-scan; the checks bundle is at least cached under `~/.cache/trivy` between steps). *Fix:* single scan with `format: sarif`, `output: trivy-results.sarif`, `exit-code: "1"` — Trivy writes the SARIF before applying the exit code — and keep the `if: always()` upload step; gating and Security-tab upload are both preserved with one scan.
- [x] **[P3]** — DONE 2026-07-12: collapsed into one `ci-complete` job named "CI Complete" carrying the full needs list + skipped-counts-as-failure check (branch protection keeps the same check name). `.github/workflows/ci.yml:236-284` — `quality-gate` and `ci-complete` are two serially chained jobs doing the same aggregation; `ci-complete` needs only `[quality-gate]` and re-runs an identical grep over one result. Each is a full fresh-runner allocation. *Impact:* one extra runner boot serialized onto the critical path of every run (~20-40s added to time-to-green for the required check) plus ~1 extra billable job-minute per run if the repo is private. *Fix:* collapse them — rename `quality-gate` to "CI Complete" (keeping the full needs list and the skipped-counts-as-failure check) and delete the second job; branch protection continues to reference the same check name.
- [~] **[P3]** — ATTEMPTED, REVERTED 2026-07-13: `awalsh128/cache-apt-pkgs-action` (SHA-pinned v1.6.3) triggers a GitHub Actions **startup_failure** — the whole CI run fails to start (no jobs execute) and GitHub surfaces no parse error via API. Isolated by bisecting the workflow on throwaway branches: only this step fails, and it still fails with the `version` input quoted and with every nested action SHA verified to resolve — so the action is incompatible with this repo's Actions environment. The `squid` job keeps plain `apt-get update` + install. `.github/workflows/ci.yml:151-154` — The `squid` job runs `sudo apt-get update` + installs `squid-openssl openssl` from the network on every run with no apt package caching. *Impact:* ~25-50s per run (apt index refresh is the bulk; the packages themselves are small). *Fix:* use `awalsh128/cache-apt-pkgs-action` (pinned by SHA, per repo convention) with `packages: squid-openssl openssl`, or at minimum restrict the update (`apt-get update -o Dir::Etc::sourcelist=...`) — cache-hit runs skip the network entirely.

Checked and found clean (Pass 8c — do not re-investigate):

- `codeql.yml:27-29` — concurrency group with `cancel-in-progress: true` is present (known Pass-3 finding verified fixed; group key correctly isolates the scheduled main run from PR refs).
- `timeout-minutes` — every job in both workflows has one (10 lint, 30 egress-test/CodeQL, 15 Trivy, 5 gates); no 6-hour-hang exposure.
- Job parallelism in ci.yml — all eight work jobs are `needs:`-free and fully parallel; the only serialization is the gate chain (covered above as a finding).
- `secrets-scan` `fetch-depth: 0` (ci.yml:98-99) — full clone is required by gitleaks-action v2, and the repo pack is 136 KiB / 32 commits; negligible. All other jobs correctly use default shallow checkout.
- shellcheck/hadolint action tool downloads — a few seconds each; below materiality.
- `.github/dependabot.yml` — weekly cadence, grouped PRs, limit 5, docker ecosystem pointed at `/sandbox/.devcontainer`; no churn.
- `.github/codeql/codeql-config.yml` — `security-extended` on one tiny language surface is deliberate and cheap; single-entry matrix.
- `.githooks/pre-commit` vs CI duplication (audit.sh + `sync.sh --check` also run as CI `secrets`/`vendored` jobs) — intentional defense-in-depth; the hook is opt-in per clone and covers fleet drift CI cannot. CI correctly runs check in self-only mode (`EGRESS_SELF_ONLY=1`, ci.yml:119).
- Artifact/upload surface — only the Trivy SARIF upload; no stray retention or oversized uploads.
- ci.yml `concurrency` (lines 39-41) with cancel-in-progress correctly prevents overlapping-run waste on rapid pushes.

#### Pass 8 — suggested working order

**10 OF 11 FINDINGS IMPLEMENTED** (2026-07-12; see the DONE notes on each item above). The 11th — apt caching in the `squid` job via `awalsh128/cache-apt-pkgs-action` — was reverted 2026-07-13 because that action causes a GitHub Actions `startup_failure` in this repo (see that item above); the `squid` job keeps plain `apt-get`. Verified: `audit.sh` (clean + a planted-secret/index-bypass fixture), `EGRESS_SELF_ONLY=1 sync.sh` + `--check` (byte-identical regeneration), `bash -n` + shellcheck on every touched script, a staged-tree fixture for the launcher tar copy, YAML parse of both workflows. The Dockerfile/CI egress build could not be run in the implementation sandbox (registry egress blocked); CI's `egress-test` job covers it.

1. **CI image-build caching `[8c P2]`** — biggest absolute win (4-8 min per CI run): buildx + GHA cache backend, then `SKIP_BUILD=1` into the smoke test.
2. **`sync.sh` per-target regeneration `[8a P2]`** — per-commit latency via the pre-commit hook (1-2s on macOS); generate each reference copy once before the target loop.
3. Then the P3 one-liners in rough value order: Dockerfile setuid-strip placement (10-40s per rebuild), CodeQL path scoping, schedule-trigger job gating, Trivy single-scan, gate-job collapse, apt caching in the squid job, `audit.sh` single-process `git grep --cached`, bin/dev proxy-wait fold-in, launcher plugin-copy `--exclude .git`.

---

## Suggested starting order for the next agent

Work P2s first; several cluster naturally. **Passes 5–7 (2026-07-05) added five new P2s — folded in below and flagged `[P5]`/`[P6]`/`[P7]`.**

0. **Launcher won't run on bash 3.2 `[P6]`** — do this FIRST; it likely blocks *every* fresh container create on a stock-macOS host. `bin/dev:170` unguarded `"${PORT_FLAGS[@]}"` → one-char fix to the `${arr[@]+"${arr[@]}"}` form. Trivial, high-impact, and independently testable.
1. **Orphaned-container cluster `[P6]`** — move `sandbox_install_autosync_trap` to right after `CID="$NAME"` (~`bin/dev:196`), before the proxy-wait/lifecycle exec. Closes BOTH the known verify-pins abort (Pass-2 #6, `:216`) and the new lifecycle-exec abort (`:202`) in one edit.
2. **Leak-gate scans worktree not index `[P7]`** — `audit.sh` `find`→ `git diff --cached`/`git show :file`. Fixes a real bypass (stage-then-clean) AND the cross-repo false-positive noise. Security-gate correctness; pairs with the pre-commit-bootstrap item.
3. **squid cleartext/no-SNI downgrade `[P5]`** — delete `squid.conf:46` `http_access allow allowed_dom` (or add `http_access deny !CONNECT` before it). Corrects the mischaracterized Pass-1 "dead config" item; enforces the file's own CONNECT-only, no-cleartext invariant.
4. **Allowlist-drift cluster** — CI regenerate+cmp of `allowlist.txt` (`ci.yml:108`) + pre-commit hook bootstrap (`.githooks/pre-commit`) + fleet drift checks in sibling repos (`ci.yml:95-116`). Vendored/generated artifacts must be provably in sync.
5. **Fail-closed verification** — extend `init-firewall.sh` post-apply checks to cover the loopback-DNS drops (`init-firewall.sh:62-63`); consider the IPv6 loopback-DNS parity + firewall-level metadata-IP drop from Pass 5.
6. **Doctor curl bug** — make the Anthropic reachability check actually able to fail (`bin/doctor:42`).
7. **Docs correctness** — `dev-claude` → `dev` in the sandbox README; missing-mount pre-flight in `bin/dev:189-191`.
8. **paths-ignore trap** — required-check deadlock on docs-only PRs (`ci.yml:24-27`).
9. **Allowlist narrowing** — demote `raw.githubusercontent.com`/`objects.githubusercontent.com` (`base-allowlist.txt:26-27`).
10. **Functional egress test** — the biggest lift; boots the image and proves blocking works.

Then sweep the P3s file-by-file (many are one-liners: sentinel error path, port-range guard, `gh` in doctor loop, sed escaping, counter label, concurrency group for CodeQL, weekly cron, dependabot hygiene, sync-SHA stamps, README setup section, stale symlink comment, audit regex additions, `.pem`-by-content check, atomic allowlist write).

# Sandbox fleet re-audit — 2026-06-10 (in progress)

Scope: devcontainer-egress (shared infra) + per-project .devcontainer dirs
(sandbox, Vision, Watchman, Napoleon-relay, git-agent, Brain via iCloud path)
+ fish launchers in dotfiles. Fleet now runs on Apple `container`, not Docker.

Status legend: [ ] not reviewed · [~] in progress · [x] reviewed

## Review progress

- [x] devcontainer-egress shared files (README, launcher-common.sh,
      init-firewall.sh, squid.conf, sync.sh, paths.sh, base-allowlist.txt, audit.sh)
      — sync.sh --check passes clean for all 6 targets (2026-06-10)
- [x] sandbox/.devcontainer (generic dev sandbox: Dockerfile, entrypoint,
      post-create/post-start, bin/dev, bin/doctor, bin/verify-pins, allowlists)
- [x] Vision/.devcontainer + vision bin/claude launcher
- [x] Watchman/.devcontainer + watchman bin/claude launcher
- [x] Napoleon-relay/.devcontainer + napoleon-claude launcher
- [x] git-agent/.devcontainer + git-agent launcher
- [x] Brain/.devcontainer (iCloud path) + brain bin/agent launcher
- [x] dotfiles fish functions (dev-claude, *-claude-sync, __claude_sandbox_sync,
      __sandbox_project_root)
- [x] Cross-cutting: pin verification, credential flow, autosync, skills/tools
      availability inside containers
- [x] Merge analysis: devcontainer-egress + sandbox as one repo (see bottom)

Not deep-read (skimmed only): per-project README.md files (known to carry stale
Docker-era text — F3/F16), Brain bin/claude + bin/doctor, Napoleon/git-agent
bin/doctor beyond structure, dotfiles Other/.hermes/sandboxes.

## Findings (running list)

### Shared infra + generic sandbox (reviewed)

F1. [security/functionality, MEDIUM] Reused containers keep a STALE squid
    allowlist. bin/dev regenerates the staged allowlist on every launch and the
    RO bind updates the file in-container, but on the reuse-running path squid
    never re-reads it (ACLs load at config parse). Widening requires restart
    (annoying); NARROWING silently doesn't apply (security). Fix: after the
    reuse path, `container exec <name> squid -k reconfigure` (root) whenever the
    staged allowlist content changed. Same pattern likely in other launchers.

F2. [security, MEDIUM — design decision to confirm] Per-target overlay
    (.sandbox-allowlist.txt / .devcontainer/allowlist.extra.txt) is read from
    the TARGET repo, which is exactly the untrusted content the sandbox
    contains. An in-container agent can write that file into the RW workspace;
    next launch silently widens egress. Mitigation options: print the overlay
    hosts at launch + require interactive ack, or keep overlays outside the
    workspace (e.g. ~/.claude-sandbox/overlays/<hash>.txt).

F3. [docs, LOW] Stale Docker-era text after the apple/container rewrite:
    - sandbox Dockerfile line 1: "built by compose"
    - sandbox README "Security model": claims `no-new-privileges` (container
      1.0.0 has NO --security-opt equivalent; actual control = setuid strip +
      no sudo) and "docker compose ... build --no-cache app" rebuild advice
    - post-start.sh: "Check `docker logs`" → `container logs`
    - entrypoint.sh: "Docker Desktop detached us from the bridge" comment
    - launcher-common.sh: "docker compose exec -e KEY" comment (apple
      container exec -e KEY name-only inheritance confirmed to work, 1.0.0)
    - bin/dev header: "see compose.yaml.docker-backup" (file gone?)

F4. [design, LOW] Dockerfile HEALTHCHECK is dead weight under apple/container
    (no healthcheck runner); real supervision is the entrypoint keep-alive
    loop. Either drop it or note it's docker-only.

F5. [functionality, LOW] On the reuse path, changed DEV_SANDBOX_PORTS are
    silently ignored (-p only applies at create; firewall reads inbound-ports
    at entrypoint). Launcher should warn + suggest REBUILD=1 when the staged
    inbound-ports differs from the running container's.

F6. [design, LOW] bin/dev step 9/11 uses DEV_CWD + bash -c cd; `container
    exec -w <dir>` exists in 1.0.0 and is simpler. Cosmetic.

F7. [performance, LOW] `container build` runs on EVERY launch (step 4).
    Cached, but still spins the builder; adds startup latency. Option: skip
    when image exists unless DEV_SANDBOX_REBUILD=1 — trade-off: vendored-file
    freshness then depends on rebuilds.

F8. [security, INFO] verify-pins gate is solid (root-owned prefix, BASH_ENV
    cleared, fail-closed). Entry firewall fail-closed verified (3 invariants).
    Squid HTTPS-only + SNI splice + metadata deny: good. audit.sh + sync.sh
    --check pass clean today.

### Vision + Watchman (reviewed 2026-06-10)

F9. [docs/security-claim, MEDIUM-LOW] Vision & Watchman post-create.sh comment
    claims the host wrapper strips ".credentials.json ... and active code-exec
    config (hooks/mcpServers/enabledPlugins)". Reality (launcher-common.sh):
    only `.hooks` is stripped from settings.json and oauthAccount/projects/
    installMethod from .claude.json — mcpServers and enabledPlugins DO
    propagate. post-start.sh's comment ("plugins/, hooks, mcpServers ...
    intentionally KEPT") contradicts it. Fix the post-create comment so the
    documented security posture matches the code (or actually strip them, if
    that was the intent).

F10. [security, MEDIUM — design decision to confirm] Vision/Watchman mount the
    HOST project memory RW into the container
    (~/.claude/projects/-Users-computer-Code-<P>/memory →
    .../-workspaces-<P>/memory). An in-container agent (processing untrusted
    workspace content) can write memory files the HOST Claude auto-loads next
    session — a container→host prompt-injection/persistence channel that
    partially bypasses the rationale for stripping `.projects`. Options: mount
    RO + explicit pull-back via *-claude-sync; or accept + note that memory
    diffs should be reviewed (memory is not git-tracked, so there's no diff
    trail today).

F11. [security/consistency, LOW] Pin-gate fail-open inconsistency: generic
    bin/dev hard-aborts when verify-pins is missing; Vision/Watchman launchers
    only WARN ("not baked yet — rebuild to enable") and continue, so a stale
    pre-pin image launches unverified. verify-pins is always baked by the
    current Dockerfiles, so absence = stale image; make it a hard abort
    everywhere.

F12. [functionality/docs, LOW] safe-chain is absent from the generic sandbox:
    Vision/Watchman install it in post-create and wire BASH_ENV to its shim;
    generic sandbox's Dockerfile comments mention "safe-chain in post-create"
    and pre-create /home/dev/.npm-global for it, but post-create.sh never
    installs it and bin/dev sets no BASH_ENV — npm/pip installs in the generic
    box are NOT supply-chain screened, and the comments overpromise. Either
    install it (matching the other boxes) or fix the comments + doctor note.

F13. [forward-compat, LOW] launcher-common's stage list copies settings.json,
    keybindings.json, CLAUDE.md, agents, rules, commands, statusline,
    status-line.sh, plugins — but not `skills`. Host has no ~/.claude/skills
    today, so nothing is lost *yet*; add `skills` to the list (the loop already
    tolerates absence) so user-level skills propagate when created.

F14. [design, LOW] F1 generalizes to the per-project launchers in image form:
    their allowlist is BAKED (COPY allowlist.txt), the launcher rebuilds the
    image every run, but the reuse-running/start-stopped paths keep the OLD
    container+image — an allowlist change (sync.sh) silently doesn't apply
    until <P>_REBUILD=1. Launcher should compare the running container's image
    digest against the freshly built one and warn (or auto-recreate on
    narrowing).

F15. [design, LOW] All entrypoints' PID-1 keep-alive loop uses a bare
    `sleep 30`; bash defers the TERM trap until the foreground sleep returns,
    so `container stop` waits up to 30s and may hit the kill timeout —
    skipping the graceful squid (and Vision Postgres) shutdown. Use
    `sleep 30 & wait $!` so the trap fires immediately.

F16. [docs, LOW] More stale Docker/devcontainer-era text (extends F3):
    Vision+Watchman Dockerfile line 1 "built by docker compose";
    "--security-opt=no-new-privileges" claims in headers; entrypoint comments
    "containerUser=root in devcontainer.json" (no devcontainer.json exists
    anymore), "Docker Desktop detached us from the bridge"; post-start +
    doctor "check `docker logs`"; Vision doctor suggests
    "devcontainer up --remove-existing-container" / "devcontainer exec" as
    remediation. All should say `container ...`.

F17. [security, INFO] Vision hardcoded local-dev Postgres creds
    (ftm_user/localdev) in launcher env + generated .env: acceptable — DB is
    loopback-only inside the VM, 5432 is not in inbound-ports, not a real
    secret. No action.

### Napoleon-relay, git-agent, Brain, fish launchers (reviewed 2026-06-10)

F18. [security/functionality, MEDIUM] git-agent's hooksPath/fsmonitor
    neutralization WRITES INTO THE HOST repo's .git/config (the .git mount is
    the host's, RW): `core.hooksPath=/workspaces/repo/.git/hooks` persists
    after the session. On the host that path doesn't exist → git silently runs
    NO hooks in that repo — e.g. it disables devcontainer-egress's own
    .githooks pre-commit (audit + drift gate) after one git-agent session on
    this repo. It also only runs at post-START, so an in-session config write
    re-enables exec until the next start. Fix: inject the overrides as
    command-scope env at `container run` instead
    (GIT_CONFIG_COUNT=2, GIT_CONFIG_KEY_0=core.hooksPath VALUE_0=<empty-hooks>,
    GIT_CONFIG_KEY_1=core.fsmonitor VALUE_1=false) — highest precedence, never
    persisted to the host file; optionally also diff .git/config at session
    exit and warn about changes that will be visible to the host.

F19. [bug, MEDIUM-LOW] git-agent post-start signing sanity check queries the
    OLD Docker socket path: `SSH_AUTH_SOCK=/ssh-agent ssh-add -l`. The
    apple/container launcher forwards the agent at
    /var/host-services/ssh-auth.sock, so the check never sees the real agent
    and prints the "signing key not loaded" warning spuriously on every start
    (or, worse, trains the user to ignore it). Use the inherited
    $SSH_AUTH_SOCK. (post-create's "(SSH_AUTH_SOCK=/ssh-agent)" comment is the
    same staleness.)

F20. [security, MEDIUM-HIGH] `*-claude-sync pull` bypasses the launcher's
    curated staging: it refreshes the stage with `rsync -a --delete` of the
    ENTIRE ~/.claude minus a short exclude list. NOT excluded: projects/
    (all host transcripts + memory for every project), history.jsonl, plans/,
    downloads/, security/. The stage dir is the SAME dir that is live
    RO-bind-mounted at /home/dev/.claude-stage, so one manual `pull` makes all
    host conversation history readable in-container until the next launcher
    run re-stages the curated set (launcher staging copies only an item
    allowlist). Fix: make pull reuse sandbox_stage_claude_config (+ the
    profile's jq strips) instead of rsync-minus-excludes.

F21. [security, HIGH — design decision to confirm] Autosync `push` (runs
    AUTOMATICALLY on every interactive session exit for Vision/Watchman/Brain,
    AUTOSYNC default 1) merges container-controlled state back into the host
    with container-wins semantics and NO code-exec strips:
      - settings.json: a hooks/enabledPlugins key ADDED in-container lands in
        host settings.json (pull strips these; push does not);
      - .claude.json: container-added mcpServers merge into the host file;
      - file rsync: plugins/ (real executable code), statusline/,
        status-line.sh, agents/, commands/ — newer-wins onto host ~/.claude;
      - projects/ is not excluded: container transcripts pollute host
        ~/.claude/projects (OBSERVED: host has -workspaces-Vision and
        -workspaces-Watchman dirs).
    Net: a prompt-injected in-container agent can persist code-exec config
    that the HOST Claude executes next session — this is the widest
    container→host channel in the design and undermines the careful staging
    sanitization on the way in. Mitigations (any/all): strip
    .hooks/.mcpServers/.enabledPlugins + exclude plugins//statusline//
    status-line.sh/projects/ on push; make autosync opt-in everywhere (generic
    sandbox already defaults 0); or show a diff of code-exec-capable paths and
    require explicit confirmation before applying. Backups (last 5) exist but
    are silent.

F22. [bug, MEDIUM-LOW] Manual sync container resolution is broken for two
    profiles (autosync is unaffected — the exit trap passes the cid
    explicitly):
      - dev-claude-sync: --container-filter still uses the Docker Compose
        label ("label=com.docker.compose.project=...") which the engine
        ignores (it only parses name=) → "container is not running" always.
        Should be: name=dev-sandbox-$hash.
      - brain-claude-sync: passes name=brain- but the engine matches with
        `grep -Fx` (exact whole-line) against names like
        brain-<roothash>-<profile> → never matches. Needs prefix matching
        (grep "^brain-") or the resolved exact name.

F23. [security, MEDIUM] brain-claude-sync pull stages ~/.claude.json VERBATIM
    (no --claudejson-strip, by design to keep mcpServers) — but that also
    keeps .oauthAccount (host identity) and .projects (full host project map +
    prompt history), which the LAUNCHER's staging always strips, even for
    Brain. Keep mcpServers if intended, but still
    del(.oauthAccount, .projects, .installMethod) on pull.

F24. [dead code, LOW] __sandbox_project_root walks up looking for
    .devcontainer/compose.yaml with "target: /workspaces/<P>" — compose files
    were archived in the apple/container rewrite, so the walk-up can never
    match and always returns the fallback. Harmless today (fallbacks are
    correct) but the "run from a second checkout" feature is silently dead.
    Re-key the marker (e.g. .devcontainer/bin/claude + WORKDIR match in the
    Dockerfile) or simplify to just $*_HOME/fallback.

F25. [docs, LOW] More stale references (extends F3/F16): Brain post-create
    points at ".devcontainer/features/agent-clis" (feature dir is gone; the
    CLIs are baked by the Dockerfile npm install), "BASH_ENV
    (devcontainer.json)" / "(compose.yaml)" comments in several post-creates,
    Brain post-start "see compose.yaml", vision/watchman/napoleon fish
    wrappers say "Compose launcher", and every project's post-start firewall
    error says "check `docker logs`". The fleet-wide grep for
    docker|compose|devcontainer.json over the .devcontainer dirs returns ~60
    hits excluding READMEs.

F26. [INFO, positive] Cross-image consistency is excellent: all 6 Dockerfiles
    pin the same debian:bookworm-slim digest, node 24.15.0,
    @anthropic-ai/claude-code@2.1.170, python-build-standalone 3.12.13
    (git-agent included — its pin list's python3 is satisfied), SHA-verified
    downloads everywhere. git-agent post-start DOES already neutralize
    core.hooksPath/core.fsmonitor (see F18 for the leak it causes), the
    empty-hooks RO overlay is in place, and Brain's qmd Option-B
    (host-embeds, container snapshot-copies index, symlinked models) is a
    clean design.

F27. [design, MEDIUM] Project-memory handling is inconsistent across the
    fleet, and Brain's is the safest:
      - Brain: host memory RO seed mount + rsync --delete on every start +
        tar push-back ONLY after interactive sessions. Headless writes are
        wiped. Good.
      - Vision/Watchman/Napoleon: host memory bind-mounted RW directly into
        the container's project slug — any in-container write (including from
        a prompt-injected headless run) lands in host memory that host Claude
        auto-loads next session (F10).
      - generic sandbox/git-agent: no memory propagation at all.
    Converge on Brain's seed+push-back pattern (or at least make the
    Vision/Watchman mounts RO with explicit pull-back).

F28. [design/maintenance, MEDIUM] The vendoring boundary is too narrow:
    sync.sh vendors only init-firewall.sh, squid.conf, launcher-common.sh —
    but entrypoint.sh, perms-fix.sh, post-start.sh (config-refresh half),
    bin/doctor (egress section), and ~80% of each Dockerfile are 6-way
    copy-paste near-twins that are ALREADY drifting (only Napoleon's
    entrypoint has the updated apple/container comments; sandbox/git-agent/
    Vision/Watchman still carry Docker-era ones). This is exactly the drift
    class paths.sh/sync.sh were created to kill. Options: parameterize
    entrypoint/perms-fix by env (PROJECT_NAME, optional postgres hook) and
    vendor them like init-firewall.sh; split post-start into a vendored
    common part + a project part; longer-term, generate the Dockerfile common
    prefix or build a shared base image (see merge section).

## Cross-cutting: agent tools/skills availability inside the boxes

- Staged in: settings.json (hooks stripped), keybindings, CLAUDE.md, agents/,
  rules/, commands/, statusline + status-line.sh, plugins/ (with host→container
  path rewrite in the plugin JSONs). mcpServers in .claude.json are KEPT by the
  launcher staging. Missing from the list: `skills/` (F13) — host has none
  today, so no loss yet. settings.local.json also not staged (likely fine).
- WebSearch works in-container (server-side via api.anthropic.com); WebFetch of
  arbitrary hosts is blocked by the allowlist (by design). context7 MCP +
  malware-list.aikido.dev are allowlisted where used.
- security-guidance plugin's python3 dependency is satisfied in all 6 images.
- Claude pinned at 2.1.170 with autoUpdates=false: containers will drift behind
  the host CLI version until images are rebuilt — accepted trade-off of the
  pin-verify design; consider a periodic "bump + rebuild fleet" routine.
- jq merge `.[1] * .[0]` (container-wins) in every post-start means host-side
  CHANGES to existing .claude.json keys never propagate to an existing
  container (only new keys do). Intentional for container-local state; worth a
  one-line comment in launcher-common docs so it doesn't surprise.

## Notes on shared infra read so far

- init-firewall.sh: default-deny first, proxy-UID-only egress, EGRESS_DENY
  logged drop, 3-invariant verification before sentinel. Reads
  /etc/egress/inbound-ports and optional /etc/egress/extra-rules.sh.
- squid.conf: peek+splice SNI allowlist, HTTPS-only (Safe_ports 443 only),
  metadata IP denied, cache off.
- sync.sh: vendored copy + cmp verify, --check drift mode, missing target is
  hard failure. paths.sh single source of target list.
- audit.sh: secret-pattern scan over repo + all managed devcontainers.
- launcher-common.sh: stage sanitized ~/.claude + .claude.json (strips hooks,
  oauthAccount, projects, installMethod), Keychain token forwarding via
  name-only -e flags, autosync-on-exit trap via fish.

## Merge analysis: devcontainer-egress + sandbox → one repo

Recommendation: YES, merge — but merge `sandbox` INTO `devcontainer-egress`
(not the other way), and do NOT pull the per-project .devcontainers in.

Why it makes sense:
1. `sandbox` is not a project. It contains ONLY .devcontainer/ — no app code,
   no README at top level, and critically NO GIT REPO (verified: no
   /Users/computer/Code/sandbox/.git). The generic sandbox's launcher,
   Dockerfile and entrypoint — security-critical code — currently have no
   version control and no pre-commit gate. Merging puts them under
   devcontainer-egress's git + .githooks (audit.sh + sync.sh --check).
2. Conceptually devcontainer-egress is already "the sandbox platform"; the
   generic sandbox is its reference implementation. They change together
   (every F1/F12/F15-class fix touches both repos today).
3. It removes one vendoring hop: as a subdirectory, the generic sandbox can
   consume init-firewall.sh/squid.conf/launcher-common.sh from ../ directly
   (build context permitting) or stay a sync target with zero path risk —
   either way paths.sh loses its most fragile entry.
4. It opens the door to F28's fix: one repo holding canonical entrypoint/
   perms-fix/post-start templates + the generic image, with per-project
   .devcontainers reduced to thin deltas (allowlist.extra.txt, inbound-ports,
   project toolchain layers).

Suggested shape:
    devcontainer-egress/            (consider renaming: agent-sandboxes?)
      README.md, sync.sh, paths.sh, audit.sh, .githooks/
      init-firewall.sh, squid.conf, launcher-common.sh, base-allowlist.txt
      sandbox/                      <- moved from ~/Code/sandbox/.devcontainer
        Dockerfile, entrypoint.sh, bin/dev, ...
Migration checklist (small, mechanical):
  - git mv the files in; update paths.sh (drop the old sandbox target or
    repoint it to the in-repo dir so allowlist generation still runs).
  - dotfiles dev-claude.fish: default `home` path → the new location
    (it already honors $SANDBOX_HOME for transition).
  - dev-claude-sync.fish: stage profile name unchanged (keyed by target hash).
  - Leave a symlink ~/Code/sandbox → devcontainer-egress/sandbox for a grace
    period if muscle memory wants it.
  - Keep Dockerfile build context self-contained: sync.sh continues to vendor
    the canonical files into sandbox/ (as it does for every other target), so
    `container build` needs no cross-dir context. Effectively the merge costs
    one `git mv` + two fish-function path edits.

Why NOT merge the per-project .devcontainers (Vision/Watchman/etc.): they
belong to their repos — the .devcontainer travels with the code it sandboxes,
and sync.sh already gives them single-source updates. Brain's lives in the
iCloud vault for the same reason.

## Suggested fix order (priority)

1. F21 push sanitization / autosync default      (container→host code-exec)
2. F20 pull staging curation                     (host→container data leak)
3. F18 git-agent host .git/config persistence    (breaks host hook gates)
4. F2  overlay allowlist trust, F10/F27 memory   (decide + converge)
5. F1/F14 stale-allowlist-on-reuse handling      (squid -k reconfigure / image-id check)
6. F11 pin-gate hard-fail, F19 ssh-agent path, F22 sync filters, F23 brain pull strip
7. F12 safe-chain in generic sandbox, F13 skills staging, F15 trap latency
8. Merge sandbox→devcontainer-egress, then F28 vendoring widening
9. Docs sweep: F3/F16/F25 stale Docker text (one pass over the fleet)

# Generic universal full-dev sandbox image — devcontainer-FREE (built by `container build`).
#
# One image, ANY project: bin/dev mounts the current directory (or git repo root)
# RW at /workspaces/project and runs claude inside, with the SAME egress lock,
# PreToolUse guard, and launch-integrity pins as the per-project sandboxes — but
# with no project-specific toolchain baked in. Ships Node + Python so JS/TS and
# Python projects (e.g. scrim) work out of the box; richer stacks should keep
# their own .devcontainer.
#
# Hardened like the others:
#   - no privilege escalation: setuid/setgid bits stripped, no sudo (apple/container has no --security-opt)
#   - a root ENTRYPOINT does all privileged setup (perms, firewall, proxy), then
#     drops to a keep-alive; dev sessions run as the unprivileged `dev` user
#   - all egress forced through an in-container squid proxy (hostname allowlist)
#
# NOTE (security posture vs git-agent): the workspace is READ-WRITE here and NO
# git push token / ssh signing socket is forwarded. Commit & push via the
# dedicated `git-agent` sandbox; this box is for editing and running code.

FROM debian:bookworm-slim@sha256:60eac759739651111db372c07be67863818726f754804b8707c90979bda511df

ARG TZ=UTC
ENV TZ="$TZ"
ENV DEBIAN_FRONTEND=noninteractive

# Base utilities + a non-root `dev` user (UID 1000). sudo intentionally absent.
# Reproducibility trade-off (see also the hadolint DL3008 ignore in ci.yml): apt
# packages and `gh` below are intentionally NOT version-pinned — Debian point
# releases drop older versions from the mirror, so a hard pin would break cache-miss
# rebuilds outright. Integrity comes instead from (a) SHA256-verified downloads for
# the tools that matter most (Node + Python, further down) and (b) the launch-time
# verify-pins gate, which fails closed on any drift of node/npm/claude/gh/git/
# python3 against binary-pins.txt.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg lsb-release xz-utils \
      git less procps jq locales openssh-client unzip rsync \
      iptables dnsutils openssl \
      squid-openssl \
      build-essential pkg-config \
  && rm -rf /var/lib/apt/lists/* \
  && useradd --create-home --uid 1000 --shell /bin/bash dev \
  && echo "C.UTF-8 UTF-8" > /etc/locale.gen && locale-gen

# GitHub CLI from the official cli.github.com apt repo.
RUN install -d -m 0755 /etc/apt/keyrings \
  && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null \
  && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
  && apt-get update && apt-get install -y --no-install-recommends gh \
  && rm -rf /var/lib/apt/lists/*

# --- Node.js — official static build (matches the other sandboxes) -----------
ARG NODE_VERSION=24.15.0
RUN set -eux; \
    case "$(dpkg --print-architecture)" in amd64) na=x64 ;; arm64) na=arm64 ;; *) exit 1 ;; esac; \
    curl -fsSLo /tmp/node.tar.xz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${na}.tar.xz"; \
    curl -fsSLo /tmp/node-SHASUMS256.txt "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt"; \
    want="$(grep " node-v${NODE_VERSION}-linux-${na}.tar.xz\$" /tmp/node-SHASUMS256.txt | cut -d' ' -f1)"; \
    echo "${want}  /tmp/node.tar.xz" | sha256sum -c -; \
    rm /tmp/node-SHASUMS256.txt; \
    tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 --no-same-owner \
      --exclude='*/CHANGELOG.md' --exclude='*/LICENSE' --exclude='*/README.md'; \
    rm /tmp/node.tar.xz; \
    mkdir -p /usr/local/share/npm-global

# --- Python 3.12 — python-build-standalone (prebuilt relocatable CPython) -----
# Same approach as Vision: a pinned release asset (NOT resolved via the GitHub
# API, whose 60-req/hr unauthenticated limit fails parallel builds). python3 +
# pip + venv + ssl, no compile. Bump by editing PY_RELEASE/PY_VERSION.
ARG PY_RELEASE=20260510
ARG PY_VERSION=3.12.13
RUN set -eux; \
    case "$(dpkg --print-architecture)" in arm64) pa=aarch64 ;; amd64) pa=x86_64 ;; *) exit 1 ;; esac; \
    url="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_RELEASE}/cpython-${PY_VERSION}+${PY_RELEASE}-${pa}-unknown-linux-gnu-install_only.tar.gz"; \
    echo "python-build-standalone: $url"; \
    curl -fsSLo /tmp/py.tar.gz "$url"; \
    curl -fsSLo /tmp/py-SHA256SUMS "https://github.com/astral-sh/python-build-standalone/releases/download/${PY_RELEASE}/SHA256SUMS"; \
    want="$(grep " cpython-${PY_VERSION}+${PY_RELEASE}-${pa}-unknown-linux-gnu-install_only.tar.gz\$" /tmp/py-SHA256SUMS | cut -d' ' -f1)"; \
    echo "${want}  /tmp/py.tar.gz" | sha256sum -c -; \
    rm /tmp/py-SHA256SUMS; \
    mkdir -p /opt/python; \
    tar -xzf /tmp/py.tar.gz --strip-components=1 -C /opt/python; \
    rm /tmp/py.tar.gz; \
    [ -x /opt/python/bin/python ] || ln -s python3 /opt/python/bin/python; \
    /opt/python/bin/python3 --version; /opt/python/bin/python3 -m venv --help >/dev/null

# python-build-standalone bakes CC=clang into sysconfig; this image has gcc (via
# build-essential), not clang. Pin CC=gcc so source C-extension builds work.
ENV CC=gcc CXX=g++

# Build-time global prefix for the agent CLI (claude). Kept ROOT-owned (see the
# claude install below) so the unprivileged dev user can't overwrite the pinned
# claude/npm binaries post-launch (the verify-pins gate fingerprints these paths).
# Runtime `npm i -g` (safe-chain) uses a SEPARATE dev-owned prefix,
# /home/dev/.npm-global. That prefix is dev-WRITABLE and persists in the writable
# layer, so it goes LAST on PATH — after the system dirs too. A compromised agent
# must not be able to shadow a pinned or system tool (claude/npm/node/git/gh/
# python3, or the sha256sum/awk that verify-pins itself relies on) by dropping a
# same-named binary there. The pinned tool dirs stay first so claude resolves to
# the pinned copy; dev's own global installs still resolve, just at lowest priority.
ENV NPM_CONFIG_PREFIX=/usr/local/share/npm-global
ENV PATH=/usr/local/share/npm-global/bin:/opt/python/bin:/usr/local/bin:$PATH:/home/dev/.npm-global/bin
# Login shells reset PATH via /etc/profile; re-add the tool dirs for them too.
RUN printf 'export PATH="/usr/local/share/npm-global/bin:/opt/python/bin:/usr/local/bin:$PATH:/home/dev/.npm-global/bin"\n' \
      > /etc/profile.d/00-sandbox-path.sh

# --- Claude Code CLI ----------------------------------------------------------
# Installed as root into the root-owned global prefix and deliberately NOT chown'd
# to dev — so the pinned claude/npm cannot be replaced by the dev user.
RUN npm install -g @anthropic-ai/claude-code@2.1.173 \
  && claude --version

# Runtime global installs (safe-chain in post-create) go to a SEPARATE dev-owned
# prefix, so they never need write access to the pinned root prefix above.
RUN install -d -o dev -g dev -m 0755 /home/dev/.npm-global
ENV NPM_CONFIG_PREFIX=/home/dev/.npm-global

# Inbound ports default to NONE; bin/dev binds a per-run /etc/egress/inbound-ports
# (RO) over this when DEV_SANDBOX_PORTS is set. The dir must exist for the bind.
RUN mkdir -p /etc/egress && : > /etc/egress/inbound-ports

# Pre-build the squid TLS-bump cert + cert DB (entrypoint regenerates if missing).
# Placed BEFORE the lifecycle/allowlist COPY block below so a routine sync.sh
# allowlist edit — the file that changes most often — does NOT invalidate this
# (expensive: RSA keygen + cert-DB init) layer on rebuild.
RUN mkdir -p /etc/squid/certs /var/lib/squid /var/log/squid /var/spool/squid \
  && openssl req -new -newkey rsa:2048 -days 3650 -nodes -x509 \
       -subj "/CN=dev-sandbox-egress-proxy" \
       -keyout /etc/squid/certs/bump.key -out /etc/squid/certs/bump.crt \
  && cat /etc/squid/certs/bump.key /etc/squid/certs/bump.crt > /etc/squid/certs/bump.pem \
  && chmod 600 /etc/squid/certs/bump.key /etc/squid/certs/bump.pem \
  && /usr/lib/squid/security_file_certgen -c -s /var/lib/squid/ssl_db -M 4MB \
  && chown -R proxy:proxy /etc/squid/certs /var/lib/squid /var/log/squid /var/spool/squid

# Pre-create dirs backed by named volumes so a fresh volume inherits dev ownership.
RUN install -d -o dev -g dev -m 0700 /home/dev/.claude \
 && install -d -o dev -g dev -m 0755 /home/dev/.config /home/dev/.config/gh /home/dev/.local

# Pin the agent's executables: record SHA-256 of each binary PATH resolves to, so
# dev-sandbox-verify-pins can fail-closed on drift at launch. A legitimate tool
# upgrade changes these hashes — rebuild the image to re-pin. This fingerprints only
# the tools installed above (none of the COPY'd files), so it too sits BEFORE the
# frequently-edited COPY block, and BEFORE the setuid strip (which changes only mode
# bits, not the hashed content).
RUN set -eux; \
    pinfile=/usr/local/share/dev-sandbox/binary-pins.txt; \
    mkdir -p /usr/local/share/dev-sandbox; \
    : > "$pinfile"; \
    for b in node npm claude gh git python3; do \
      p="$(command -v "$b")"; \
      h="$(sha256sum "$p" | awk '{print $1}')"; \
      printf '%s\t%s\t%s\n' "$b" "$p" "$h" >> "$pinfile"; \
    done; \
    chmod 0644 "$pinfile"; \
    cat "$pinfile"

# Bake lifecycle + helpers + privileged scripts + squid.conf + the generated
# allowlist LAST (only the setuid strip follows). These are exactly the layers a
# routine sync.sh edit changes, so keeping them after cert-gen + pin-gen means a
# rebuild re-runs only these cheap COPYs + the strip — not the expensive layers
# above. (Run from the IMAGE, never the mounted workspace.)
COPY post-create.sh  /usr/local/share/dev-sandbox/post-create.sh
COPY post-start.sh   /usr/local/share/dev-sandbox/post-start.sh
COPY bin/doctor      /usr/local/bin/dev-sandbox-doctor
COPY bin/verify-pins /usr/local/bin/dev-sandbox-verify-pins
RUN chmod 0755 /usr/local/bin/dev-sandbox-doctor /usr/local/bin/dev-sandbox-verify-pins
# Privileged scripts (root-owned, source-only repo copies).
COPY init-firewall.sh /usr/local/sbin/egress-firewall
COPY perms-fix.sh     /usr/local/sbin/dev-sandbox-perms-fix
COPY entrypoint.sh    /usr/local/sbin/dev-sandbox-entrypoint
COPY squid.conf       /etc/squid/squid.conf
COPY allowlist.txt    /etc/squid/allowlist.txt
RUN chmod 0755 /usr/local/sbin/egress-firewall /usr/local/sbin/dev-sandbox-perms-fix /usr/local/sbin/dev-sandbox-entrypoint \
  && chown root:root /usr/local/sbin/egress-firewall /usr/local/sbin/dev-sandbox-perms-fix /usr/local/sbin/dev-sandbox-entrypoint

# Strip ALL setuid/setgid bits image-wide (defense-in-depth). MUST be the last RUN
# so it covers every file COPY'd above.
RUN find / -xdev -type f -perm /6000 -exec chmod -s {} + 2>/dev/null || true

WORKDIR /workspaces/project

# NOTE: docker-only. apple/container 1.0.0 has no healthcheck runner, so this
#   HEALTHCHECK is a no-op under the current runtime; real supervision is the
#   entrypoint keep-alive loop (squid auto-restart). Kept for parity / docker use.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD bash -c 'pgrep -x squid >/dev/null && /usr/sbin/iptables -S OUTPUT 2>/dev/null | grep -q "^-P OUTPUT DROP"'

ENTRYPOINT ["/usr/local/sbin/dev-sandbox-entrypoint"]

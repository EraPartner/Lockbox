# Canonical devcontainer egress lock

Single source of truth for the egress firewall + squid proxy shared by the
Brain, Vision, Watchman, and git-agent devcontainers. Previously each carried its
own copy of `init-firewall.sh` + `squid.conf`; now you edit them **here** and run
`./sync.sh` (one edit instead of four).

## Files

- `init-firewall.sh` — iptables default-deny, egress allowed only for the squid
  proxy UID. Identical everywhere; per-project bits are data files it reads:
  - `/etc/squid/allowlist.txt` — the hostname allowlist (per project, NOT synced).
  - `/etc/egress/inbound-ports` — optional, one TCP port per line, for projects
    that publish services (Vision/Watchman). Absent = no inbound (Brain/git-agent).
- `squid.conf` — peek+splice SNI allowlist proxy. Identical everywhere.
- `sync.sh` — copies the two files into each project's `.devcontainer/`.

## Workflow

```sh
# edit init-firewall.sh or squid.conf here, then:
./sync.sh
# rebuild the affected containers so the baked copies update:
devcontainer up --remove-existing-container --workspace-folder <project> [--config ...]
```

## Per-project wiring (set once)

Each project's Dockerfile bakes the synced files to GENERIC paths:
`COPY init-firewall.sh /usr/local/sbin/egress-firewall` and
`COPY squid.conf /etc/squid/squid.conf`. Its entrypoint calls
`/usr/local/sbin/egress-firewall` and checks `/run/egress-firewall-ok`; its
post-start checks the same sentinel. Projects with inbound services bake an
`/etc/egress/inbound-ports` file (via the Dockerfile).

---
paths:
  - "init-firewall.sh"
  - "**/init-firewall.sh"
  - "squid.conf"
  - "**/squid.conf"
  - "base-allowlist.txt"
  - "**/allowlist*.txt"
  - "sync.sh"
---

# Egress enforcement internals

Mechanism detail for the canonical egress files. The non-negotiable rules that govern changing
them live in `## Security invariants` in `AGENTS.md` and apply whether or not this file is loaded.

## How egress is enforced (the core model)

Every sandbox forces all outbound traffic through two independent layers applied by the root
entrypoint on each start, **fail-closed**:

1. **iptables/ip6tables egress lock** (`init-firewall.sh`, baked at
   `/usr/local/sbin/egress-firewall`): default-DROP set FIRST, then only the `proxy` UID may
   originate outbound packets. Everything else must use the proxy over loopback or is dropped
   (rate-limit-logged: `dmesg | grep egress-deny`). IPv6 OUTPUT is default-deny. The cloud-metadata
   IP (`169.254.169.254`, `fd00:ec2::254`) is dropped at L3 ahead of the proxy-UID accept. A
   `/run/egress-firewall-ok` sentinel is written **only after** re-verifying the policy + key rules
   with `iptables -C`; its absence fails the lifecycle.
2. **In-container squid SNI proxy** (`squid.conf`, peek+splice) on `127.0.0.1:3128`. squid peeks the
   TLS ClientHello SNI and *splices* allowed hostnames (tunnels without decrypting — **end-to-end
   TLS preserved, no MITM / CA injection**) and terminates the rest. It is **CONNECT-only**
   (`http_access deny !CONNECT`) so a non-CONNECT absolute-URI request can't downgrade to cleartext
   and bypass the SNI check; HTTPS-only (`Safe_ports 443`). ECH (hidden SNI) → no allowed name →
   terminated. squid is supervised by the entrypoint keep-alive; if it dies, egress stays denied
   (fail-closed) until it restarts.

Defense-in-depth on top: **safe-chain** (installed in `post-create`, wired via `BASH_ENV`) screens
`npm`/`pip` installs against `malware-list.aikido.dev`, and a **launch-integrity gate**
(`bin/verify-pins`) fingerprints `node npm claude codex gh git python3` at build and aborts the launch on
drift.

## Allowlist composition (base + overlay)

- `base-allowlist.txt` — the shared floor EVERY container gets (Anthropic API + minimal GitHub:
  `github.com`, `api.github.com`, `codeload.github.com`). Multi-tenant hosts
  (`raw.githubusercontent.com`, `objects.githubusercontent.com`) are deliberately kept OFF the base
  and live only in the generic sandbox's extras.
- `<project>/.devcontainer/allowlist.extra.txt` — that one project's deltas.
- `sync.sh` concatenates base + extra (de-duped) into the baked `.devcontainer/allowlist.txt`.

To change egress: edit the base or an extra, run `./sync.sh`, then **rebuild** the affected
container so the new allowlist is re-baked and re-read.

**Baked vs bind-mounted — an important difference.** The LockBox provider launchers and
the sibling projects COPY the allowlist into the image, so an allowlist change needs a rebuild
(`*_REBUILD=1`); reused/started containers are warned by `sandbox_warn_stale_allowlist`. The
generic `sandbox/.devcontainer/bin/dev` instead **bind-mounts** its allowlist and does
`squid -k reconfigure` on change — and reads a per-target overlay from the workspace only after
**interactive** confirmation (fail-closed / ignored on a non-interactive launch, since the
workspace is untrusted content).

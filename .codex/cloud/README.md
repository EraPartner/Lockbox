# Codex cloud environment

Set the environment setup command to:

```bash
bash .codex/cloud/setup.sh
```

The setup installs the portable global working agreement and marks the shell as a cloud session.
The repository itself has no additional dependency bootstrap.

## Git handoff

Codex cloud may check out the repository without a Git remote or a shell push credential. This is
not a task blocker. Complete the changes and portable checks, then use Codex's **Open pull request**
action to publish the result.

After creation, a pull-request-linked cloud task may inspect comments and checks, make in-scope
follow-up changes, and let the connected GitHub integration update the same branch. When the user
explicitly asks to merge, the integration may do so only after required checks and approvals pass
and no blocking review remains. Never use an admin bypass or directly update a protected branch.

Do not add a `GH_TOKEN` or persist another GitHub credential in the cloud container. Regular
desktop and cloud development sessions do not publish with shell Git commands. Desktop sessions
leave the reviewed working-tree diff for an explicitly launched LockBox `git-agent` session;
cloud uses the platform-managed pull-request flow above.

The authorized `git-agent` publication environment stages, signs, and pushes within its own
boundary. Repository hooks remain disabled there. Follow `git-agent/PUBLICATION.md` for the exact
content hashes, validation evidence, selected-path handoff, and trusted staged audit. Do not run
arbitrary target-repository validation commands inside the publication boundary.

Do not copy the host hooks into this environment. They protect the local Mac and use host paths.
Cloud runs cannot verify Apple Container, Secure Enclave signing, host mounts, or macOS firewall
behavior. Run those checks in a local session before release or security-sensitive changes.

## Generated working agreement

`.codex/cloud/AGENTS.md` is generated. Its authored sources are
`dotfiles/Other/codex/global-agents.md` and `dotfiles/Other/codex/cloud/AGENTS.md`.
The generator preserves portable global sections and combines them with the cloud-specific
agreement. Edit those canonical sources, then run
`python3 dotfiles/Other/codex/generate-cloud-policy.py` from the parent fleet directory.
Do not edit generated agreements or their vendored `policy/` inputs independently.

The local fleet check is
`python3 dotfiles/Other/codex/generate-cloud-policy.py --check`. It compares generated outputs,
vendored inputs, and checker copies against the canonical sources across all four fleet targets;
missing targets fail. Use `--project PROJECT` to check an explicitly selected target.

Each repository can independently run `python3 .codex/cloud/check-instructions.py` from its root.
CI requires this check in its existing vendored/cloud-tooling job. This verifies that the generated
agreement matches the repository's vendored inputs; it cannot establish freshness against an
absent sibling dotfiles checkout. Use the fleet check for that stronger guarantee. Project-level
`AGENTS.md` instructions continue to apply separately.

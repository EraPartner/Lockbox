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

Do not add a `GH_TOKEN` or persist another GitHub credential in the cloud container. Direct terminal
pushes belong in a local LockBox task, where the existing SSH remote, Secure Enclave key, commit
signing, and local hooks are available.

Do not copy the host hooks into this environment. They protect the local Mac and use host paths.
Cloud runs cannot verify Apple Container, Secure Enclave signing, host mounts, or macOS firewall
behavior. Run those checks in a local session before release or security-sensitive changes.

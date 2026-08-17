# Codex cloud environment

Set the environment setup command to:

```bash
bash .codex/cloud/setup.sh
```

The setup installs the portable global working agreement and marks the shell as a cloud session.
The repository itself has no additional dependency bootstrap.

Do not copy the host hooks into this environment. They protect the local Mac and use host paths.
Cloud runs cannot verify Apple Container, Secure Enclave signing, host mounts, or macOS firewall
behavior. Run those checks in a local session before release or security-sensitive changes.

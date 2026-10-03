#!/usr/bin/env python3
"""Hold a host profile startup lock until the launcher closes descriptor 9."""

import fcntl
import os
from pathlib import Path
import stat
import sys
import time


def main():
    path = Path(sys.argv[1])
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink():
            raise ValueError("startup lock directory must not contain symlinks")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("startup lock must be a regular private file")
    deadline = time.monotonic() + 30
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise ValueError(
                    "another launch is still starting this profile; retry after it finishes"
                )
            time.sleep(0.1)
    # Bash 3.2 cannot close a descriptor supplied through a variable. Use one
    # fixed inherited descriptor and close it before native login or the session.
    os.dup2(descriptor, 9, inheritable=True)
    if descriptor != 9:
        os.close(descriptor)
    os.environ["LOCKBOX_STARTUP_LOCK"] = str(path)
    os.execvp("bash", ["bash", *sys.argv[2:]])


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print(f"launcher: cannot acquire startup lock: {error}", file=sys.stderr)
        sys.exit(1)

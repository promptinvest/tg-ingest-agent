"""Shared, compatible edits for Cara's operator-owned environment files."""
import os
import re
from pathlib import Path


def set_env_value(content, key, value):
    """Replace one assignment literally; append absent keys and refuse duplicates."""
    new, count = re.subn(rf"(?m)^{re.escape(key)}=.*$", lambda _: f"{key}={value}", content)
    if count == 1:
        return new
    if count > 1:
        raise ValueError(f"{key} is assigned {count} times; fix the file by hand")
    return content.rstrip("\n") + f"\n{key}={value}\n"


def write_env_atomically(path, content, *, sync_directory=False):
    """Keep the prior file until replace, private backup and stale-temp refusal.

    The bootstrap entry point additionally syncs the containing directory; staged
    token/key entry points retain their original file-only fsync behavior.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise SystemExit(f"a stale {tmp} is in the way — an earlier run was killed "
                         f"mid-write. Inspect it, then: rm -f {tmp}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if previous is not None:
            backup = path.with_name(path.name + ".bak")
            bak_fd = os.open(backup, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            # os.open's mode applies only on create; older backups may be looser.
            os.fchmod(bak_fd, 0o600)
            with os.fdopen(bak_fd, "w", encoding="utf-8") as handle:
                handle.write(previous)
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    os.chmod(path, 0o600)
    if sync_directory:
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:  # Preserve bootstrap behavior on filesystems denying fsync.
            pass
    return path

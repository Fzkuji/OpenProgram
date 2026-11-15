"""Windows' rules for deleting a file, for tests that run elsewhere."""
from __future__ import annotations

import os
import stat


def windows_unlink(monkeypatch, *, locked=()):
    """``os.unlink`` as Windows has it: a read-only file is refused, and so is a held one."""
    real = os.unlink

    def unlink(path, *, dir_fd=None):
        if os.path.basename(os.fspath(path)) in locked:
            raise PermissionError(13, "The process cannot access the file because it is being used by another process", path)
        if not os.lstat(path, dir_fd=dir_fd).st_mode & stat.S_IWUSR:
            raise PermissionError(13, "Access is denied", path)
        return real(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", unlink)


def tree_with_read_only_objects(root):
    """A tree holding what Git leaves: object files without the write bit."""
    objects = root / ".git" / "objects" / "ab"
    objects.mkdir(parents=True)
    for name in ("cdef01", "cdef02"):
        (objects / name).write_bytes(b"object")
        (objects / name).chmod(0o444)
    (root / "kept.txt").write_text("x", encoding="utf-8")
    return objects

"""Write fake tool executables onto inodes the OS has already checked.

macOS assesses every newly created executable file the first time it runs,
which costs 0.3-1 s per file on a busy machine. Shell-script fixtures that
write a dozen fake tools per test spend most of their time there. The
assessment belongs to the inode, and rewriting a file in place keeps its
inode, so this pool hands each test inodes that earlier tests already ran.
Every test still gets its own paths and its own file contents; only the
inode is recycled after the test is done with it.
"""
from __future__ import annotations

import os
from pathlib import Path


class ExecutablePool:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._free: list[Path] = []
        self._issued: list[tuple[Path, int]] = []

    def write(self, path: Path, text: str, *, mode: int = 0o755) -> Path:
        """Write ``text`` as an executable at ``path``, reusing a checked inode."""
        issued = {inode for _, inode in self._issued}
        try:
            current = path.lstat()
        except FileNotFoundError:
            current = None
        reuse_in_place = current is not None and current.st_ino in issued
        if not reuse_in_place and self._free:
            spare = self._free.pop()
            try:
                os.replace(spare, path)
            except OSError:
                # A different file system cannot take the inode; fall back to
                # a fresh file, which only costs the one-time check.
                spare.unlink(missing_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(mode)
        inode = path.lstat().st_ino
        if inode not in issued:
            self._issued.append((path, inode))
        return path

    def reclaim(self) -> None:
        """Take back the inodes the finished test still holds at their paths."""
        for path, inode in self._issued:
            try:
                current = path.lstat()
            except FileNotFoundError:
                continue
            if current.st_ino != inode or not path.is_file() or path.is_symlink():
                continue
            spare = self._root / str(inode)
            try:
                os.replace(path, spare)
            except OSError:
                continue
            self._free.append(spare)
        self._issued.clear()

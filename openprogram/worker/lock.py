"""Single-holder file lock for the persistent worker.

Only one worker process can run at a time per profile. Uses
``fcntl.flock`` (macOS + Linux) via the cross-platform
:mod:`openprogram._compat` shim, which emulates the same surface
on Windows via :func:`msvcrt.locking`. The lock file also stores
the holder PID so peek-style callers (`worker status`) can report
the owner without trying to acquire.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import IO, Optional

from openprogram import _compat as fcntl

from .paths import lock_path


class WorkerLock:
    """Exclusive file lock held by the running worker."""

    def __init__(self) -> None:
        self.path: Path = lock_path()
        self._fh: Optional[IO[str]] = None
        self.holder_pid: Optional[int] = None

    def try_acquire(self) -> bool:
        """Non-blocking acquire. Populates ``holder_pid`` on failure."""
        fh = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.seek(0)
            raw = fh.read().strip()
            try:
                self.holder_pid = int(raw) if raw else None
            except ValueError:
                self.holder_pid = None
            fh.close()
            return False

        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        os.fsync(fh.fileno())
        self._fh = fh
        self.holder_pid = os.getpid()
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.flush()
        except OSError:
            pass
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            self._fh.close()
        except OSError:
            pass
        self._fh = None


def read_holder_pid() -> Optional[int]:
    """Peek at the current lock holder. Returns None if no holder."""
    p = lock_path()
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None

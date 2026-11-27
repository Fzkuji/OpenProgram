"""Single-holder file lock for the persistent worker.

Only one worker process can run at a time per profile. Uses
``fcntl.flock`` (macOS + Linux) through
:mod:`openprogram.host._platform.locking`, which emulates the same surface
on Windows via :func:`msvcrt.locking`. The lock file also stores
the holder PID so peek-style callers (`worker status`) can report
the owner without trying to acquire.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import IO, Optional

from openprogram.host._platform import locking

from .paths import lock_path


_held_paths: set[str] = set()
_held_paths_lock = threading.Lock()


def _lock_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


class WorkerLock:
    """Exclusive file lock held by the running worker."""

    def __init__(self) -> None:
        self.path: Path = lock_path()
        self._fh: Optional[IO[str]] = None
        self.holder_pid: Optional[int] = None
        self._claimed = False

    def try_acquire(self, *, claim: bool = True) -> bool:
        """Non-blocking acquire. Populates ``holder_pid`` on failure.

        ``claim=False`` only finds out whether the lock is free: the caller is
        not the worker, so it writes no PID and does not become the process
        whose appends land in ``main.jsonl``.
        """
        fh = open(self.path, "a+", encoding="utf-8")
        try:
            locking.flock(fh.fileno(), locking.LOCK_EX | locking.LOCK_NB)
        except BlockingIOError:
            # The locked byte lies past the PID (host/_platform/locking.py),
            # so the holder's PID reads the same way on every platform.
            fh.seek(0)
            raw = fh.read().strip()
            try:
                self.holder_pid = int(raw) if raw else None
            except ValueError:
                self.holder_pid = None
            fh.close()
            return False

        if not claim:
            self._fh = fh
            return True
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        os.fsync(fh.fileno())
        self._fh = fh
        self.holder_pid = os.getpid()
        with _held_paths_lock:
            _held_paths.add(_lock_key(self.path))
        from openprogram.state.store.session.files import session_log

        session_log.claim_main_writer()
        self._claimed = True
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
            locking.flock(self._fh.fileno(), locking.LOCK_UN)
        except OSError:
            pass
        try:
            self._fh.close()
        except OSError:
            pass
        self._fh = None
        if not self._claimed:
            return
        self._claimed = False
        with _held_paths_lock:
            _held_paths.discard(_lock_key(self.path))
        from openprogram.state.store.session.files import session_log

        session_log.release_main_writer()


def held_in_this_process(path: Path) -> bool:
    """Return whether this process holds the worker lock at ``path``.

    Answers from the in-process registry, without probing the file, so it is
    cheap and never disturbs another process's lock.
    """
    with _held_paths_lock:
        return _lock_key(path) in _held_paths


def read_holder_pid() -> Optional[int]:
    """Peek at the PID the lock holder wrote. Returns None if there is none.

    Readable while the lock is held, on Windows too: the locked byte lies past
    the PID. Only a hint; :func:`is_held_by` proves the holder is live.
    """
    p = lock_path()
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8").strip()
        pid = int(raw) if raw else None
        return pid if pid is not None and pid > 0 else None
    except (OSError, ValueError):
        return None


def is_held_by(pid: int) -> bool:
    """Return whether ``pid`` owns the live worker lock.

    The PID file alone is not proof because it can survive a crash.  Probe the
    actual advisory lock first: acquiring it means there is no live holder.
    """
    probe = WorkerLock()
    if probe.try_acquire(claim=False):
        probe.release()
        return False
    return probe.holder_pid == pid

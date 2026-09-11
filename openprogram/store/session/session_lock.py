"""Interprocess session lock keyed by stable session id.

Fork children, CLI, worker, rewind and migration share one flock file
under the application state root. Path-based locks are not sufficient
because placement can change and subprocesses write independently.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from openprogram import _compat as fcntl


def _lock_dir() -> Path:
    from openprogram.paths import get_state_dir
    path = Path(get_state_dir()) / "sessions" / ".locks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_lock_path(session_id: str) -> Path:
    safe = session_id.replace("/", "_").replace("\\", "_").replace("..", "_")
    return _lock_dir() / f"{safe}.lock"


@contextmanager
def session_interprocess_lock(
    session_id: str,
    *,
    timeout: float | None = None,
    blocking: bool = True,
) -> Iterator[None]:
    """Exclusive flock for one session id.

    After acquiring, callers must re-read session placement. ``timeout``
    is seconds; on expiry raises :class:`TimeoutError` without taking
    the lock. Non-blocking mode raises :class:`BlockingIOError`.
    """
    if not session_id or session_id in {".", ".."}:
        raise ValueError("session_id is required")
    path = session_lock_path(session_id)
    handle = path.open("a+")
    mode = fcntl.LOCK_EX
    deadline = None if timeout is None else (time.monotonic() + timeout)
    try:
        while True:
            try:
                flags = mode if blocking and deadline is None else (mode | fcntl.LOCK_NB)
                fcntl.flock(handle.fileno(), flags)
                break
            except BlockingIOError:
                if not blocking:
                    raise
                if deadline is None:
                    time.sleep(0.05)
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"session lock busy: {session_id}")
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def session_lock_available(session_id: str) -> bool:
    """True when no other process currently holds the exclusive lock."""
    try:
        with session_interprocess_lock(session_id, blocking=False):
            return True
    except BlockingIOError:
        return False

"""Interprocess session lock keyed by stable session id.

Fork children, CLI, worker, rewind and migration share one flock file
under the application state root. Path-based locks are not sufficient
because placement can change and subprocesses write independently.
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from openprogram import _compat as fcntl


_held_session_locks = threading.local()


def _lock_dir(root: str | Path | None = None) -> Path:
    from openprogram.paths import get_state_dir
    base = Path(root) if root is not None else Path(get_state_dir()) / "sessions"
    path = base / ".locks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_lock_path(session_id: str, root: str | Path | None = None) -> Path:
    safe = session_id.replace("/", "_").replace("\\", "_").replace("..", "_")
    return _lock_dir(root) / f"{safe}.lock"


@contextmanager
def session_interprocess_lock(
    session_id: str,
    *,
    timeout: float | None = None,
    blocking: bool = True,
    reentrant: bool = False,
    root: str | Path | None = None,
) -> Iterator[None]:
    """Exclusive flock for one session id.

    After acquiring, callers must re-read session placement. ``timeout``
    is seconds; on expiry raises :class:`TimeoutError` without taking
    the lock. Non-blocking mode raises :class:`BlockingIOError`.
    """
    if not session_id or session_id in {".", ".."}:
        raise ValueError("session_id is required")
    path = session_lock_path(session_id, root)
    held = getattr(_held_session_locks, "keys", set())
    held_key = (os.getpid(), str(path.resolve()))
    if reentrant and held_key in held:
        # flock is per open file description, so opening the same lock file
        # again from a writer-held thread would deadlock that thread.  Keep
        # the process-local nesting reentrant; other processes still wait on
        # the original descriptor.
        yield
        return
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
        held.add(held_key)
        _held_session_locks.keys = held
        try:
            yield
        finally:
            held.discard(held_key)
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def session_lock_available(
    session_id: str, *, root: str | Path | None = None,
) -> bool:
    """True when no other process currently holds the exclusive lock."""
    try:
        with session_interprocess_lock(session_id, blocking=False, root=root):
            return True
    except BlockingIOError:
        return False


@contextmanager
def registry_file_lock(root: str | Path, name: str, *, timeout: float = 15.0) -> Iterator[None]:
    """Lock a cross-session registry while it is read-modify-written."""
    directory = Path(root) / ".locks"
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / f".{name}.lock").open("a+")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"registry lock busy: {name}")
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

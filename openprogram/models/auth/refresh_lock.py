"""Cross-process lock held while one OAuth refresh token is spent.

A refresh token rotates: the first process to spend it gets a new pair and
every later spend of the old one is refused (``invalid_grant``), which
signs that holder out. Two workers, a worker and its cron subprocess, or
two local instances reading the same external login must therefore take
turns, and each one re-reads the stored login once it holds the lock, so a
login someone else refreshed meanwhile is adopted instead of refreshed
again.

The lock is an advisory ``flock`` on a sibling ``*.refresh.lock`` file. It
is separate from the lock that guards a credential file's writes, so the
holder can still save the refreshed login.
"""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import AsyncIterator, Iterator

from openprogram.host._platform import locking

# A refresh is one HTTPS POST with a 30 s timeout; wait a little longer
# than that for a peer that is in the middle of one.
REFRESH_LOCK_TIMEOUT_S = 45.0
_POLL_S = 0.05


def refresh_lock_path(credential_file: Path) -> Path:
    """The lock file that serialises refreshes of ``credential_file``."""
    path = Path(credential_file)
    return path.with_name(path.name + ".refresh.lock")


def _open(lock_path: Path) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    return os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)


def _try_lock(fd: int) -> bool:
    try:
        locking.flock(fd, locking.LOCK_EX | locking.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _release(fd: int) -> None:
    try:
        locking.flock(fd, locking.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def held(credential_file: Path, timeout: float = REFRESH_LOCK_TIMEOUT_S) -> Iterator[None]:
    """Hold the refresh lock of ``credential_file`` (blocking caller)."""
    lock_path = refresh_lock_path(credential_file)
    fd = _open(lock_path)
    deadline = time.monotonic() + timeout
    try:
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"another process is refreshing {credential_file}")
            time.sleep(_POLL_S)
    except BaseException:
        os.close(fd)
        raise
    try:
        yield
    finally:
        _release(fd)


@asynccontextmanager
async def held_async(credential_file: Path, timeout: float = REFRESH_LOCK_TIMEOUT_S) -> AsyncIterator[None]:
    """Hold the refresh lock of ``credential_file`` without blocking the loop."""
    lock_path = refresh_lock_path(credential_file)
    fd = _open(lock_path)
    deadline = time.monotonic() + timeout
    try:
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"another process is refreshing {credential_file}")
            await asyncio.sleep(_POLL_S)
    except BaseException:
        os.close(fd)
        raise
    try:
        yield
    finally:
        _release(fd)


__all__ = ["REFRESH_LOCK_TIMEOUT_S", "held", "held_async", "refresh_lock_path"]

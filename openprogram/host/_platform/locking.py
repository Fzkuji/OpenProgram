"""Advisory file locking with the POSIX ``fcntl.flock`` surface on every platform.

The module exposes ``flock`` and ``LOCK_EX`` / ``LOCK_UN`` / ``LOCK_NB``. On
POSIX they are :mod:`fcntl` itself. Windows has no ``fcntl``, so single-byte
advisory locking is emulated with :func:`msvcrt.locking`, and the
``PermissionError`` it raises on contention becomes :class:`BlockingIOError`,
so call sites keep the POSIX exception pattern::

    from openprogram.host._platform import locking as fcntl

    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

Windows semantics:

* The lock is on one byte far past the end of any data (``_LOCK_OFFSET``).
  msvcrt refuses reads and writes that overlap a byte another handle has
  locked; a lock on a data byte would make the file unreadable to every other
  process, which POSIX ``flock`` never does. A lock file therefore stays
  readable while it is held (``worker.lock`` names its holder that way). The
  fd's position is put back after each call.
* A blocking acquire (``LOCK_EX`` without ``LOCK_NB``) retries every 100 ms
  because ``msvcrt.LK_LOCK`` gives up after about 10 seconds.
* The lock is per process and per fd. Re-acquiring the same byte on the same
  fd is an error on Windows, matching POSIX exclusive semantics; no call site
  relies on re-entrant locking.
"""
from __future__ import annotations


try:  # POSIX (macOS, Linux)
    import fcntl as _fcntl

    LOCK_EX = _fcntl.LOCK_EX
    LOCK_UN = _fcntl.LOCK_UN
    LOCK_NB = _fcntl.LOCK_NB

    def flock(fd: int, mode: int) -> None:
        _fcntl.flock(fd, mode)

except ImportError:  # Windows
    import errno as _errno
    import msvcrt as _msvcrt
    import os as _os
    import time as _time

    # Bit values picked to be distinct; only ever consumed by `flock()`
    # below, so the exact numbers don't matter as long as they don't collide.
    LOCK_EX = 0x2
    LOCK_NB = 0x4
    LOCK_UN = 0x8

    # One byte at a fixed offset no locked file of ours reaches (they are
    # small lock and state files). Windows allows locking past the end of a
    # file, and msvcrt.locking starts at the current position, so each call
    # seeks there and back. Below 2**31, where every CRT's offset fits.
    _LOCK_OFFSET = 0x7FFFFFFE
    _LOCK_NBYTES = 1
    _RETRY_INTERVAL = 0.1

    def _locking(fd: int, operation: int) -> None:
        position = _os.lseek(fd, 0, _os.SEEK_CUR)
        _os.lseek(fd, _LOCK_OFFSET, _os.SEEK_SET)
        try:
            _msvcrt.locking(fd, operation, _LOCK_NBYTES)
        finally:
            _os.lseek(fd, position, _os.SEEK_SET)

    def flock(fd: int, mode: int) -> None:
        if mode & LOCK_UN:
            try:
                _locking(fd, _msvcrt.LK_UNLCK)
            except OSError:
                # Match POSIX: releasing a lock we don't hold is a
                # silent no-op for our callers.
                pass
            return

        if mode & LOCK_NB:
            try:
                _locking(fd, _msvcrt.LK_NBLCK)
            except OSError as e:
                # msvcrt raises PermissionError (EACCES) on contention.
                # Re-raise as BlockingIOError to match the exception
                # POSIX fcntl gives when LOCK_NB finds the lock held.
                if e.errno in (_errno.EACCES, _errno.EAGAIN):
                    raise BlockingIOError(e.errno, str(e)) from None
                raise
            return

        # Blocking acquire. LK_LOCK retries for ~10s internally; loop
        # forever in case the holder is slow to release.
        while True:
            try:
                _locking(fd, _msvcrt.LK_LOCK)
                return
            except OSError as e:
                if e.errno not in (_errno.EACCES, _errno.EAGAIN, _errno.EDEADLK):
                    raise
                _time.sleep(_RETRY_INTERVAL)


__all__ = ["LOCK_EX", "LOCK_NB", "LOCK_UN", "flock"]

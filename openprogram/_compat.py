"""Cross-platform shim for the subset of :mod:`fcntl` used across the
codebase: ``flock(fd, mode)`` plus the ``LOCK_EX`` / ``LOCK_UN`` /
``LOCK_NB`` constants.

On POSIX this is a thin re-export of :mod:`fcntl`. On Windows the
``fcntl`` module does not exist — we emulate advisory exclusive
locking on a single byte at offset 0 of the file using
:func:`msvcrt.locking`, and translate ``PermissionError`` (raised by
msvcrt when ``LK_NBLCK`` finds the byte already held) into
:class:`BlockingIOError` so call sites can keep the POSIX exception
pattern.

Usage — replace ``import fcntl`` with::

    from openprogram import _compat as fcntl

Everything downstream stays the same: ``fcntl.flock(fd, fcntl.LOCK_EX
| fcntl.LOCK_NB)`` etc.

Notes on Windows semantics:

* The lock is on byte 0 of the file; we ``lseek`` to 0 before each
  call so a subsequent ``seek``/``write`` to the same fd is
  unaffected (all current callers either don't write or seek
  explicitly after acquiring).
* A blocking acquire (``LOCK_EX`` without ``LOCK_NB``) busy-waits at
  100 ms intervals because ``msvcrt.LK_LOCK`` only retries for ~10s
  before giving up.
* The lock is per-process-per-fd. Re-acquiring the same byte on the
  same fd is an error on Windows, matching POSIX exclusive
  semantics — no current call site relies on re-entrant locking.
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

    # Bit values picked to be distinct; only ever consumed by our own
    # `flock()` below, so the exact numbers don't matter as long as
    # they don't collide.
    LOCK_EX = 0x2
    LOCK_NB = 0x4
    LOCK_UN = 0x8

    # Lock a single byte at offset 0 of the file. msvcrt.locking takes
    # bytes-from-current-position, so we always lseek(0) first.
    _LOCK_NBYTES = 1
    _RETRY_INTERVAL = 0.1

    def _seek_zero(fd: int) -> None:
        try:
            _os.lseek(fd, 0, _os.SEEK_SET)
        except OSError:
            # Some pseudo-files (rare for our lock files) don't seek;
            # locking will still operate at the current position.
            pass

    def flock(fd: int, mode: int) -> None:
        if mode & LOCK_UN:
            _seek_zero(fd)
            try:
                _msvcrt.locking(fd, _msvcrt.LK_UNLCK, _LOCK_NBYTES)
            except OSError:
                # Match POSIX: releasing a lock we don't hold is a
                # silent no-op for our callers.
                pass
            return

        if mode & LOCK_NB:
            _seek_zero(fd)
            try:
                _msvcrt.locking(fd, _msvcrt.LK_NBLCK, _LOCK_NBYTES)
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
            _seek_zero(fd)
            try:
                _msvcrt.locking(fd, _msvcrt.LK_LOCK, _LOCK_NBYTES)
                return
            except OSError as e:
                if e.errno not in (_errno.EACCES, _errno.EAGAIN, _errno.EDEADLK):
                    raise
                _time.sleep(_RETRY_INTERVAL)


__all__ = ["LOCK_EX", "LOCK_NB", "LOCK_UN", "flock"]

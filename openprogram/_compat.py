"""Cross-platform shims for OS APIs that differ between POSIX and Windows.

Two surfaces:

1. ``fcntl`` subset — ``flock`` + ``LOCK_EX`` / ``LOCK_UN`` / ``LOCK_NB``.
   On POSIX this is a thin re-export of :mod:`fcntl`. On Windows the
   module doesn't exist, so we emulate single-byte advisory locking
   via :func:`msvcrt.locking` and translate ``PermissionError`` (raised
   on contention) into :class:`BlockingIOError` so call sites can keep
   the POSIX exception pattern.

2. ``kill_process_tree(pid)`` — force-kill a process and every child it
   spawned. POSIX uses ``os.killpg(getpgid(pid), SIGKILL)`` (requires
   the target was launched with ``start_new_session=True`` so it owns
   its own pgid). Windows uses ``taskkill /F /T /PID <pid>``; ``/T``
   kills the tree, ``/F`` forces it. Both branches swallow
   already-dead errors. ``signal.SIGKILL`` doesn't exist on Windows
   Python, so the helper exists precisely so callers don't need
   per-platform branches.

Usage — replace ``import fcntl`` with::

    from openprogram import _compat as fcntl

Everything downstream stays the same: ``fcntl.flock(fd, fcntl.LOCK_EX
| fcntl.LOCK_NB)`` etc.

Notes on Windows ``flock`` semantics:

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

import os as _os
import signal as _signal
import subprocess as _subprocess
import sys as _sys


def kill_process_tree(pid: int) -> bool:
    """Force-kill ``pid`` and every descendant. Best-effort, non-raising.

    POSIX path requires the target was started with
    ``start_new_session=True`` (i.e. it leads its own process group).
    If it doesn't, we fall back to a single-process ``SIGKILL``.

    Returns True if at least one ``kill`` syscall succeeded, False if
    the process was already gone (or no permission to signal it).
    """
    if _sys.platform == "win32":
        try:
            res = _subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
            )
            return res.returncode == 0
        except (FileNotFoundError, _subprocess.TimeoutExpired, OSError):
            # taskkill missing (extremely old Windows / locked-down env)
            # — fall through to bare TerminateProcess via os.kill.
            pass
        try:
            _os.kill(pid, _signal.SIGTERM)  # maps to TerminateProcess
            return True
        except (ProcessLookupError, OSError):
            return False

    # POSIX
    try:
        pgid = _os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return False
    try:
        _os.killpg(pgid, _signal.SIGKILL)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # pgid mismatch (target wasn't a session leader) — fall back to
        # single-process kill so callers that forgot start_new_session
        # still get the process gone.
        try:
            _os.kill(pid, _signal.SIGKILL)
            return True
        except (ProcessLookupError, OSError):
            return False

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


def node_tool_cmd(argv: list[str]) -> list[str]:
    """Make a node-ecosystem command (``npm`` / ``npx`` / ``agent-browser``
    / any project ``.cmd`` bin) runnable via ``subprocess(..., shell=False)``
    on every OS.

    The Windows problem: npm/npx/etc. ship as ``*.cmd`` batch shims, and
    ``CreateProcess`` (what ``subprocess`` uses when ``shell=False``)
    cannot execute a ``.cmd``/``.bat`` even by absolute path — it raises
    ``OSError [WinError 193] "%1 is not a valid Win32 application"``.
    Resolving the bare name with ``shutil.which`` alone is NOT enough.

    So on Windows we resolve ``argv[0]`` and, if it's a ``.cmd``/``.bat``,
    route it through ``cmd.exe /c``; a real ``.exe`` (e.g. ``node``) is
    passed through untouched. On POSIX the argv is returned unchanged
    (with a best-effort ``shutil.which`` resolve of ``argv[0]``), so the
    behaviour on macOS/Linux is identical to ``subprocess`` with the bare
    name.

    Pass the result straight to ``subprocess.run/Popen(..., shell=False)``.
    """
    import shutil
    if not argv:
        return argv
    exe, rest = argv[0], list(argv[1:])
    resolved = shutil.which(exe) or exe
    if _sys.platform != "win32":
        return [resolved, *rest]
    if resolved.lower().endswith((".cmd", ".bat")):
        comspec = _os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c", resolved, *rest]
    return [resolved, *rest]


def restrict_to_user(path) -> None:
    """Lock a credential file down to the current user only — cross-platform.

    POSIX: ``chmod 0o600`` (owner read/write, nothing for group/other) —
    identical to the bare ``os.chmod(path, 0o600)`` call sites this
    replaces.

    Windows: the POSIX mode bits are meaningless to NTFS — ``os.chmod``
    there only toggles the read-only attribute, so a ``0o600`` is a
    near-no-op and the file keeps whatever ACL it inherited. Files under
    ``%USERPROFILE%`` already inherit a user-private ACL (so the practical
    exposure is small), but for defence-in-depth we additionally strip
    inheritance and grant full control to only the current user + SYSTEM
    via ``icacls``. SYSTEM is kept so backup / AV / indexing still work.

    Best-effort throughout: any failure (no ``icacls``, odd account name,
    timeout) leaves the inherited — already user-scoped — ACL in place,
    which is safe. Never raises.
    """
    p = _os.fspath(path)
    try:
        _os.chmod(p, 0o600)
    except OSError:
        pass
    if _sys.platform != "win32":
        return
    user = _os.environ.get("USERNAME") or _os.environ.get("USER")
    if not user:
        return
    try:
        _subprocess.run(
            ["icacls", p, "/inheritance:r",
             "/grant:r", f"{user}:F", "/grant:r", "SYSTEM:F"],
            stdout=_subprocess.DEVNULL,
            stderr=_subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, ValueError, _subprocess.SubprocessError):
        pass


_PROMPT_TOOLKIT_USABLE_CACHE: bool | None = None


def prompt_toolkit_usable() -> bool:
    """Return True if ``prompt_toolkit`` (and therefore ``questionary``,
    ``inquirer``, …) can render a full-screen interactive prompt in the
    current terminal.

    On POSIX with a tty, or Windows with a native ``cmd.exe`` /
    Windows Terminal / PowerShell console, returns True.

    On Git Bash (MinTTY), Cygwin without a winpty wrapper, redirected
    stdio, IDEs that pipe through pseudo-ttys, or any other case where
    prompt_toolkit's ``create_output()`` fails — returns False, so
    callers can fall back to plain ``input()``-driven menus.

    The probe is destructive-free (it creates and immediately drops
    the output backend) but does a small amount of work, so the
    result is cached for the lifetime of the process.
    """
    global _PROMPT_TOOLKIT_USABLE_CACHE
    if _PROMPT_TOOLKIT_USABLE_CACHE is not None:
        return _PROMPT_TOOLKIT_USABLE_CACHE
    try:
        from prompt_toolkit.output.defaults import create_output
    except ImportError:
        _PROMPT_TOOLKIT_USABLE_CACHE = False
        return False
    try:
        # create_output() raises NoConsoleScreenBufferError on Windows
        # MinTTY and friends; any other terminal-detection issue also
        # surfaces here.
        create_output()
        _PROMPT_TOOLKIT_USABLE_CACHE = True
    except Exception:  # noqa: BLE001 — any failure is "don't use it"
        _PROMPT_TOOLKIT_USABLE_CACHE = False
    return _PROMPT_TOOLKIT_USABLE_CACHE


__all__ = [
    "LOCK_EX",
    "LOCK_NB",
    "LOCK_UN",
    "flock",
    "kill_process_tree",
    "node_tool_cmd",
    "prompt_toolkit_usable",
    "restrict_to_user",
]

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
import os
import errno
import functools as _functools
import signal as _signal
import subprocess as _subprocess
import sys as _sys


from ._platform.asyncio import (
    install_asyncio_exception_handler,
)
from ._platform.filesystem import (
    filesystem_path,
    restrict_descriptor_to_user,
    restrict_to_user,
    restrict_directory_to_user,
    is_link_metadata,
    remove_tree,
    user_private_metadata,
    open_regular_binary,
    read_user_state_bytes,
    directory_handle,
    directory_child,
    directory_close,
    directory_duplicate,
    directory_read_file,
)
from ._platform.process_ownership import (
    no_window_creation_flags,
    process_tree_popen_kwargs,
    ProcessTreeOwner,
    _windows_job_api,
    _windows_job_active_processes,
    _windows_set_job_kill_on_close,
    _windows_create_kill_on_close_job,
    _windows_assign_process_to_job,
    _windows_resume_process,
    _windows_close_handle,
    _windows_terminate_and_close_job,
    _windows_release_job,
)
from ._platform.environment import (
    can_open_browser,
    open_browser_url,
    tui_child_requires_direct_stdio_inheritance,
    tui_worker_ready_timeout_seconds,
    _windows_default_wsl2_distribution,
    _windows_wsl_executable,
    windows_wsl_exec_prefix,
    windows_wsl_sandbox_reason,
    windows_path_to_wsl,
    managed_release_target,
    release_installer_command,
    release_installer_fallback_command,
    platform_environment_advisories,
    _linux_systemd_user_reason,
    conversational_update_backend,
    worker_service_backend,
)
from ._platform.processes import (
    kill_process_tree,
    process_command_line,
    pids_on_port,
    _linux_listening_socket_inodes,
    _linux_proc_pids_on_port,
    process_ids_by_name,
    kill_processes_matching,
    _posix_process_command_lines,
    process_start_token,
    process_alive,
)
from ._platform.terminal import (
    executable_cmd,
    node_tool_cmd,
    _windows_powershell,
    desktop_bundle_metadata,
    _utf8_shell_environment,
    powershell_invocation,
    git_bash_invocation,
    interactive_pty_available,
    InteractivePty,
    prompt_toolkit_usable,
)


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


# ---------------------------------------------------------------------------
# InteractivePty — cross-platform driver for interactive child CLIs
# ---------------------------------------------------------------------------
#
# Some children only behave interactively under a real terminal: they
# line-buffer output (so a prompt / URL arrives promptly) and read typed input
# from a tty. The claude-code account login is the canonical case — Meridian
# shells out to ``claude auth login``, which prints an OAuth URL then waits for
# a pasted code.
#
# POSIX has stdlib ``pty``. Windows has neither ``pty`` nor a way to
# ``select()`` on a console handle, so we wrap the ConPTY binding ``pywinpty``
# (import name ``winpty``) and pump its blocking reads through a background
# thread + queue. ``interactive_pty_available()`` reports whether this host can
# drive one at all, so callers can fall back (e.g. to a token paste) when it
# can't.


_PROMPT_TOOLKIT_USABLE_CACHE: bool | None = None


_DIRECTORY_FD_SUPPORTED = os.open in os.supports_dir_fd and os.scandir in os.supports_fd


__all__ = [
    "remove_tree",
    "restrict_descriptor_to_user",
    "directory_handle",
    "directory_child",
    "directory_close",
    "directory_duplicate",
    "directory_read_file",
    "is_link_metadata",
    "process_alive",
    "process_start_token",
    "read_user_state_bytes",
    "open_regular_binary",
    "user_private_metadata",
    "LOCK_EX",
    "LOCK_NB",
    "LOCK_UN",
    "InteractivePty",
    "ProcessTreeOwner",
    "executable_cmd",
    "filesystem_path",
    "flock",
    "install_asyncio_exception_handler",
    "interactive_pty_available",
    "can_open_browser",
    "conversational_update_backend",
    "kill_processes_matching",
    "kill_process_tree",
    "managed_release_target",
    "node_tool_cmd",
    "no_window_creation_flags",
    "open_browser_url",
    "platform_environment_advisories",
    "pids_on_port",
    "process_command_line",
    "process_ids_by_name",
    "process_tree_popen_kwargs",
    "prompt_toolkit_usable",
    "restrict_directory_to_user",
    "restrict_to_user",
    "release_installer_command",
    "release_installer_fallback_command",
    "tui_child_requires_direct_stdio_inheritance",
    "tui_worker_ready_timeout_seconds",
    "windows_path_to_wsl",
    "windows_wsl_exec_prefix",
    "windows_wsl_sandbox_reason",
    "worker_service_backend",
]

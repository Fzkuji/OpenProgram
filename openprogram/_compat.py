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


def install_asyncio_exception_handler(loop) -> None:
    """Install the small set of platform-specific asyncio workarounds.

    On Windows, CPython's proactor transport can report a peer reset from
    ``_call_connection_lost`` *after* the WebSocket has already completed its
    normal disconnect path.  The callback runs outside application code, so
    catching ``WebSocketDisconnect`` cannot prevent the noisy ``WinError
    10054`` traceback.  Suppress only that exact transport-teardown callback;
    every other loop exception keeps the caller's existing/default handling.
    """

    previous = loop.get_exception_handler()

    def _handler(active_loop, context) -> None:
        exception = context.get("exception")
        handle = context.get("handle")
        callback = getattr(handle, "_callback", None)
        owner = getattr(callback, "__self__", None)
        callback_name = getattr(callback, "__name__", "")
        owner_module = getattr(type(owner), "__module__", "")
        winerror = getattr(exception, "winerror", None)
        benign_windows_disconnect = (
            _sys.platform == "win32"
            and isinstance(exception, ConnectionResetError)
            and winerror == 10054
            and callback_name == "_call_connection_lost"
            and owner_module == "asyncio.proactor_events"
        )
        if benign_windows_disconnect:
            return
        if previous is not None:
            previous(active_loop, context)
        else:
            active_loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)


def filesystem_path(path) -> str:
    """Return a Win32-safe spelling for a local filesystem path.

    Python still reaches legacy ``MAX_PATH`` APIs when Windows long-path
    policy is disabled.  The extended-length prefix bypasses that process-
    external registry dependency without changing the path on POSIX.  Keep
    this conversion at the compatibility seam so product modules do not grow
    scattered ``\\\\?\\`` branches.
    """

    value = _os.fspath(path)
    if _sys.platform != "win32" or not isinstance(value, str):
        return value
    if value.startswith("\\\\?\\"):
        return value
    absolute = _os.path.abspath(value)
    if len(absolute) < 248:
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def no_window_creation_flags() -> int:
    """Flags for background subprocesses that must not flash a console."""

    if _sys.platform != "win32":
        return 0
    return int(getattr(_subprocess, "CREATE_NO_WINDOW", 0))


def process_tree_popen_kwargs() -> dict[str, object]:
    """Creation options for a child that may need whole-tree termination.

    POSIX tree termination is only safe when the child leads its own session;
    otherwise a shell inherits the caller's process group and ``killpg`` could
    terminate OpenProgram itself.  Windows ``taskkill /T`` discovers descendants
    by PID, while ``CREATE_NEW_PROCESS_GROUP`` keeps console control events from
    leaking between the child command and the interactive parent.
    """

    if _sys.platform == "win32":
        flags = no_window_creation_flags()
        flags |= int(getattr(_subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        return {"creationflags": flags}
    return {"start_new_session": True}


class ProcessTreeOwner:
    """Own one subprocess tree independently of its original leader.

    A process PID is not a durable tree handle.  A shell can start a
    background child and exit while that child still owns one of our capture
    pipes; by the time ``communicate()`` times out, ``taskkill /T /PID`` (and
    ``getpgid(pid)`` on POSIX) can no longer find the descendants through the
    dead shell.

    POSIX solves this by creating a session and retaining its process-group
    id.  Windows needs a kernel Job Object.  The process is created suspended,
    assigned to a kill-on-close job, and only then resumed, so there is no
    startup race in which it can spawn an unowned descendant.

    Call :meth:`release` after normal completion to let deliberately detached
    descendants keep running.  Call :meth:`terminate` on timeout or failure to
    force-kill everything still owned by the tree.
    """

    def __init__(self) -> None:
        self._pgid: int | None = None
        self._job_handle: int | None = None
        self._started = False
        self._finished = False

    def __del__(self) -> None:
        # Ownership is intentionally fail-closed: an exception between spawn
        # and the caller's explicit release must not strand a child tree.  At
        # interpreter shutdown module globals may already be cleared, hence
        # the broad guard in this last-resort path only.
        try:
            self.terminate()
        except BaseException:
            pass

    def popen(self, *args, **kwargs) -> _subprocess.Popen:
        """Start and take ownership of one process tree."""

        if self._started:
            raise RuntimeError("a ProcessTreeOwner can only start one process")
        self._started = True
        if _sys.platform == "win32":
            return self._popen_windows(*args, **kwargs)

        if "start_new_session" in kwargs:
            raise TypeError(
                "ProcessTreeOwner controls the start_new_session option"
            )
        proc = _subprocess.Popen(*args, start_new_session=True, **kwargs)
        # start_new_session makes the initial PID the new PGID.  Retain that
        # value instead of asking getpgid(pid) during cleanup: the leader may
        # already be a reaped shell while background members still exist.
        self._pgid = proc.pid
        return proc

    def _popen_windows(self, *args, **kwargs) -> _subprocess.Popen:
        if "creationflags" in kwargs:
            creationflags = int(kwargs.pop("creationflags"))
        else:
            creationflags = 0
        creationflags |= no_window_creation_flags()
        creationflags |= int(
            getattr(_subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        creationflags |= int(getattr(_subprocess, "CREATE_SUSPENDED", 0x00000004))

        job_handle = _windows_create_kill_on_close_job()
        proc: _subprocess.Popen | None = None
        assigned = False
        try:
            proc = _subprocess.Popen(
                *args,
                creationflags=creationflags,
                **kwargs,
            )
            _windows_assign_process_to_job(job_handle, proc)
            assigned = True
            _windows_resume_process(proc.pid)
        except BaseException:
            # A CREATE_SUSPENDED process has not had an opportunity to spawn
            # children.  If assignment succeeded, closing the kill-on-close
            # job is the most reliable cleanup; otherwise terminate the one
            # suspended process directly.
            if assigned:
                _windows_terminate_and_close_job(job_handle)
            else:
                _windows_close_handle(job_handle)
                if proc is not None:
                    kill = getattr(proc, "kill", None)
                    try:
                        if kill is not None:
                            kill()
                    except OSError:
                        pass
            if proc is not None:
                wait = getattr(proc, "wait", None)
                try:
                    if wait is not None:
                        wait(timeout=2)
                except (OSError, _subprocess.TimeoutExpired):
                    pass
                for name in ("stdin", "stdout", "stderr"):
                    stream = getattr(proc, name, None)
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass
            raise

        self._job_handle = job_handle
        return proc

    def terminate(self) -> bool:
        """Force-kill the owned tree.  Best-effort and idempotent."""

        if self._finished:
            return False
        self._finished = True
        if _sys.platform == "win32":
            job_handle, self._job_handle = self._job_handle, None
            if job_handle is None:
                return False
            return _windows_terminate_and_close_job(job_handle)

        pgid, self._pgid = self._pgid, None
        if pgid is None:
            return False
        try:
            _os.killpg(pgid, _signal.SIGKILL)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False

    def release(self) -> None:
        """Release a normally completed tree without killing descendants."""

        if self._finished:
            return
        self._finished = True
        if _sys.platform == "win32":
            job_handle, self._job_handle = self._job_handle, None
            if job_handle is not None:
                _windows_release_job(job_handle)
        else:
            self._pgid = None


@_functools.cache
def _windows_job_api():
    """Return lazily configured kernel32 Job/Thread API bindings."""

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
    ]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ThreadEntry32),
    ]
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ThreadEntry32),
    ]
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    return kernel32, ExtendedLimitInformation, ThreadEntry32


def _windows_set_job_kill_on_close(job_handle: int, enabled: bool) -> None:
    import ctypes

    kernel32, info_type, _thread_type = _windows_job_api()
    info = info_type()
    if enabled:
        info.BasicLimitInformation.LimitFlags = 0x00002000
    if not kernel32.SetInformationJobObject(
        job_handle,
        9,  # JobObjectExtendedLimitInformation
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def _windows_create_kill_on_close_job() -> int:
    import ctypes

    kernel32, _info_type, _thread_type = _windows_job_api()
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    value = int(handle)
    try:
        _windows_set_job_kill_on_close(value, True)
    except BaseException:
        _windows_close_handle(value)
        raise
    return value


def _windows_assign_process_to_job(
    job_handle: int,
    proc: _subprocess.Popen,
) -> None:
    import ctypes

    kernel32, _info_type, _thread_type = _windows_job_api()
    process_handle = int(getattr(proc, "_handle"))
    if not kernel32.AssignProcessToJobObject(job_handle, process_handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _windows_resume_process(pid: int) -> None:
    """Resume the primary thread of a CREATE_SUSPENDED process."""

    import ctypes

    kernel32, _info_type, thread_type = _windows_job_api()
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if not snapshot or int(snapshot) == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    resumed = False
    try:
        entry = thread_type()
        entry.dwSize = ctypes.sizeof(entry)
        present = bool(kernel32.Thread32First(snapshot, ctypes.byref(entry)))
        while present:
            if int(entry.th32OwnerProcessID) == int(pid):
                thread = kernel32.OpenThread(0x0002, False, entry.th32ThreadID)
                if thread:
                    try:
                        previous = kernel32.ResumeThread(thread)
                        # A zero return means the thread was already running;
                        # do not mistake an injected/helper thread for the
                        # CREATE_SUSPENDED primary thread.  Walk the complete
                        # snapshot and undo one suspension on every suspended
                        # thread owned by the new process.
                        if previous not in (0, 0xFFFFFFFF):
                            resumed = True
                    finally:
                        kernel32.CloseHandle(thread)
            present = bool(kernel32.Thread32Next(snapshot, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot)
    if not resumed:
        raise ctypes.WinError(ctypes.get_last_error())


def _windows_close_handle(handle: int) -> bool:
    kernel32, _info_type, _thread_type = _windows_job_api()
    return bool(kernel32.CloseHandle(handle))


def _windows_terminate_and_close_job(job_handle: int) -> bool:
    kernel32, _info_type, _thread_type = _windows_job_api()
    terminated = bool(kernel32.TerminateJobObject(job_handle, 1))
    closed = _windows_close_handle(job_handle)
    return terminated or closed


def _windows_release_job(job_handle: int) -> None:
    """Destroy a job without applying its kill-on-close limit."""

    try:
        _windows_set_job_kill_on_close(job_handle, False)
    except OSError:
        # SetInformationJobObject should be infallible for our own handle.  If
        # the OS nevertheless rejects it, retaining the handle is safer than
        # unexpectedly killing a deliberately detached background command.
        return
    _windows_close_handle(job_handle)


def can_open_browser() -> bool:
    """Whether an automatic browser launch is meaningful on this host.

    Linux servers and SSH sessions commonly have ``xdg-open`` installed but
    no graphical session.  Calling :mod:`webbrowser` there either produces a
    misleading success or starts a helper that cannot display anything.  WSLg
    and desktop Linux expose DISPLAY/WAYLAND_DISPLAY and remain supported.
    """

    if not _sys.platform.startswith("linux"):
        return True
    return bool(
        _os.environ.get("DISPLAY") or _os.environ.get("WAYLAND_DISPLAY")
    )


def open_browser_url(url: str, *, new: int = 2) -> bool:
    """Best-effort open ``url`` without launching dead helpers headlessly."""

    if not can_open_browser():
        return False
    try:
        import webbrowser

        return bool(webbrowser.open(url, new=new))
    except Exception:
        return False


def tui_child_requires_direct_stdio_inheritance() -> bool:
    """Whether an interactive Node TUI must inherit the console directly.

    Windows exposes console streams as native handles behind CRT file
    descriptors.  Passing descriptors saved with :func:`os.dup` through
    ``subprocess.Popen(stdout=..., stderr=...)`` is not stable across the
    detached worker bootstrap and can fail with ``WinError 6``.  Direct
    inheritance also preserves Node's ``isTTY``/raw-mode detection.

    POSIX descriptors remain safe to duplicate, which lets the Python startup
    phase write to a log before the Ink child takes over the real terminal.
    Keep this platform distinction in the compatibility seam rather than in
    the CLI launcher.
    """

    return _sys.platform == "win32"


def tui_worker_ready_timeout_seconds() -> float:
    """Upper bound for the TUI's detached-worker cold start.

    A complete Windows runtime can spend over a minute in Defender's first
    scan. POSIX has no equivalent reason to leave an apparently blank terminal
    waiting for two minutes after a worker startup failure.
    """

    return 120.0 if _sys.platform == "win32" else 30.0


@_functools.cache
def _windows_default_wsl2_distribution() -> tuple[str | None, str | None]:
    """Return the default WSL2 distribution and an actionable failure.

    Reading the per-user WSL registry avoids launching ``wsl.exe`` on hosts
    where it is installed as a Windows feature but has no distribution.  On
    those machines the launcher can wait for interactive setup indefinitely.
    """

    if _sys.platform != "win32":
        return None, "WSL2 delegation is only available on Windows"
    try:
        import winreg

        root_path = r"Software\Microsoft\Windows\CurrentVersion\Lxss"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root_path) as root:
            default_id, _ = winreg.QueryValueEx(root, "DefaultDistribution")
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            root_path + "\\" + str(default_id),
        ) as distro:
            name, _ = winreg.QueryValueEx(distro, "DistributionName")
            version, _ = winreg.QueryValueEx(distro, "Version")
    except (OSError, ValueError, TypeError):
        return None, (
            "Windows sandbox needs a default WSL2 distribution with "
            "bubblewrap installed"
        )
    if int(version) != 2:
        return None, f"default WSL distribution {name!r} is not WSL2"
    if not str(name).strip():
        return None, "default WSL2 distribution has no name"
    return str(name), None


def _windows_wsl_executable() -> str | None:
    import shutil

    return shutil.which("wsl.exe") if _sys.platform == "win32" else None


def windows_wsl_exec_prefix() -> list[str]:
    """Return a stable argv prefix for the default WSL2 distribution."""

    executable = _windows_wsl_executable()
    distribution, reason = _windows_default_wsl2_distribution()
    if executable is None:
        raise RuntimeError("Windows sandbox needs wsl.exe")
    if reason is not None or distribution is None:
        raise RuntimeError(reason or "Windows sandbox needs a default WSL2 distribution")
    return [executable, "--distribution", distribution, "--exec"]


@_functools.cache
def windows_wsl_sandbox_reason() -> str | None:
    """Why WSL2+bubblewrap cannot enforce the Windows sandbox, if any."""

    try:
        prefix = windows_wsl_exec_prefix()
    except RuntimeError as exc:
        return str(exc)
    probe = (
        "command -v bwrap >/dev/null 2>&1 || exit 21; "
        "command -v bash >/dev/null 2>&1 || exit 22; "
        "exec bwrap --new-session --die-with-parent --unshare-pid "
        "--unshare-ipc --unshare-uts --unshare-net --cap-drop ALL "
        "--ro-bind / / --proc /proc --dev /dev -- /bin/true"
    )
    flags = getattr(_subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = _subprocess.run(
            [*prefix, "sh", "-c", probe],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=flags,
        )
    except _subprocess.TimeoutExpired:
        return "Windows WSL2 sandbox probe timed out"
    except OSError as exc:
        return f"Windows WSL2 sandbox probe failed: {exc}"
    if result.returncode == 0:
        return None
    if result.returncode == 21:
        return "Windows sandbox needs bubblewrap in the default WSL2 distribution"
    if result.returncode == 22:
        return "Windows sandbox needs /bin/bash in the default WSL2 distribution"
    lines = (result.stderr or result.stdout).strip().splitlines()
    detail = f": {lines[-1]}" if lines else ""
    return "WSL2 bubblewrap cannot create the required namespaces" + detail


@_functools.lru_cache(maxsize=512)
def windows_path_to_wsl(path: str) -> str:
    """Translate one absolute Windows path through the selected WSL distro."""

    absolute = _os.path.abspath(_os.fspath(path))
    prefix = windows_wsl_exec_prefix()
    flags = getattr(_subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = _subprocess.run(
            [*prefix, "wslpath", "-a", "-u", absolute],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=flags,
        )
    except (_subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(f"could not translate Windows path for WSL2: {exc}") from exc
    translated = result.stdout.strip()
    if result.returncode != 0 or not translated.startswith("/"):
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            f"could not translate Windows path for WSL2: {absolute}"
            + (f" ({detail})" if detail else "")
        )
    return translated


def managed_release_target(
    system: str | None = None,
    machine: str | None = None,
) -> tuple[str, str, str, str] | None:
    """Return the formal runtime platform, arch, suffix and installer.

    This is the single platform seam used by the managed updater. Archive
    format is part of the target contract: POSIX runtimes use ``tar.gz`` and
    Windows runtimes use a ZIP that needs no Unix compatibility layer.
    """

    import platform as _platform

    system = system or _platform.system()
    machine = (machine or _platform.machine()).lower()
    platform_name = {
        "Darwin": "macos",
        "Linux": "linux",
        "Windows": "windows",
    }.get(system)
    arch = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "x86_64",
        "amd64": "x86_64",
    }.get(machine)
    if platform_name is None or arch is None:
        return None
    if platform_name == "windows":
        return platform_name, arch, ".zip", "install-release.ps1"
    return platform_name, arch, ".tar.gz", "install-release.sh"


def release_installer_command(path) -> list[str]:
    """Build the native command for one downloaded release installer."""

    import shutil

    value = _os.fspath(path)
    if value.lower().endswith(".ps1"):
        executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if executable is None:
            raise OSError("PowerShell is required to run the release installer")
        return [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            value,
        ]
    return ["sh", value]


def release_installer_fallback_command(path) -> list[str] | None:
    """Return a Git-for-Windows fallback for a POSIX installer, if present.

    Formal Windows releases use PowerShell. This fallback only preserves the
    ability to exercise or recover an older POSIX-tagged managed installer on
    a Windows host where ``sh`` is not on PATH.
    """

    import shutil

    value = _os.fspath(path)
    if _sys.platform != "win32" or not value.lower().endswith(".sh"):
        return None
    git = shutil.which("git.exe")
    if not git:
        return None
    candidate = _os.path.abspath(
        _os.path.join(_os.path.dirname(git), "..", "bin", "sh.exe")
    )
    return [candidate, value] if _os.path.isfile(candidate) else None


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
                capture_output=True,
                timeout=10,
                creationflags=no_window_creation_flags(),
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

    # POSIX.  Only signal a process group when the target actually leads a
    # group that is different from ours.  ``killpg(getpgid(pid), ...)`` is
    # unsafe as a generic fallback: an ordinary child inherits its caller's
    # process group, so that spelling would kill the caller (and potentially
    # its terminal) along with the child.  Callers that need tree semantics
    # launch the target with ``start_new_session=True``; every other target
    # gets the safe single-process fallback.
    try:
        pgid = _os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return False
    try:
        own_pgid = _os.getpgrp()
    except (AttributeError, OSError):
        own_pgid = None
    try:
        if pgid == pid and pgid != own_pgid:
            _os.killpg(pgid, _signal.SIGKILL)
        else:
            _os.kill(pid, _signal.SIGKILL)
        return True
    except (ProcessLookupError, PermissionError, OSError):
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


def executable_cmd(argv: list[str]) -> list[str]:
    """Return an argv that ``subprocess(..., shell=False)`` can execute.

    Windows ``CreateProcess`` cannot directly launch ``.cmd``/``.bat`` files
    or scripts whose interpreter is declared by a POSIX shebang. Route batch
    files through ``cmd.exe`` and resolve a shebang interpreter without ever
    enabling shell interpolation. This supports credential helpers and other
    user-configured subprocesses while preserving their argv boundaries.

    A Git-for-Windows shell is discovered next to ``git.exe`` only when a
    helper explicitly asks for ``sh``/``bash``. It is not a runtime
    dependency; an absent interpreter remains a clear launch failure.
    """
    import shlex
    import shutil
    from pathlib import Path

    if not argv:
        return argv
    exe, rest = argv[0], list(argv[1:])
    resolved = shutil.which(exe) or exe
    if _sys.platform != "win32":
        return [resolved, *rest]
    if resolved.lower().endswith((".cmd", ".bat")):
        comspec = _os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c", resolved, *rest]
    if resolved.lower().endswith(".ps1"):
        powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if powershell:
            return [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                resolved,
                *rest,
            ]

    try:
        with Path(resolved).open("rb") as script:
            first_line = script.readline(4096)
    except OSError:
        first_line = b""
    if first_line.startswith(b"#!"):
        declaration = first_line[2:].decode("utf-8", errors="replace").strip()
        words = shlex.split(declaration, posix=True)
        if words:
            interpreter, interpreter_args = words[0], words[1:]
            if interpreter.replace("\\", "/").rsplit("/", 1)[-1] == "env":
                if interpreter_args[:1] == ["-S"]:
                    interpreter_args = shlex.split(" ".join(interpreter_args[1:]))
                if interpreter_args:
                    interpreter, interpreter_args = (
                        interpreter_args[0], interpreter_args[1:]
                    )
            interpreter_name = interpreter.replace("\\", "/").rsplit("/", 1)[-1]
            found = shutil.which(interpreter) or shutil.which(interpreter_name)
            if found is None and interpreter_name in {"python", "python3"}:
                found = _sys.executable
            if found is None and interpreter_name in {"sh", "bash"}:
                git = shutil.which("git.exe")
                if git:
                    candidate = Path(git).parent.parent / "bin" / f"{interpreter_name}.exe"
                    if candidate.is_file():
                        found = str(candidate)
            if found:
                return [found, *interpreter_args, resolved, *rest]
    return [resolved, *rest]


def node_tool_cmd(argv: list[str]) -> list[str]:
    """Compatibility alias for existing Node-ecosystem call sites."""
    return executable_cmd(argv)


def _windows_powershell(script: str, *, timeout: float = 5.0) -> str:
    """Run one read-only Windows CIM query and return UTF-8 output.

    Kept in the compatibility seam because modern Windows no longer ships
    WMIC and product modules must not grow their own platform subprocesses.
    """

    import shutil

    executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if executable is None:
        return ""
    prefix = (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();"
    )
    flags = getattr(_subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = _subprocess.run(
            [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                prefix + script,
            ],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=flags,
        )
    except (OSError, _subprocess.TimeoutExpired):
        return ""
    return result.stdout if result.returncode == 0 else ""


def desktop_bundle_metadata(app_path):
    """Read the installed shell version and resources without launching it.

    Select the actual bundle layout, so release tooling can inspect macOS
    fixtures on any host. Windows PE metadata requires native PowerShell.
    """
    import plistlib
    from pathlib import Path

    app = Path(app_path)
    if app.name == "OpenProgram.exe" and app.is_file():
        app = app.parent
    plist = app / "Contents" / "Info.plist"
    if plist.is_file():
        with plist.open("rb") as stream:
            version = plistlib.load(stream).get("CFBundleShortVersionString")
        return app / "Contents" / "Resources", version

    executable = app / "OpenProgram.exe"
    resources = app / "resources"
    if not executable.is_file() or not (resources / "app.asar").is_file():
        raise ValueError("installed Desktop bundle layout is unavailable")
    # A single-quoted PowerShell literal treats all path characters as data;
    # doubling apostrophes also handles installation folders such as O'Brien.
    literal = str(executable.resolve()).replace("'", "''")
    version = _windows_powershell(
        f"[Diagnostics.FileVersionInfo]::GetVersionInfo('{literal}').ProductVersion",
        timeout=15,
    ).strip()
    if not version:
        raise ValueError("installed Windows EXE product version is unavailable")
    # Electron's Windows resource can express the three-part release version
    # with a zero fourth component. Do not discard a nonzero revision.
    parts = version.split(".")
    if len(parts) == 4 and parts[-1] == "0" and all(part.isdecimal() for part in parts):
        version = ".".join(parts[:3])
    return resources, version


def _utf8_shell_environment(env=None):
    child_env = dict(os.environ if env is None else env)
    child_env.setdefault("PYTHONUTF8", "1")
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    return child_env


def powershell_invocation(executable: str, command: str, env=None):
    """Keep PowerShell source/Unicode independent of native argv quoting."""
    import base64

    source = (
        '$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); '
        + command
    )
    encoded = base64.b64encode(source.encode("utf-16-le")).decode("ascii")
    return [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], _utf8_shell_environment(env)


def git_bash_invocation(bash: str, command: str, env=None):
    """Transport shell source past MSYS argv parsing without losing escapes.

    MSYS parses the native Windows command line differently from the CRT
    quoting used by subprocess. In particular, doubled backslashes inside a
    `-c` argument can collapse. Environment values are passed verbatim. Remove
    the transport variable before evaluating so descendant tools do not inherit
    a second copy of the command (which may contain credentials).
    """
    child_env = _utf8_shell_environment(env)
    child_env["OPENPROGRAM_INTERNAL_SHELL_COMMAND"] = command
    trampoline = (
        '__openprogram_source=$OPENPROGRAM_INTERNAL_SHELL_COMMAND; '
        'unset OPENPROGRAM_INTERNAL_SHELL_COMMAND; '
        'export -n __openprogram_source; eval "$__openprogram_source"'
    )
    return [bash, "-c", trampoline], child_env


def platform_environment_advisories(state_dir) -> list[tuple[bool, str, str]]:
    """Return non-blocking host advice without changing system settings.

    Rows are marked successful because these are optional platform capability
    and performance notes rather than requirements for running OpenProgram.
    No probe changes service state, security policy, ACLs, or file modes.
    """

    if _sys.platform.startswith("linux"):
        from openprogram.sandbox import unavailable_reason

        sandbox_reason = unavailable_reason()
        sandbox_detail = (
            "available (bubblewrap namespaces verified)"
            if sandbox_reason is None
            else f"optional isolation unavailable: {sandbox_reason}"
        )
        service_reason = _linux_systemd_user_reason()
        service_detail = (
            "available"
            if service_reason is None
            else f"optional login service unavailable: {service_reason}; "
                 "use `openprogram worker start`"
        )
        return [
            (True, "linux sandbox", sandbox_detail),
            (True, "systemd user service", service_detail),
        ]

    if _sys.platform != "win32":
        return []

    long_paths = _windows_powershell(
        "$v=(Get-ItemProperty "
        "'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem' "
        "-Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled;"
        "if($null -eq $v){'unknown'}elseif($v -eq 1){'enabled'}else{'disabled'}"
    ).strip().lower()
    if long_paths == "enabled":
        long_path_detail = "enabled"
    elif long_paths == "disabled":
        long_path_detail = (
            "disabled — enable Win32 long paths if deeply nested Programs fail"
        )
    else:
        long_path_detail = "status unavailable; no setting was changed"

    import json

    defender_output = _windows_powershell(
        "$s=Get-MpComputerStatus -ErrorAction Stop;"
        "$p=Get-MpPreference -ErrorAction Stop;"
        "[pscustomobject]@{realTime=[bool]$s.RealTimeProtectionEnabled;"
        "exclusions=@($p.ExclusionPath)}|ConvertTo-Json -Compress",
        timeout=8.0,
    ).strip()
    runtime_dir = _os.path.join(_os.fspath(state_dir), "runtime")
    defender_detail = "status unavailable; no setting was changed"
    if defender_output:
        try:
            payload = json.loads(defender_output)
            exclusions = payload.get("exclusions") or []
            if isinstance(exclusions, str):
                exclusions = [exclusions]
            normalized_runtime = _os.path.normcase(_os.path.abspath(runtime_dir))
            excluded = any(
                _os.path.normcase(_os.path.abspath(str(value))).rstrip("\\/")
                == normalized_runtime.rstrip("\\/")
                for value in exclusions
                if value
            )
            if excluded:
                defender_detail = f"runtime exclusion present: {runtime_dir}"
            elif payload.get("realTime"):
                defender_detail = (
                    "real-time scanning active; if startup is slow, consider "
                    f"excluding only {runtime_dir}"
                )
            else:
                defender_detail = "real-time scanning is not active"
        except (TypeError, ValueError, OSError):
            pass

    return [
        (True, "windows long paths", long_path_detail),
        (True, "windows defender", defender_detail),
    ]


def _linux_systemd_user_reason() -> str | None:
    """Why a Linux systemd user manager cannot be reached, if any."""

    import shutil

    executable = shutil.which("systemctl")
    if executable is None:
        return "systemctl is not installed"
    try:
        result = _subprocess.run(
            [executable, "--user", "show-environment"],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except _subprocess.TimeoutExpired:
        return "systemctl --user timed out"
    except OSError as exc:
        return f"systemctl --user probe failed: {exc}"
    if result.returncode == 0:
        return None
    lines = (result.stderr or result.stdout).strip().splitlines()
    detail = lines[-1] if lines else f"exit status {result.returncode}"
    container = (
        _os.path.exists("/.dockerenv")
        or _os.path.exists("/run/.containerenv")
        or bool(_os.environ.get("container"))
    )
    if container:
        return f"container has no reachable user manager ({detail})"
    return detail


def process_command_line(pid: int) -> str:
    """Best-effort command line query for one process on this host."""

    if pid <= 0:
        return ""
    if _sys.platform == "win32":
        output = _windows_powershell(
            "$p=Get-CimInstance Win32_Process -Filter \"ProcessId = "
            f"{int(pid)}\";if($null -ne $p){{[Console]::Out.Write($p.CommandLine)}}"
        )
        return output.strip()
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as stream:
            return (
                stream.read()
                .replace(b"\x00", b" ")
                .decode("utf-8", "replace")
                .strip()
            )
    except OSError:
        pass
    try:
        result = _subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip()
    except (OSError, _subprocess.TimeoutExpired):
        return ""


def pids_on_port(port: int) -> list[int]:
    """Return PIDs listening on one TCP port, or an empty list on error."""

    if _sys.platform == "win32":
        try:
            result = _subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=no_window_creation_flags(),
            )
        except (OSError, _subprocess.TimeoutExpired):
            return []
        pids: list[int] = []
        needle = f":{port}"
        for line in (result.stdout or "").splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[3].upper() != "LISTENING":
                continue
            if not parts[1].endswith(needle):
                continue
            try:
                pids.append(int(parts[4]))
            except ValueError:
                pass
        return pids

    try:
        result = _subprocess.run(
            ["lsof", f"-iTCP:{port}", "-sTCP:LISTEN", "-nP", "-Fp"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, _subprocess.TimeoutExpired):
        result = None
    if result is not None:
        found = {
            int(line[1:])
            for line in result.stdout.splitlines()
            if line.startswith("p") and line[1:].isdigit()
        }
        if found:
            return sorted(found)

    # ``lsof`` is not part of many minimal Linux images (including the
    # distributions people commonly use for a packaged runtime).  The kernel
    # already exposes the authoritative socket table, so fall back to mapping
    # LISTEN socket inodes from /proc/net/tcp{,6} to /proc/<pid>/fd links.
    # Permission-denied processes are skipped; diagnostics remain best-effort.
    if _sys.platform.startswith("linux"):
        return _linux_proc_pids_on_port(port)
    return []


def _linux_listening_socket_inodes(
    port: int,
    *,
    proc_root: str = "/proc",
) -> set[str]:
    """Return Linux TCP LISTEN socket inodes for ``port`` from procfs."""

    if not 0 <= int(port) <= 65535:
        return set()
    inodes: set[str] = set()
    for table in ("tcp", "tcp6"):
        path = _os.path.join(proc_root, "net", table)
        try:
            with open(path, encoding="ascii", errors="replace") as stream:
                rows = stream.readlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            # linux/net/tcp exposes: sl, local_address, rem_address, st,
            # ..., inode.  0A is TCP_LISTEN and inode is column 10.
            if len(fields) < 10 or fields[3].upper() != "0A":
                continue
            try:
                local_port = int(fields[1].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError):
                continue
            inode = fields[9]
            if local_port == int(port) and inode.isdigit() and inode != "0":
                inodes.add(inode)
    return inodes


def _linux_proc_pids_on_port(
    port: int,
    *,
    proc_root: str = "/proc",
) -> list[int]:
    """Map Linux procfs socket inodes to owning process IDs."""

    import glob

    inodes = _linux_listening_socket_inodes(port, proc_root=proc_root)
    if not inodes:
        return []
    owners: set[int] = set()
    pattern = _os.path.join(proc_root, "[0-9]*", "fd", "*")
    for descriptor in glob.iglob(pattern):
        try:
            target = _os.readlink(descriptor)
        except OSError:
            continue
        if not target.startswith("socket:[") or not target.endswith("]"):
            continue
        if target[8:-1] not in inodes:
            continue
        pid_text = _os.path.basename(
            _os.path.dirname(_os.path.dirname(descriptor))
        )
        try:
            owners.add(int(pid_text))
        except ValueError:
            continue
    return sorted(owners)


def process_ids_by_name(names) -> list[int]:
    """Return Windows PIDs whose executable basename is in ``names``."""

    if _sys.platform != "win32":
        return []
    safe_names = sorted(
        {
            name.lower()
            for name in names
            if name and all(char.isalnum() or char in "._-" for char in name)
        }
    )
    if not safe_names:
        return []
    query = " OR ".join(f"Name = '{name}'" for name in safe_names)
    output = _windows_powershell(
        "Get-CimInstance Win32_Process -Filter \""
        + query
        + '\"|ForEach-Object{[Console]::Out.WriteLine($_.ProcessId)}'
    )
    pids: list[int] = []
    for line in output.splitlines():
        try:
            pids.append(int(line.strip()))
        except ValueError:
            continue
    return pids


def kill_processes_matching(names, command_line_fragment: str) -> None:
    """Best-effort force-kill named processes whose command line matches.

    Windows enumerates processes through CIM and terminates the matching
    process trees with ``taskkill``. POSIX enumerates process IDs and performs
    a literal command-line substring match.  Do not route the fragment through
    ``pkill -f``: pkill interprets it as a regular expression (so paths can
    match unintended processes) and its own invocation may match the pattern.
    Keeping both implementations here prevents product commands from growing
    platform branches of their own.
    """

    if not command_line_fragment:
        return
    if _sys.platform == "win32":
        normalized_fragment = command_line_fragment.replace("\\", "/").lower()
        for pid in process_ids_by_name(names):
            command_line = process_command_line(pid).replace("\\", "/").lower()
            if normalized_fragment not in command_line:
                continue
            try:
                _subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    check=False,
                    stdout=_subprocess.DEVNULL,
                    stderr=_subprocess.DEVNULL,
                    timeout=10,
                    creationflags=no_window_creation_flags(),
                )
            except (FileNotFoundError, _subprocess.TimeoutExpired, OSError):
                pass
        return

    fragment = command_line_fragment
    for pid, command_line in _posix_process_command_lines().items():
        if pid == _os.getpid():
            continue
        if fragment not in command_line:
            continue
        kill_process_tree(pid)


def _posix_process_command_lines() -> dict[int, str]:
    """Best-effort POSIX process snapshot for literal command matching."""

    if _sys.platform.startswith("linux"):
        import glob

        pids = {
            int(_os.path.basename(path))
            for path in glob.iglob("/proc/[0-9]*")
            if _os.path.basename(path).isdigit()
        }
        return {
            pid: command
            for pid in sorted(pids)
            if (command := process_command_line(pid))
        }
    try:
        result = _subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, _subprocess.TimeoutExpired):
        return {}
    values: dict[int, str] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if not parts or not parts[0].isdigit():
            continue
        values[int(parts[0])] = parts[1] if len(parts) > 1 else ""
    return values


def conversational_update_backend() -> str | None:
    """Installed source-update controller adapter, not the release updater.

    A worker service adapter alone is insufficient: packaging, activation,
    recovery and native verification must all implement the same transaction.
    Only the macOS controller currently supplies that complete adapter.
    """
    return "launchd" if _sys.platform == "darwin" else None


def worker_service_backend() -> str | None:
    """Name of the per-user worker service adapter for this host."""

    if _sys.platform == "darwin":
        return "launchd"
    if _sys.platform == "linux":
        return "systemd"
    if _sys.platform == "win32":
        return "windows"
    return None


def restrict_descriptor_to_user(descriptor: int) -> None:
    """Apply POSIX descriptor mode without rewriting inherited Windows ACLs."""
    if _sys.platform != "win32":
        _os.fchmod(descriptor, 0o600)


def restrict_to_user(path) -> None:
    """Apply owner-only POSIX mode; preserve inherited ACLs on Windows W1.

    POSIX: ``chmod 0o600`` (owner read/write, nothing for group/other) —
    identical to the bare ``os.chmod(path, 0o600)`` call sites this
    replaces.

    Windows W1 deliberately preserves the inherited NTFS ACL. POSIX mode
    bits have no equivalent there, while rewriting inherited ACLs has made
    state unreadable for domain accounts, OneDrive folders, and managed
    machines. Credential-specific ACL hardening
    therefore remains outside W1; callers still get the normal user-profile
    ACL without a compatibility-breaking mutation.
    """
    p = _os.fspath(path)
    if _sys.platform == "win32":
        return
    try:
        _os.chmod(p, 0o600)
    except OSError:
        pass


def restrict_directory_to_user(path) -> None:
    """Apply owner-only POSIX mode; preserve inherited ACLs on Windows W1."""

    p = _os.fspath(path)
    if _sys.platform == "win32":
        return
    try:
        _os.chmod(p, 0o700)
    except OSError:
        pass


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


def interactive_pty_available() -> bool:
    """True when an :class:`InteractivePty` can be spawned on this host."""
    if _sys.platform == "win32":
        try:
            import winpty  # noqa: F401  (pywinpty)
            return True
        except Exception:
            return False
    try:
        import pty  # noqa: F401
        return True
    except Exception:
        return False


class InteractivePty:
    """Spawn ``argv`` under a pseudo-terminal and drive it line by line.

    Unified API over POSIX ``pty`` and Windows ConPTY (``pywinpty``):

      * ``read_nonblocking(timeout)`` → text seen so far, or ``""`` if nothing
        arrived within ``timeout`` seconds.
      * ``write(text)`` → send ``text`` as if typed at the prompt.
      * ``wait(timeout)`` → the child's exit code (raises
        :class:`subprocess.TimeoutExpired` on timeout).
      * ``kill()`` / ``close()`` → terminate + release the pty.
      * ``alive`` → whether the child is still running.

    Raises :class:`RuntimeError` from the constructor when no pty backend
    exists — guard with :func:`interactive_pty_available` to fall back."""

    def __init__(self, argv, env=None, *, cols: int = 120, rows: int = 40) -> None:
        self._argv = list(argv)
        self._closed = False
        if _sys.platform == "win32":
            self._init_windows(env, cols, rows)
        else:
            self._init_posix(env, cols, rows)

    # -- POSIX (stdlib pty) -----------------------------------------------
    def _init_posix(self, env, cols, rows) -> None:
        try:
            import pty
        except ImportError as e:  # pragma: no cover - exotic POSIX build
            raise RuntimeError("pty unavailable on this host") from e
        self._backend = "posix"
        self._master, slave = pty.openpty()
        try:
            try:
                import fcntl as _f
                import struct
                import termios
                _f.ioctl(self._master, termios.TIOCSWINSZ,
                         struct.pack("HHHH", rows, cols, 0, 0))
            except Exception:
                pass
            self._proc = _subprocess.Popen(
                self._argv, stdin=slave, stdout=slave, stderr=slave,
                close_fds=True, env=env, start_new_session=True,
            )
        except BaseException:
            # Popen failed (ENOENT / EMFILE / permissions): close both fds so
            # they don't leak — __init__ is aborting and the half-built object
            # won't be close()d by the caller.
            for _fd in (self._master, slave):
                try:
                    _os.close(_fd)
                except OSError:
                    pass
            raise
        _os.close(slave)

    def _posix_read(self, timeout: float) -> str:
        import select
        try:
            r, _w, _e = select.select([self._master], [], [], timeout)
        except (OSError, ValueError):
            return ""
        if self._master not in r:
            return ""
        try:
            data = _os.read(self._master, 4096)
        except OSError:
            return ""
        return data.decode("utf-8", "replace")

    # -- Windows (ConPTY via pywinpty) ------------------------------------
    def _init_windows(self, env, cols, rows) -> None:
        try:
            import winpty
        except Exception as e:  # ImportError or a binding load failure
            raise RuntimeError(
                "pywinpty (winpty) is required to drive an interactive login "
                "on Windows; install it or use the token paste flow"
            ) from e
        import queue as _queue
        import threading
        self._backend = "win"
        self._queue: "_queue.Queue" = _queue.Queue()
        # pywinpty's PtyProcess.spawn accepts an argv list and an env dict.
        self._proc = winpty.PtyProcess.spawn(
            self._argv, env=env, dimensions=(rows, cols),
        )

        def _pump() -> None:
            # ConPTY reads block; a daemon thread funnels chunks to the queue
            # so read_nonblocking() can honour a timeout. read() raises EOF at
            # child exit.
            try:
                while True:
                    chunk = self._proc.read(4096)
                    if chunk:
                        self._queue.put(chunk)
            except Exception:
                pass
            finally:
                self._queue.put(None)  # EOF sentinel

        self._reader = threading.Thread(target=_pump, daemon=True)
        self._reader.start()

    def _win_read(self, timeout: float) -> str:
        import queue as _queue
        try:
            chunk = self._queue.get(timeout=timeout)
        except _queue.Empty:
            return ""
        if chunk is None:  # EOF sentinel — child exited
            return ""
        buf = [chunk]
        try:  # drain anything already queued without blocking
            while True:
                more = self._queue.get_nowait()
                if more is None:
                    break
                buf.append(more)
        except _queue.Empty:
            pass
        return "".join(buf)

    # -- unified API -------------------------------------------------------
    def read_nonblocking(self, timeout: float = 1.0) -> str:
        if self._backend == "win":
            return self._win_read(timeout)
        return self._posix_read(timeout)

    def write(self, text: str) -> None:
        try:
            if self._backend == "win":
                # A ConPTY completes a line on CR (the Enter keypress), not a
                # bare LF — so translate "\n" to "\r\n". Callers can keep
                # writing "<line>\n" and it works on both platforms. (Normalise
                # any existing CRLF first to avoid "\r\r\n".)
                text = text.replace("\r\n", "\n").replace("\n", "\r\n")
                self._proc.write(text)
            else:
                _os.write(self._master, text.encode("utf-8"))
        except Exception:
            pass

    @property
    def alive(self) -> bool:
        try:
            if self._backend == "win":
                return bool(self._proc.isalive())
            return self._proc.poll() is None
        except Exception:
            return False

    def wait(self, timeout: float | None = None) -> int:
        if self._backend == "win":
            import time as _t
            end = None if timeout is None else _t.time() + timeout
            while self._proc.isalive():
                if end is not None and _t.time() >= end:
                    raise _subprocess.TimeoutExpired(self._argv, timeout)
                _t.sleep(0.1)
            return int(self._proc.exitstatus or 0)
        return self._proc.wait(timeout=timeout)

    def kill(self) -> None:
        # Kill the whole tree, not just the leader. On Windows the spawned
        # child is `cmd.exe /c meridian.cmd …` (node_tool_cmd wraps the .cmd
        # shim) which in turn spawns node; on POSIX the backend itself spawns
        # `claude`. Terminating only the leader would orphan the real OAuth
        # process. kill_process_tree handles both (taskkill /T on Windows,
        # killpg on POSIX — the child leads its own session via start_new_session).
        pid = getattr(getattr(self, "_proc", None), "pid", None)
        if pid:
            try:
                kill_process_tree(pid)
            except Exception:
                pass
        try:
            if self._backend == "win":
                self._proc.terminate(force=True)
            else:
                self._proc.kill()
        except Exception:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._backend == "win":
                try:
                    # PtyProcess.close() closes its TCP bridge, but pywinpty's
                    # native reader can still be blocked in PTY.read().  Wake
                    # that read first so both the bridge and our queue pump can
                    # terminate deterministically.
                    self._proc.pty.cancel_io()
                except Exception:
                    pass
                try:
                    self._proc.close(force=True)
                except Exception:
                    pass
                # The adapter pump and pywinpty's internal read helper must
                # observe the closed ConPTY before close() returns. Leaving
                # daemon readers alive leaks production threads into the next
                # command/test and can retain console handles unnecessarily.
                reader = getattr(self, "_reader", None)
                if reader is not None:
                    try:
                        reader.join(timeout=2.0)
                    except Exception:
                        pass
                native_reader = getattr(self._proc, "_thread", None)
                if native_reader is not None:
                    try:
                        native_reader.join(timeout=2.0)
                    except Exception:
                        pass
            else:
                try:
                    _os.close(self._master)
                except OSError:
                    pass
        except Exception:
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


def is_link_metadata(info) -> bool:
    """Recognize POSIX links and Windows directory junction/reparse entries."""
    import stat
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def user_private_metadata(info, *, exact_mode: int | None = None) -> bool:
    """POSIX ownership policy; Windows uses inherited profile ACLs unchanged."""
    import stat
    if _sys.platform == "win32":
        return not bool(getattr(info, "st_file_attributes", 0) & 0x400)
    if info.st_uid != _os.getuid():
        return False
    mode = stat.S_IMODE(info.st_mode)
    return mode == exact_mode if exact_mode is not None else not bool(mode & 0o077)


def open_regular_binary(path):
    """Open a stable regular file for streaming, without changing its access policy."""
    import stat

    native = filesystem_path(path)
    before = _os.lstat(native)
    if not stat.S_ISREG(before.st_mode) or is_link_metadata(before):
        raise ValueError("not a regular file")
    flags = (_os.O_RDONLY | getattr(_os, "O_NONBLOCK", 0)
             | getattr(_os, "O_BINARY", 0) | getattr(_os, "O_NOFOLLOW", 0))
    fd = _os.open(native, flags)
    try:
        info = _os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or is_link_metadata(info)
                or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)):
            raise ValueError("file changed while opening")
        return _os.fdopen(fd, "rb")
    except BaseException:
        _os.close(fd)
        raise


def read_user_state_bytes(path, *, limit: int) -> bytes:
    """Bounded regular-file read using the host's user-state metadata policy."""
    import stat
    before = _os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or not user_private_metadata(before):
        raise ValueError("invalid user-state file")
    flags = (_os.O_RDONLY | getattr(_os, "O_NONBLOCK", 0)
             | getattr(_os, "O_BINARY", 0) | getattr(_os, "O_NOFOLLOW", 0))
    fd = _os.open(path, flags)
    with _os.fdopen(fd, "rb") as stream:
        info = _os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or not user_private_metadata(info)
                or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)
                or info.st_size > limit):
            raise ValueError("invalid user-state file")
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("user-state file exceeds read limit")
        return raw


def process_start_token(pid: int) -> str | None:
    """Return a creation-time identity, independent of executable spelling."""
    if pid <= 0:
        return None
    if _sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
                return None
            created = times[0]
            return f"win:{(created.dwHighDateTime << 32) | created.dwLowDateTime}"
        finally:
            kernel.CloseHandle(handle)
    if _sys.platform.startswith("linux"):
        from pathlib import Path
        try:
            # comm may contain spaces and parentheses; fields begin after its last ')'.
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()
            return f"proc:{fields[19]}"
        except (OSError, IndexError, UnicodeError):
            return None
    try:
        result = _subprocess.run(["ps", "-p", str(pid), "-o", "lstart="],
                                 capture_output=True, text=True, timeout=1, check=False)
        value = result.stdout.strip()
        return f"ps:{value}" if result.returncode == 0 and value else None
    except (OSError, _subprocess.SubprocessError):
        return None


def process_alive(pid: int) -> bool:
    """Probe without signalling: Windows kill(pid, 0) can terminate a process."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if _sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied is not evidence of exit.
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True  # Conservatively retain an owner on query failure.
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    if _sys.platform.startswith("linux"):
        from pathlib import Path
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()
            if fields and fields[0] == "Z":
                return False
        except OSError:
            pass  # Fall through to the conventional POSIX existence probe.
    try:
        _os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


_DIRECTORY_FD_SUPPORTED = os.open in os.supports_dir_fd and os.scandir in os.supports_fd


def directory_handle(path):
    """Open a no-follow directory; path fallback where dir_fd is unavailable.

    The fallback validates ancestors on each operation but cannot provide POSIX
    descriptor-relative atomicity against concurrent directory replacement.
    It deliberately does not alter Windows ACLs or require directory open().
    """
    import stat
    from pathlib import Path

    if _DIRECTORY_FD_SUPPORTED:
        return os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                       | getattr(os, "O_NOFOLLOW", 0))
    target = Path(path).absolute()
    for component in reversed((target, *target.parents)):
        info = component.lstat()
        if (not stat.S_ISDIR(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise OSError(errno.ELOOP, "directory is a link or not a directory")
    return str(target)


def directory_child(handle, name):
    if isinstance(handle, str):
        return directory_handle(os.path.join(handle, name))
    return os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                   | getattr(os, "O_NOFOLLOW", 0), dir_fd=handle)


def directory_close(handle):
    if not isinstance(handle, str):
        os.close(handle)


def directory_duplicate(handle):
    return directory_handle(handle) if isinstance(handle, str) else os.dup(handle)


def directory_read_file(handle, name):
    """Return a binary file descriptor without following a reparse point."""
    import stat

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if not isinstance(handle, str):
        return os.open(name, flags, dir_fd=handle)
    directory_handle(handle)
    path = os.path.join(handle, name)
    before = os.lstat(path)
    if (not stat.S_ISREG(before.st_mode)
            or getattr(before, "st_file_attributes", 0) & 0x400):
        raise OSError(errno.ELOOP, "file is a link or not a regular file")
    fd = os.open(path, flags)
    after = os.fstat(fd)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        os.close(fd)
        raise OSError(errno.ESTALE, "file changed while opening")
    return fd


__all__ = [
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

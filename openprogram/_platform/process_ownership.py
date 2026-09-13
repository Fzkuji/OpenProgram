"""Platform process ownership."""
from __future__ import annotations
from .. import _compat as state


def no_window_creation_flags() -> int:
    """Flags for background subprocesses that must not flash a console."""

    if state._sys.platform != "win32":
        return 0
    return int(getattr(state._subprocess, "CREATE_NO_WINDOW", 0))


def process_tree_popen_kwargs() -> dict[str, object]:
    """Creation options for a child that may need whole-tree termination.

    POSIX tree termination is only safe when the child leads its own session;
    otherwise a shell inherits the caller's process group and ``killpg`` could
    terminate OpenProgram itself.  Windows ``taskkill /T`` discovers descendants
    by PID, while ``CREATE_NEW_PROCESS_GROUP`` keeps console control events from
    leaking between the child command and the interactive parent.
    """

    if state._sys.platform == "win32":
        flags = state.no_window_creation_flags()
        flags |= int(getattr(state._subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
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

    def popen(self, *args, **kwargs) -> state._subprocess.Popen:
        """Start and take ownership of one process tree."""

        if self._started:
            raise RuntimeError("a ProcessTreeOwner can only start one process")
        self._started = True
        if state._sys.platform == "win32":
            return self._popen_windows(*args, **kwargs)

        if "start_new_session" in kwargs:
            raise TypeError(
                "ProcessTreeOwner controls the start_new_session option"
            )
        proc = state._subprocess.Popen(*args, start_new_session=True, **kwargs)
        # start_new_session makes the initial PID the new PGID.  Retain that
        # value instead of asking getpgid(pid) during cleanup: the leader may
        # already be a reaped shell while background members still exist.
        self._pgid = proc.pid
        return proc

    def _popen_windows(self, *args, **kwargs) -> state._subprocess.Popen:
        if "creationflags" in kwargs:
            creationflags = int(kwargs.pop("creationflags"))
        else:
            creationflags = 0
        creationflags |= state.no_window_creation_flags()
        creationflags |= int(
            getattr(state._subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        creationflags |= int(getattr(state._subprocess, "CREATE_SUSPENDED", 0x00000004))

        job_handle = state._windows_create_kill_on_close_job()
        proc: state._subprocess.Popen | None = None
        assigned = False
        try:
            proc = state._subprocess.Popen(
                *args,
                creationflags=creationflags,
                **kwargs,
            )
            state._windows_assign_process_to_job(job_handle, proc)
            assigned = True
            state._windows_resume_process(proc.pid)
        except BaseException:
            # A CREATE_SUSPENDED process has not had an opportunity to spawn
            # children.  If assignment succeeded, closing the kill-on-close
            # job is the most reliable cleanup; otherwise terminate the one
            # suspended process directly.
            if assigned:
                state._windows_terminate_and_close_job(job_handle)
            else:
                state._windows_close_handle(job_handle)
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
                except (OSError, state._subprocess.TimeoutExpired):
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

    def active_process_count(self) -> int:
        """Read the Windows kernel-owned membership, even after the leader exits."""
        if state._sys.platform != "win32" or self._job_handle is None:
            raise RuntimeError("an active Windows Job Object is required")
        return state._windows_job_active_processes(self._job_handle)

    def terminate_members(self) -> None:
        """Terminate Windows members while retaining the handle for exit proof."""
        if state._sys.platform != "win32" or self._job_handle is None:
            raise RuntimeError("an active Windows Job Object is required")
        import ctypes
        kernel32, _, _ = state._windows_job_api()
        if not kernel32.TerminateJobObject(self._job_handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self) -> bool:
        """Force-kill the owned tree.  Best-effort and idempotent."""

        if self._finished:
            return False
        self._finished = True
        if state._sys.platform == "win32":
            job_handle, self._job_handle = self._job_handle, None
            if job_handle is None:
                return False
            return state._windows_terminate_and_close_job(job_handle)

        pgid, self._pgid = self._pgid, None
        if pgid is None:
            return False
        try:
            state._os.killpg(pgid, state._signal.SIGKILL)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False

    def release(self) -> None:
        """Release a normally completed tree without killing descendants."""

        if self._finished:
            return
        self._finished = True
        if state._sys.platform == "win32":
            job_handle, self._job_handle = self._job_handle, None
            if job_handle is not None:
                state._windows_release_job(job_handle)
        else:
            self._pgid = None


@state._functools.cache
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


def _windows_job_active_processes(job_handle: int) -> int:
    import ctypes
    from ctypes import wintypes

    class BasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    kernel32, _, _ = state._windows_job_api()
    query = kernel32.QueryInformationJobObject
    query.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                      wintypes.DWORD, wintypes.LPVOID]
    query.restype = wintypes.BOOL
    info = BasicAccountingInformation()
    if not query(job_handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(info.ActiveProcesses)


def _windows_set_job_kill_on_close(job_handle: int, enabled: bool) -> None:
    import ctypes

    kernel32, info_type, _thread_type = state._windows_job_api()
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

    kernel32, _info_type, _thread_type = state._windows_job_api()
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    value = int(handle)
    try:
        state._windows_set_job_kill_on_close(value, True)
    except BaseException:
        state._windows_close_handle(value)
        raise
    return value


def _windows_assign_process_to_job(
    job_handle: int,
    proc: state._subprocess.Popen,
) -> None:
    import ctypes

    kernel32, _info_type, _thread_type = state._windows_job_api()
    process_handle = int(getattr(proc, "_handle"))
    if not kernel32.AssignProcessToJobObject(job_handle, process_handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _windows_resume_process(pid: int) -> None:
    """Resume the primary thread of a CREATE_SUSPENDED process."""

    import ctypes

    kernel32, _info_type, thread_type = state._windows_job_api()
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
    kernel32, _info_type, _thread_type = state._windows_job_api()
    return bool(kernel32.CloseHandle(handle))


def _windows_terminate_and_close_job(job_handle: int) -> bool:
    kernel32, _info_type, _thread_type = state._windows_job_api()
    terminated = bool(kernel32.TerminateJobObject(job_handle, 1))
    closed = state._windows_close_handle(job_handle)
    return terminated or closed


def _windows_release_job(job_handle: int) -> None:
    """Destroy a job without applying its kill-on-close limit."""

    try:
        state._windows_set_job_kill_on_close(job_handle, False)
    except OSError:
        # SetInformationJobObject should be infallible for our own handle.  If
        # the OS nevertheless rejects it, retaining the handle is safer than
        # unexpectedly killing a deliberately detached background command.
        return
    state._windows_close_handle(job_handle)

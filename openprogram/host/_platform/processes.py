"""Platform processes."""
from __future__ import annotations
import os as _os
import signal as _signal
import subprocess as _subprocess
import sys as _sys
from openprogram.host._platform import process_ownership
from openprogram.host._platform import terminal


def _oem_codec() -> str:
    """Codec of the code page Windows console tools write to a pipe in."""

    return "oem"


def console_text(data: bytes | str | None) -> str:
    """Decode what a Windows console tool wrote to a pipe.

    netstat, schtasks and Windows PowerShell write in their console's code
    page. Started with ``no_window_creation_flags()`` they get a console of
    their own, whose code page is the OEM one (cp936 on a Chinese system).
    Python's text mode decodes with the ANSI code page, or strictly as UTF-8
    under ``-X utf8``, and a strict decode that fails loses the whole output.
    So callers capture bytes and decode here, never raising. Guessing UTF-8
    first is not safe: many two-byte cp936 characters are valid UTF-8.
    """

    if data is None:
        return ""
    if isinstance(data, str):
        return data
    if _sys.platform == "win32":
        try:
            return data.decode(_oem_codec(), "replace")
        except LookupError:
            pass
    return data.decode("utf-8", "replace")


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
                creationflags=process_ownership.no_window_creation_flags(),
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


def _windows_command_line(pid: int) -> str | None:
    """Read a process's command line through the kernel, or None.

    Asks NtQueryInformationProcess for ProcessCommandLineInformation (class
    60, Windows 8.1 and later) on a limited-query handle. A PowerShell CIM
    query needs a new PowerShell each time, which on a freshly installed or
    busy machine takes longer than its timeout and answers nothing; that
    made ``worker stop`` report a running worker as not running. None means
    the kernel could not answer (no such process, no access, older Windows)
    and the caller falls back to CIM.
    """
    try:
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll")
    except (AttributeError, OSError):
        return None

    class _UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", ctypes.c_void_p),
        ]

    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    query = ntdll.NtQueryInformationProcess
    query.argtypes = [
        wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    query.restype = ctypes.c_long
    handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        size = ctypes.c_ulong(0)
        query(handle, 60, None, 0, ctypes.byref(size))
        if not size.value:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if query(handle, 60, buffer, size.value, ctypes.byref(size)) < 0:
            return None
        text = _UnicodeString.from_buffer(buffer)
        if not text.Buffer or not text.Length:
            return ""
        return ctypes.wstring_at(text.Buffer, text.Length // 2)
    finally:
        kernel.CloseHandle(handle)


def process_command_line(pid: int) -> str:
    """Best-effort command line query for one process on this host."""

    if pid <= 0:
        return ""
    if _sys.platform == "win32":
        native = _windows_command_line(int(pid))
        if native is not None:
            return native.strip()
        output = terminal._windows_powershell(
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
        if _sys.platform.startswith("linux"):
            return ""  # gone or not ours; ps would read the same /proc
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
                timeout=3,
                creationflags=process_ownership.no_window_creation_flags(),
            )
        except (OSError, _subprocess.TimeoutExpired):
            return []
        pids: list[int] = []
        needle = f":{port}"
        for line in console_text(result.stdout).splitlines():
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
            fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[-1].split()
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
            fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[-1].split()
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

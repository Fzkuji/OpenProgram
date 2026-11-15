"""Worker status lines stamped with the seconds since the process started.

Every ``[worker]`` line reads ``[worker +12.3s] execution store: ready``, so
a slow start can be read from the worker log: the gap between two lines is
the step between them.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import time


def _process_started_at() -> float | None:
    """Wall-clock time this process was created, from the OS."""
    try:
        if sys.platform == "darwin":
            return _darwin_started_at()
        if sys.platform.startswith("linux"):
            return _linux_started_at()
    except Exception:  # noqa: BLE001 - fall back to the import time
        return None
    return None


def _darwin_started_at() -> float | None:
    # sysctl(CTL_KERN, KERN_PROC, KERN_PROC_PID, pid) fills a kinfo_proc
    # whose first member, extern_proc, starts with the start timeval.
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    mib = (ctypes.c_int * 4)(1, 14, 1, os.getpid())
    buffer = ctypes.create_string_buffer(1024)
    size = ctypes.c_size_t(len(buffer))
    if libc.sysctl(mib, 4, buffer, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
        return None
    seconds = ctypes.c_int64.from_buffer(buffer, 0).value
    microseconds = ctypes.c_int32.from_buffer(buffer, 8).value
    return seconds + microseconds / 1_000_000 if seconds > 0 else None


def _linux_started_at() -> float | None:
    with open("/proc/self/stat", encoding="ascii") as handle:
        fields = handle.read().rsplit(")", 1)[1].split()
    ticks = int(fields[19])  # field 22 (starttime), after pid and comm
    with open("/proc/stat", encoding="ascii") as handle:
        boot = next(int(line.split()[1]) for line in handle if line.startswith("btime "))
    return boot + ticks / os.sysconf("SC_CLK_TCK")


_STARTED_AT = _process_started_at() or time.time()


def elapsed() -> float:
    """Seconds since this process started."""
    return max(0.0, time.time() - _STARTED_AT)


def worker_print(message: str) -> None:
    """Print one worker status line: ``[worker +12.3s] message``."""
    print(f"[worker +{elapsed():.1f}s] {message}")

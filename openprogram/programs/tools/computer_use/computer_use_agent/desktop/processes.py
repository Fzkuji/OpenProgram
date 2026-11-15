"""The applications running right now, read without the main run loop.

``NSWorkspace.runningApplications`` changes only while the main run loop
runs, which a worker never does: it keeps the apps of the moment it was
first read and misses every app launched since. Listing the process ids
and looking each one up is fresh and takes about two milliseconds.
"""
from __future__ import annotations

import ctypes
from typing import Any


def all_pids() -> list[int]:
    """Every process id on the machine; raises OSError where libproc is missing."""
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    count = libproc.proc_listallpids(None, 0)
    buffer = (ctypes.c_int * (max(count, 0) + 256))()
    count = libproc.proc_listallpids(buffer, ctypes.sizeof(buffer))
    if count <= 0:
        raise OSError("proc_listallpids failed")
    return [pid for pid in buffer[:count] if pid > 0]


def running_applications(appkit) -> list[Any]:
    """Every running application as it is now, as ``NSRunningApplication`` objects."""
    try:
        pids = all_pids()
    except OSError:
        return list(appkit.NSWorkspace.sharedWorkspace().runningApplications() or [])
    lookup = appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_
    return [app for pid in pids if (app := lookup(pid)) is not None]


__all__ = ["all_pids", "running_applications"]

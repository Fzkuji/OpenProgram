"""Optional idle-system-sleep assertion for active agent execution.

One assertion per execution, owned by the handle the platform returns:

* macOS: an IOKit ``PreventUserIdleSystemSleep`` assertion.
* Windows: a power request (``PowerCreateRequest`` with
  ``PowerRequestSystemRequired``). It is a handle, so executions on any
  thread hold and clear their own; ``SetThreadExecutionState`` would tie
  the state to the calling thread.
* Linux: a logind block on ``idle:sleep`` held by a ``systemd-inhibit``
  child that reads this process's pipe, so the lock ends with the
  execution and also when the worker dies.

The display may still sleep everywhere. On Linux a suspend the user asks
for waits for the execution too: desktop idle suspend goes through the same
logind request, and logind has no narrower lock that stops it.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import logging
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator

_log = logging.getLogger(__name__)
_IOPM_ASSERTION_LEVEL_ON = 255  # kIOPMAssertionLevelOn in IOPMLib.h
_REASON = "OpenProgram active agent execution"
_POWER_REQUEST_CONTEXT_VERSION = 0
_POWER_REQUEST_CONTEXT_SIMPLE_STRING = 0x1
_POWER_REQUEST_SYSTEM_REQUIRED = 1  # PowerRequestSystemRequired in winnt.h
_INHIBIT_RELEASE_SECONDS = 2.0
# Failures already reported: the setting is read at every execution.
_reported: set[str] = set()


def support(platform: str | None = None, which: Callable[[str], str | None] | None = None) -> dict:
    """Whether this host can hold the assertion, and why not."""
    platform = sys.platform if platform is None else platform
    which = which or shutil.which
    if platform in ("darwin", "win32"):
        return {"available": True, "reason": ""}
    if platform.startswith("linux"):
        if which("systemd-inhibit"):
            return {"available": True, "reason": ""}
        return {"available": False,
                "reason": "Preventing idle sleep on Linux needs systemd-inhibit (systemd-logind)."}
    return {"available": False, "reason": f"Preventing idle sleep is not supported on {platform}."}


def _acquire_assertion() -> Callable[[], None]:
    if sys.platform == "darwin":
        return _acquire_macos()
    if sys.platform == "win32":
        return _acquire_windows()
    if sys.platform.startswith("linux"):
        return _acquire_linux()
    raise OSError(support()["reason"])


class _ReasonDetail(ctypes.Structure):
    _fields_ = [("LocalizedReasonModule", ctypes.c_void_p), ("LocalizedReasonId", ctypes.c_uint32),
                ("ReasonStringCount", ctypes.c_uint32), ("ReasonStrings", ctypes.c_void_p)]


class _ReasonText(ctypes.Union):
    _fields_ = [("Detailed", _ReasonDetail), ("SimpleReasonString", ctypes.c_wchar_p)]


class _ReasonContext(ctypes.Structure):
    """``REASON_CONTEXT`` from winnt.h."""
    _fields_ = [("Version", ctypes.c_uint32), ("Flags", ctypes.c_uint32), ("Reason", _ReasonText)]


def _acquire_windows(kernel32=None) -> Callable[[], None]:
    """Hold a system-required power request until the returned call."""
    if kernel32 is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.PowerCreateRequest.argtypes = [ctypes.POINTER(_ReasonContext)]
    kernel32.PowerCreateRequest.restype = ctypes.c_void_p
    for name in ("PowerSetRequest", "PowerClearRequest"):
        getattr(kernel32, name).argtypes = [ctypes.c_void_p, ctypes.c_int]
        getattr(kernel32, name).restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    def failure(call: str) -> OSError:
        code = getattr(ctypes, "get_last_error", lambda: 0)()
        return OSError(f"{call} failed: Windows error {code}")

    context = _ReasonContext(_POWER_REQUEST_CONTEXT_VERSION, _POWER_REQUEST_CONTEXT_SIMPLE_STRING)
    context.Reason.SimpleReasonString = _REASON
    handle = kernel32.PowerCreateRequest(ctypes.byref(context))
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise failure("PowerCreateRequest")
    if not kernel32.PowerSetRequest(handle, _POWER_REQUEST_SYSTEM_REQUIRED):
        error = failure("PowerSetRequest")
        kernel32.CloseHandle(handle)
        raise error

    def release() -> None:
        try:
            if not kernel32.PowerClearRequest(handle, _POWER_REQUEST_SYSTEM_REQUIRED):
                raise failure("PowerClearRequest")
        finally:
            kernel32.CloseHandle(handle)

    return release


def _acquire_linux(which: Callable[[str], str | None] | None = None,
                   popen: Callable[..., subprocess.Popen] | None = None) -> Callable[[], None]:
    """Hold a logind lock through ``systemd-inhibit`` until the returned call.

    The child runs ``cat`` on a pipe from this process: closing the pipe ends
    it, and so does this process dying, so no lock outlives the worker.
    """
    executable = (which or shutil.which)("systemd-inhibit")
    if executable is None:
        raise OSError(support("linux", which)["reason"])
    child = (popen or subprocess.Popen)(
        [executable, "--what=idle:sleep", "--who=OpenProgram", f"--why={_REASON}", "--mode=block", "cat"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    def release() -> None:
        refused = child.poll()
        try:
            if child.stdin is not None:
                child.stdin.close()
            child.wait(timeout=_INHIBIT_RELEASE_SECONDS)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        if refused:
            raise OSError(f"systemd-inhibit could not hold the idle sleep lock (exit status {refused})")

    return release


def _acquire_macos() -> Callable[[], None]:
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    io = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFRelease.restype = None
    io.IOPMAssertionCreateWithName.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
    ]
    io.IOPMAssertionCreateWithName.restype = ctypes.c_int32
    io.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
    io.IOPMAssertionRelease.restype = ctypes.c_int32
    strings = []
    try:
        for value in (b"PreventUserIdleSystemSleep", _REASON.encode()):
            pointer = cf.CFStringCreateWithCString(None, value, 0x08000100)
            if not pointer:
                raise OSError("unable to allocate power assertion description")
            strings.append(pointer)
        assertion_id = ctypes.c_uint32()
        status = io.IOPMAssertionCreateWithName(
            strings[0], _IOPM_ASSERTION_LEVEL_ON, strings[1], ctypes.byref(assertion_id),
        )
        if status:
            raise OSError(f"IOPMAssertionCreateWithName failed: {status}")
    finally:
        for pointer in strings:
            cf.CFRelease(pointer)

    def release() -> None:
        status = io.IOPMAssertionRelease(assertion_id)
        if status:
            raise OSError(f"IOPMAssertionRelease failed: {status}")

    return release


@contextmanager
def prevent_idle_sleep() -> Iterator[None]:
    """Read the opt-in setting once and release this execution's assertion."""
    from openprogram.state.config import read_config

    release = None
    try:
        config = read_config()
        execution = config.get("execution", {})
        if isinstance(execution, dict) and execution.get("prevent_idle_sleep") is True:
            release = _acquire_assertion()
    except OSError as exc:
        _report("Unable to prevent idle system sleep", exc)
    except Exception:
        _log.warning("Unable to prevent idle system sleep", exc_info=True)
    try:
        yield
    finally:
        if release is not None:
            try:
                release()
            except OSError as exc:
                _report("Unable to release idle sleep assertion", exc)
            except Exception:
                _log.warning("Unable to release idle sleep assertion", exc_info=True)


def _report(what: str, exc: OSError) -> None:
    """One line per distinct host refusal, not one traceback per execution."""
    line = f"{what}: {exc}"
    if line not in _reported:
        _reported.add(line)
        _log.warning("%s", line)

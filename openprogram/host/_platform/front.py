"""What the user has in front stays in front while an agent operates something else.

Every backend delivers its own input in the background: nothing it sends
activates an application or raises a window. That covers what the backend
does. It does not cover what the operated application does by itself: one
that starts, opens a page, a document or a dialog may activate itself, and
no option of ours stops it (Chrome comes to the front whenever a tab opens
in front, however the tab was opened).

:func:`kept` is the one answer to all of those. For the length of a step it
watches which application is in front, and the moment an operated
application takes the front it gives the front back to the application the
user was in. An application the user switches to themselves is not an
operated one, so it is left alone and becomes the one the front is given back
to.

Nothing of this is drawn. The window server tells an application that it
came to the front, and the one before it that it left, with events; an
application brings its windows forward and draws its menu bar, and the other
dims its title bars, only on receiving them. An event tap holds those events
back for the few milliseconds the front is away, so neither application ever
learns of it and no window moves. Where the tap cannot be made (no
Accessibility grant), the front is read every 50 ms instead, given back, and
the windows are put back in the order they had.

Windows and Linux on X11 have no place where those events can be held, so
there the front is read (every 20 ms on Windows; on X11 the window manager
reports each change), given back, and the windows put back in order. Two
things differ on those systems, and the backends say so here: applications
are told apart by process, so a backend names the ones it operates
(:func:`note`, and :func:`starting` while one is being started); and some
input is delivered only to the front window, so a backend brings the window
forward on purpose (:func:`forward`) and the guard stands aside until it has
put things back. On Wayland an application cannot take the focus without a
token from the compositor, and nothing here runs.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterator

_log = logging.getLogger(__name__)

POLL_SECONDS = 0.05
# After the front was given back unseen, how long the window order is still compared with the one before.
SETTLE_SECONDS = 0.5
SETTLE_POLL_SECONDS = 0.004
# An application that was started or told to open something may activate
# itself a moment after the step that caused it has returned.
LINGER_SECONDS = 0.8
# A step that keeps taking the front is fought this many times, then left.
MAX_RETURNS = 6
# How many windows of the old stack are put back in order.
MAX_RESTACK = 24
RAISE_TIMEOUT = 0.25
FRONT_WINDOW_ONLY = 1  # kSetFrontProcessFrontWindowOnly

# Window-server events that tell an application its place: it lost the front,
# it gained it, and the AppKit-defined pair an application gets while it starts.
EVENT_APPKIT, EVENT_LOST, EVENT_GAINED = 13, 19, 20
APPKIT_ACTIVATED, APPKIT_DEACTIVATED = 1, 2
_FIELD_TARGET_PID, _FIELD_SUBTYPE = 40, 83
_TAP_ANNOTATED_SESSION = 2
_TAP_DISABLED = 0xFFFFFFFE  # and 0xFFFFFFFF: the system switched the tap off

# The desktop App, stable and development.
APP_BUNDLES = ("ai.openprogram.desktop", "ai.openprogram.desktop.dev")

Operated = Callable[[int], bool]
# A window on screen: its process, its window-server id and its frame (x, y, width, height).
Window = tuple[int, int, tuple[float, float, float, float]]


@dataclass
class Taken:
    """One time an operated application took the front and it was given back."""
    application: str
    seconds: float


@dataclass
class Watch:
    """What a :func:`kept` block saw; ``taken`` is filled while it runs and for a moment after."""
    taken: list[Taken] = field(default_factory=list)


class _Desktop:
    """The window server and workspace calls :func:`kept` needs; replaced in tests."""

    # How often the front is read.
    poll = POLL_SECONDS

    def front(self) -> int | None:
        raise NotImplementedError

    def name(self, pid: int) -> str:
        raise NotImplementedError

    def stack(self) -> list[Window]:
        """Ordinary on-screen windows front to back."""
        raise NotImplementedError

    def activate(self, pid: int) -> bool:
        raise NotImplementedError

    def raise_window(self, pid: int, window_id: int) -> None:
        raise NotImplementedError

    def prepare(self, pids: set[int]) -> None:
        """Have what :meth:`raise_window` needs for these applications at hand, so that raising is one call."""

    def listen(self, handler: Callable[[int, int, int], bool]) -> Callable[[], None] | None:
        """Pass each front-change event to ``handler(kind, subtype, pid)``; ``True`` from it drops the event.

        Returns what stops the listening, or ``None`` where events cannot be held back.
        """
        return None


class _MacDesktop(_Desktop):
    def __init__(self) -> None:
        import ctypes

        self._ctypes = ctypes
        self._services = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        self._elements: dict[int, list[Any]] = {}

    def front(self) -> int | None:
        # NSWorkspace keeps its answer current from the main run loop, which a
        # worker thread does not turn; the process manager is asked directly.
        ctypes = self._ctypes
        serial = (ctypes.c_uint32 * 2)()
        if self._services.GetFrontProcess(ctypes.byref(serial)) != 0:
            return None
        pid = ctypes.c_int(0)
        if self._services.GetProcessPID(ctypes.byref(serial), ctypes.byref(pid)) != 0:
            return None
        return int(pid.value) or None

    def _application(self, pid: int):
        import AppKit

        return AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)

    def name(self, pid: int) -> str:
        try:
            application = self._application(pid)
        except ImportError:
            application = None
        return str(application.localizedName() or "") if application is not None else f"pid {pid}"

    def stack(self) -> list[Window]:
        import Quartz

        rows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []
        found = []
        for row in rows:
            if row.get("kCGWindowLayer") != 0:
                continue
            bounds = row.get("kCGWindowBounds") or {}
            found.append((int(row.get("kCGWindowOwnerPID", 0)), int(row.get("kCGWindowNumber", 0)),
                          (float(bounds.get("X", 0)), float(bounds.get("Y", 0)),
                           float(bounds.get("Width", 0)), float(bounds.get("Height", 0)))))
        return found

    def activate(self, pid: int) -> bool:
        # NSRunningApplication.activate waits a second for the main run loop
        # when a worker thread calls it; the process manager answers at once.
        # Only the application's front window comes forward, as it stood.
        ctypes = self._ctypes
        serial = (ctypes.c_uint32 * 2)()
        if self._services.GetProcessForPID(ctypes.c_int(pid), ctypes.byref(serial)) != 0:
            return False
        return self._services.SetFrontProcessWithOptions(ctypes.byref(serial), ctypes.c_uint32(FRONT_WINDOW_ONLY)) == 0

    def raise_window(self, pid: int, window_id: int) -> None:
        import ApplicationServices as services

        import objc

        if not services.AXIsProcessTrusted():
            raise PermissionError("Accessibility is not granted")
        ctypes = self._ctypes
        # The windows read before, then read again for one that opened since.
        for fresh in (False, True):
            for window in self._windows(pid, fresh):
                number = ctypes.c_uint32(0)
                # The window server's id of an Accessibility window, as window managers read it.
                if self._services._AXUIElementGetWindow(ctypes.c_void_p(objc.pyobjc_id(window)),
                                                        ctypes.byref(number)) == 0 and number.value == window_id:
                    services.AXUIElementPerformAction(window, "AXRaise")
                    return

    def _windows(self, pid: int, fresh: bool) -> list[Any]:
        import ApplicationServices as services

        if fresh or pid not in self._elements:
            application = services.AXUIElementCreateApplication(pid)
            # A busy application is not waited for: the order is a courtesy.
            services.AXUIElementSetMessagingTimeout(application, RAISE_TIMEOUT)
            error, windows = services.AXUIElementCopyAttributeValue(application, "AXWindows", None)
            self._elements[pid] = list(windows or []) if error == 0 else []
        return self._elements[pid]

    def prepare(self, pids: set[int]) -> None:
        import ApplicationServices as services

        if services.AXIsProcessTrusted():
            for pid in pids:
                self._windows(pid, fresh=True)

    def listen(self, handler: Callable[[int, int, int], bool]) -> Callable[[], None] | None:
        ctypes = self._ctypes
        self._services.AXIsProcessTrusted.restype = ctypes.c_ubyte
        if not self._services.AXIsProcessTrusted():
            # Asking for a tap without the grant would put a permission prompt in front of the user.
            return None
        graphics = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        foundation = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        pointer = ctypes.c_void_p
        signature = ctypes.CFUNCTYPE(pointer, pointer, ctypes.c_uint32, pointer, pointer)
        graphics.CGEventTapCreate.restype = pointer
        graphics.CGEventTapCreate.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint64,
                                              signature, pointer]
        graphics.CGEventTapEnable.argtypes = [pointer, ctypes.c_bool]
        graphics.CGEventGetIntegerValueField.restype = ctypes.c_int64
        graphics.CGEventGetIntegerValueField.argtypes = [pointer, ctypes.c_uint32]
        foundation.CFMachPortCreateRunLoopSource.restype = pointer
        foundation.CFMachPortCreateRunLoopSource.argtypes = [pointer, pointer, ctypes.c_long]
        foundation.CFMachPortInvalidate.argtypes = [pointer]
        foundation.CFRunLoopGetCurrent.restype = pointer
        foundation.CFRunLoopAddSource.argtypes = [pointer, pointer, pointer]
        foundation.CFRunLoopRunInMode.argtypes = [pointer, ctypes.c_double, ctypes.c_bool]
        foundation.CFRunLoopStop.argtypes = [pointer]
        foundation.CFRelease.argtypes = [pointer]
        common = pointer.in_dll(foundation, "kCFRunLoopCommonModes")
        default = pointer.in_dll(foundation, "kCFRunLoopDefaultMode")
        tap: dict[str, Any] = {}
        ready, stopped = threading.Event(), threading.Event()

        def received(proxy, kind, event, context):
            if kind >= _TAP_DISABLED:
                graphics.CGEventTapEnable(tap["port"], True)
                return event
            try:
                subtype = graphics.CGEventGetIntegerValueField(event, _FIELD_SUBTYPE) if kind == EVENT_APPKIT else 0
                if handler(int(kind), int(subtype), int(graphics.CGEventGetIntegerValueField(event, _FIELD_TARGET_PID))):
                    return None
            except Exception:
                _log.debug("a front-change event could not be read", exc_info=True)
            return event

        callback = signature(received)

        def run() -> None:
            mask = (1 << EVENT_APPKIT) | (1 << EVENT_LOST) | (1 << EVENT_GAINED)
            port = graphics.CGEventTapCreate(_TAP_ANNOTATED_SESSION, 0, 0, mask, callback, None)
            if not port:
                ready.set()
                return
            source = foundation.CFMachPortCreateRunLoopSource(None, port, 0)
            loop = foundation.CFRunLoopGetCurrent()
            foundation.CFRunLoopAddSource(loop, source, common)
            tap.update(port=port, loop=loop)
            ready.set()
            while not stopped.is_set():
                foundation.CFRunLoopRunInMode(default, 0.25, False)
            graphics.CGEventTapEnable(port, False)
            foundation.CFMachPortInvalidate(port)
            foundation.CFRelease(source)
            foundation.CFRelease(port)

        threading.Thread(target=run, name="computer-use-front-events", daemon=True).start()
        ready.wait(1.0)
        if "loop" not in tap:
            stopped.set()
            return None

        def stop() -> None:
            stopped.set()
            foundation.CFRunLoopStop(tap["loop"])

        return stop


class _WindowsDesktop(_Desktop):
    """Windows: the foreground window stands for its process, and the z-order is set directly."""

    poll = 0.02

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes, self._wintypes = ctypes, wintypes
        handle = ctypes.c_void_p
        user32 = self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._visitor = ctypes.WINFUNCTYPE(wintypes.BOOL, handle, wintypes.LPARAM)
        for function, restype, argtypes in (
                (user32.GetForegroundWindow, handle, []),
                (user32.GetWindowThreadProcessId, wintypes.DWORD, [handle, ctypes.POINTER(wintypes.DWORD)]),
                (user32.SetForegroundWindow, wintypes.BOOL, [handle]),
                (user32.BringWindowToTop, wintypes.BOOL, [handle]),
                (user32.AttachThreadInput, wintypes.BOOL, [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]),
                (user32.IsWindow, wintypes.BOOL, [handle]),
                (user32.IsWindowVisible, wintypes.BOOL, [handle]),
                (user32.IsIconic, wintypes.BOOL, [handle]),
                (user32.GetWindowRect, wintypes.BOOL, [handle, ctypes.POINTER(wintypes.RECT)]),
                (user32.GetWindowLongW, ctypes.c_long, [handle, ctypes.c_int]),
                (user32.SetWindowPos, wintypes.BOOL, [handle, handle, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                                      ctypes.c_int, ctypes.c_uint]),
                (user32.EnumWindows, wintypes.BOOL, [self._visitor, wintypes.LPARAM]),
                (kernel32.GetCurrentThreadId, wintypes.DWORD, []),
                (kernel32.OpenProcess, handle, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
                (kernel32.CloseHandle, wintypes.BOOL, [handle]),
                (kernel32.QueryFullProcessImageNameW, wintypes.BOOL,
                 [handle, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)])):
            function.restype, function.argtypes = restype, argtypes
        try:
            self._dwm = ctypes.WinDLL("dwmapi")
            self._dwm.DwmGetWindowAttribute.argtypes = [handle, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        except (OSError, AttributeError):
            self._dwm = None
        # The window each process last had in front: what giving the front back to it means.
        self._last: dict[int, int] = {}

    def _pid(self, window: int) -> int:
        pid = self._wintypes.DWORD(0)
        self._user32.GetWindowThreadProcessId(window, self._ctypes.byref(pid))
        return int(pid.value)

    def front(self) -> int | None:
        window = self._user32.GetForegroundWindow()
        pid = self._pid(window) if window else 0
        if not pid:
            return None
        self._last[pid] = int(window)
        return pid

    def name(self, pid: int) -> str:
        process = self._kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not process:
            return f"pid {pid}"
        try:
            buffer = self._ctypes.create_unicode_buffer(1024)
            size = self._wintypes.DWORD(len(buffer))
            if not self._kernel32.QueryFullProcessImageNameW(process, 0, buffer, self._ctypes.byref(size)):
                return f"pid {pid}"
            return re.split(r"[\\/]", buffer.value)[-1].rsplit(".", 1)[0] or f"pid {pid}"
        finally:
            self._kernel32.CloseHandle(process)

    def stack(self) -> list[Window]:
        ctypes, user32 = self._ctypes, self._user32
        found: list[Window] = []

        def visit(window, _):
            try:
                if not user32.IsWindowVisible(window) or user32.IsIconic(window):
                    return True
                if user32.GetWindowLongW(window, -20) & 0x80:  # GWL_EXSTYLE, WS_EX_TOOLWINDOW
                    return True
                if self._dwm is not None:
                    cloaked = ctypes.c_int(0)
                    self._dwm.DwmGetWindowAttribute(window, 14, ctypes.byref(cloaked), 4)  # DWMWA_CLOAKED
                    if cloaked.value:
                        return True
                rect = self._wintypes.RECT()
                if user32.GetWindowRect(window, ctypes.byref(rect)) and rect.right > rect.left and rect.bottom > rect.top:
                    found.append((self._pid(window), int(window), (float(rect.left), float(rect.top),
                                                                   float(rect.right - rect.left),
                                                                   float(rect.bottom - rect.top))))
            except Exception:
                return True  # a window that closed while it was being read
            return True

        # Top-level windows come front to back.
        user32.EnumWindows(self._visitor(visit), 0)
        return found

    def activate(self, pid: int) -> bool:
        user32 = self._user32
        window = self._last.get(pid)
        if not window or not user32.IsWindow(window):
            window = next((number for owner, number, _ in self.stack() if owner == pid), None)
        if not window:
            return False
        # Windows lets a process take the foreground only from the thread that has it: join its input queue for the call.
        front = user32.GetForegroundWindow()
        own = self._kernel32.GetCurrentThreadId()
        other = user32.GetWindowThreadProcessId(front, None) if front else 0
        attached = bool(other and other != own and user32.AttachThreadInput(own, other, True))
        try:
            user32.BringWindowToTop(window)
            user32.SetForegroundWindow(window)
        finally:
            if attached:
                user32.AttachThreadInput(own, other, False)
        return int(user32.GetForegroundWindow() or 0) == int(window)

    def raise_window(self, pid: int, window_id: int) -> None:
        # HWND_TOP with SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE: the order alone changes.
        self._user32.SetWindowPos(window_id, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)


class _X11Desktop(_Desktop):
    """Linux on X11, through the window manager's own properties (EWMH).

    ``xprop -spy`` reports the active window and the stacking order each time
    the window manager changes them, so reading the front costs nothing.
    """

    _EVERYWHERE = (0.0, 0.0, 1.0, 1.0)  # frames are not read: every window counts as overlapping

    def __init__(self) -> None:
        self._active = 0
        self._stacking: list[int] = []
        self._pids: dict[int, int] = {}
        self._last: dict[int, int] = {}
        self._spy = subprocess.Popen(["xprop", "-root", "-spy", "_NET_ACTIVE_WINDOW", "_NET_CLIENT_LIST_STACKING"],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        threading.Thread(target=self._read, name="computer-use-front-x11", daemon=True).start()

    def _read(self) -> None:
        for line in self._spy.stdout or ():
            windows = [int(number, 16) for number in re.findall(r"0x[0-9a-fA-F]+", line.split(":", 1)[-1])]
            if line.startswith("_NET_ACTIVE_WINDOW"):
                self._active = windows[0] if windows else 0
            elif line.startswith("_NET_CLIENT_LIST_STACKING"):
                self._stacking = windows

    def _run(self, *arguments: object) -> str | None:
        try:
            done = subprocess.run(["xdotool", *map(str, arguments)], capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    def _pid(self, window: int) -> int:
        if window not in self._pids:
            answer = self._run("getwindowpid", window) or ""
            if not answer.isdigit():
                return 0
            if len(self._pids) > 512:
                self._pids.clear()
            self._pids[window] = int(answer)
        return self._pids[window]

    def front(self) -> int | None:
        window = self._active
        pid = self._pid(window) if window else 0
        if not pid:
            return None
        self._last[pid] = window
        return pid

    def name(self, pid: int) -> str:
        try:
            with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as handle:
                return handle.read().strip() or f"pid {pid}"
        except OSError:
            return f"pid {pid}"

    def stack(self) -> list[Window]:
        # The window manager lists them bottom to top.
        return [(pid, window, self._EVERYWHERE) for window in reversed(self._stacking)
                if (pid := self._pid(window))]

    def activate(self, pid: int) -> bool:
        window = self._last.get(pid) or next((number for owner, number, _ in self.stack() if owner == pid), 0)
        return bool(window) and self._run("windowactivate", window) is not None

    def raise_window(self, pid: int, window_id: int) -> None:
        self._run("windowraise", window_id)


def _x11_session() -> bool:
    if not os.environ.get("DISPLAY") or shutil.which("xprop") is None or shutil.which("xdotool") is None:
        return False
    return os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland" or bool(os.environ.get("OPENPROGRAM_ALLOW_XWAYLAND"))


_desktop: _Desktop | None = None
_desktop_tried = False


def desktop() -> _Desktop | None:
    """This platform's desktop, or ``None`` where the front is not kept."""
    global _desktop, _desktop_tried
    if _desktop is None and not _desktop_tried:
        _desktop_tried = True
        try:
            if sys.platform == "darwin":
                _desktop = _MacDesktop()
            elif sys.platform == "win32":
                _desktop = _WindowsDesktop()
            elif sys.platform.startswith("linux") and _x11_session():
                _desktop = _X11Desktop()
        except Exception:
            _log.debug("the front application cannot be read on this system", exc_info=True)
    return _desktop


# What a backend tells the guard where applications are told apart by process.
_noted: set[int] = set()
_standing = {"forward": 0, "starting": 0}
_standing_lock = threading.Lock()


def note(pid: int) -> None:
    """A backend names a process it operates (Windows and Linux; on macOS the application is asked by name)."""
    if len(_noted) > 512:
        _noted.clear()
    _noted.add(int(pid))


@contextmanager
def _while(kind: str) -> Iterator[None]:
    with _standing_lock:
        _standing[kind] += 1
    try:
        yield
    finally:
        with _standing_lock:
            _standing[kind] -= 1


def forward():
    """A backend brings the operated window forward on purpose, for input the system gives only to the front window.

    The guard stands aside for the length of the block; the backend puts the
    front window and the order back itself before it leaves.
    """
    return _while("forward")


def starting():
    """A backend is starting an application whose process it does not know yet.

    While the block runs, a process that had no window when the step began
    and comes to the front is the one being started.
    """
    return _while("starting")


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def restack(before: list[Window], operated: Operated) -> list[tuple[int, int]]:
    """The windows to raise, in order, so that ``before`` is what the user sees again.

    Activating puts the operated application's windows over the rest.
    Raising, deepest first, each window that stood above the operated
    application's lowest one rebuilds the old order above it; what was under
    it never moved. Windows that touch none of the operated application's
    look the same wherever they stand, and are left alone.
    """
    own = [index for index, (pid, _, _) in enumerate(before) if operated(pid)]
    if not own or own[-1] == 0:
        return []
    frames = [before[index][2] for index in own]
    above = [(pid, window_id) for pid, window_id, frame in before[:own[-1]]
             if operated(pid) or any(_overlap(frame, other) for other in frames)]
    return list(reversed(above))[-MAX_RESTACK:]


def disturbed(before: list[Window], after: list[Window], operated: Operated) -> bool:
    """Whether a window of the operated application now stands over one that stood over it in ``before``."""
    wanted = set(restack(before, operated))
    place = {(pid, window_id): index for index, (pid, window_id, _) in enumerate(after)}
    over: list[int] = []  # where the windows that stood over the one being looked at are now
    for pid, window_id, _ in before:
        here = place.get((pid, window_id))
        if operated(pid) and here is not None and any(here < other for other in over):
            return True
        if (pid, window_id) in wanted and here is not None:
            over.append(here)
    return False


class _Interception:
    """One event tap for all the steps that are keeping the front at the moment."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.keepers: list[_Keeper] = []
        self.stop: Callable[[], None] | None = None

    def join(self, screen: _Desktop, keeper: "_Keeper") -> bool:
        with self.lock:
            if not self.keepers:
                try:
                    self.stop = screen.listen(self.event)
                except Exception:
                    _log.debug("front-change events cannot be held back on this Mac", exc_info=True)
                    self.stop = None
                if self.stop is None:
                    return False
            self.keepers.append(keeper)
            return True

    def leave(self, keeper: "_Keeper") -> None:
        with self.lock:
            if keeper not in self.keepers:
                return
            self.keepers.remove(keeper)
            if not self.keepers and self.stop is not None:
                self.stop()
                self.stop = None

    def event(self, kind: int, subtype: int, pid: int) -> bool:
        # Called inside the tap, with the event waiting: no lock that a step could hold.
        drop = False
        for keeper in list(self.keepers):
            drop = keeper.event(kind, subtype, pid) or drop
        return drop


_interception = _Interception()


class _Keeper:
    def __init__(self, screen: _Desktop, operated: Operated, watch: Watch) -> None:
        self.screen, self.operated, self.watch = screen, operated, watch
        self.user = screen.front()
        self.order: list[Window] = self._stack()
        # The processes that had a window as the step began; one outside them is new.
        self.known = {pid for pid, _, _ in self.order} | ({self.user} if self.order and self.user else set())
        self.returns = 0
        self.until: float | None = None
        self.lock = threading.Lock()
        # Whether front-change events reach event() before the applications they are for.
        self.tapped = False
        self.told = threading.Lock()
        # The user's application was not told that it lost the front; operated ones not told they gained it.
        self.unseen = False
        self.hidden: set[int] = set()
        self.settle_until = 0.0
        self.wake = threading.Event()
        self.suspect: int | None = None

    def _stack(self) -> list[Window]:
        try:
            return self.screen.stack()
        except Exception:
            return []

    def _is_operated(self, pid: int) -> bool:
        try:
            if self.operated(pid):
                return True
        except Exception:
            return False
        return bool(_standing["starting"] and self.known and pid not in self.known)

    def event(self, kind: int, subtype: int, pid: int) -> bool:
        """One window-server event on its way to an application; ``True`` keeps it from arriving.

        The user's application is about to be told that it lost the front to
        an operated one: the front is given back here and the event dropped,
        and so are the operated application's "gained" and "lost" and the
        user's "gained" that follow. Neither application learns of it, so no
        window moves and nothing is redrawn. An event is dropped only once
        the front has really been given back.
        """
        gained = kind == EVENT_GAINED or (kind == EVENT_APPKIT and subtype == APPKIT_ACTIVATED)
        lost = kind == EVENT_LOST or (kind == EVENT_APPKIT and subtype == APPKIT_DEACTIVATED)
        if not gained and not lost:
            return False
        with self.told:
            if pid == self.user:
                if lost:
                    self.unseen = self.unseen or self._give_back_unseen()
                    return self.unseen
                if self.unseen:
                    self.unseen = False
                    return True
                return False
            if gained:
                if self.unseen and self._is_operated(pid):
                    self.hidden.add(pid)
                    return True
                return False
            if pid in self.hidden:
                self.hidden.discard(pid)
                return True
            return False

    def _give_back_unseen(self) -> bool:
        now = self.screen.front()
        if (now is None or self.user is None or now == self.user or self.returns >= MAX_RETURNS
                or not self._is_operated(now)):
            return False
        started = time.monotonic()
        if not self.screen.activate(self.user):
            return False
        self.returns += 1
        self.settle_until = time.monotonic() + SETTLE_SECONDS
        self.wake.set()
        name = self.screen.name(now)
        self.watch.taken.append(Taken(name, time.monotonic() - started))
        _log.info("%s took the front while an agent operated it; given back unseen", name)
        return True

    def _put_back(self) -> None:
        for pid, window_id in restack(self.order, self._is_operated):
            try:
                self.screen.raise_window(pid, window_id)
            except Exception:
                # The order is a courtesy; without Accessibility the front application is still given back.
                break
        self.screen.activate(self.user)

    def check(self) -> None:
        with self.lock:
            if _standing["forward"]:
                # A backend holds the operated window in front on purpose and puts things back itself.
                self.suspect = None
                return
            now = self.screen.front()
            if now is None:
                return
            if now == self.user:
                self.suspect = None
                current = self._stack()
                if time.monotonic() < self.settle_until:
                    # Given back unseen a moment ago: an application that moved its window anyway is answered.
                    if current and disturbed(self.order, current, self._is_operated):
                        self._put_back()
                elif current:
                    self.order = current
                return
            if self.user is None or not self._is_operated(now) or self._is_operated(self.user):
                # The user moved on by themselves, or was in the operated application from the start.
                with self.told:
                    self.user, self.order = now, self._stack()
                    self.unseen = False
                    self.hidden.clear()
                return
            if self.returns >= MAX_RETURNS:
                return
            if self.tapped and self.suspect != now:
                # The tap answers within the same few milliseconds; this is for the time it did not.
                self.suspect = now
                return
            self.returns += 1
            started = time.monotonic()
            name = self.screen.name(now)
            if not self.screen.activate(self.user):
                self.user, self.order = now, self._stack()
                return
            self._put_back()
            self.watch.taken.append(Taken(name, time.monotonic() - started))
            _log.info("%s took the front while an agent operated it; given back to %s", name,
                      self.screen.name(self.user))

    def run(self, stop: threading.Event) -> None:
        try:
            if self.tapped:
                try:
                    self.screen.prepare({pid for pid, _ in restack(self.order, self._is_operated)})
                except Exception:
                    _log.debug("the windows to put back could not be read", exc_info=True)
            while True:
                try:
                    self.check()
                except Exception:
                    _log.debug("keeping the front application failed", exc_info=True)
                    return
                if stop.is_set():
                    if self.until is None:
                        self.until = time.monotonic() + LINGER_SECONDS
                    if time.monotonic() >= self.until:
                        return
                # An application given the front back unseen may still move its own window: watched closely.
                self.wake.wait(SETTLE_POLL_SECONDS if time.monotonic() < self.settle_until else self.screen.poll)
                self.wake.clear()
        finally:
            _interception.leave(self)


def _start(operated: Operated | None, watch: Watch) -> tuple[_Keeper, threading.Event, threading.Thread] | None:
    screen = desktop() if operated is not None else None
    if screen is None:
        return None
    try:
        keeper = _Keeper(screen, operated, watch)
    except Exception:
        _log.debug("the front application could not be read", exc_info=True)
        return None
    if keeper.user is None or keeper._is_operated(keeper.user):
        # Nothing to keep: the user is in the operated application.
        return None
    keeper.tapped = _interception.join(screen, keeper)
    stop = threading.Event()
    thread = threading.Thread(target=keeper.run, args=(stop,), name="computer-use-front", daemon=True)
    thread.start()
    return keeper, stop, thread


@contextmanager
def kept(operated: Operated | None) -> Iterator[Watch]:
    """Run a step with the user's front application and window order kept.

    ``operated(pid)`` says whether a process belongs to an application this
    step operates. The block returns as soon as the step does; the watch goes
    on for :data:`LINGER_SECONDS` on its own thread.
    """
    watch = Watch()
    started = _start(operated, watch)
    try:
        yield watch
    finally:
        if started is not None:
            started[1].set()


def watch_for(operated: Operated, seconds: float) -> threading.Thread | None:
    """Keep the front for ``seconds`` while something outside this call brings an application up.

    For an application that is being started again: nothing here waits for
    it, and it shows its window whenever it is ready.
    """
    started = _start(operated, Watch())
    if started is None:
        return None
    keeper, stop, thread = started
    keeper.until = time.monotonic() + max(0.0, seconds)
    stop.set()
    return thread


def under(path: str) -> Operated:
    """``operated`` for the application whose bundle is ``path``, read from the process table alone."""
    import ctypes

    root = str(path).rstrip("/") + "/"
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    except OSError:
        return lambda pid: False

    def operated(pid: int) -> bool:
        buffer = ctypes.create_string_buffer(4096)
        length = libproc.proc_pidpath(ctypes.c_int(pid), buffer, ctypes.c_uint32(len(buffer)))
        return length > 0 and buffer.value.decode("utf-8", "replace").startswith(root)

    return operated


def bundles(*identifiers: str) -> Operated:
    """``operated`` for applications named by bundle identifier."""
    wanted = {identifier for identifier in identifiers if identifier}

    def operated(pid: int) -> bool:
        if sys.platform != "darwin" or not wanted:
            return False
        import AppKit

        application = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        return application is not None and str(application.bundleIdentifier() or "") in wanted

    return operated


def programs(*names: str) -> Operated:
    """``operated`` for processes named by their executable, without its extension (Windows and Linux)."""
    wanted = {name.lower() for name in names if name}

    def operated(pid: int) -> bool:
        screen = desktop()
        return screen is not None and bool(wanted) and screen.name(pid).lower() in wanted

    return operated


def named(query: str) -> Operated:
    """``operated`` for the application a step names: a name, a bundle identifier, a path or ``pid:<n>``."""
    wanted = str(query or "").strip().lower()

    def operated(pid: int) -> bool:
        if not wanted:
            return False
        if wanted.startswith("pid:"):
            return wanted[4:] == str(pid)
        if sys.platform != "darwin":
            # The backend resolved the name to a process and said so.
            return pid in _noted
        import AppKit

        application = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if application is None:
            return False
        names = {str(application.bundleIdentifier() or "").lower(), str(application.localizedName() or "").lower()}
        url = application.bundleURL()
        if url is not None:
            path = str(url.path())
            names.update({path.lower(), path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()})
        return wanted in names

    return operated


def main(argv: list[str] | None = None) -> int:
    """``front.py <application bundle path> <seconds>``: keep the front while that application comes up.

    The scripts that reinstall the App run this file by itself with the
    system Python, so it needs nothing outside the standard library; the
    window order is put back only where the frameworks for it load.
    """
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2:
        sys.stderr.write(f"{main.__doc__}\n")
        return 2
    thread = watch_for(under(arguments[0]), float(arguments[1]))
    if thread is not None:
        thread.join()
    return 0


__all__ = ["APP_BUNDLES", "Taken", "Watch", "bundles", "desktop", "disturbed", "forward", "kept", "named", "note",
           "programs", "restack", "starting", "under", "watch_for"]

if __name__ == "__main__":
    raise SystemExit(main())

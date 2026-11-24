"""The whole display of this Mac, operated with the real pointer and keyboard.

The ``app`` driver (:mod:`.macos`) reads one window and posts its input to
that window's process, so the user keeps the pointer and the front app.
This module is the other route: it reads a whole display and posts pointer
and key events at the HID tap, where the hardware's own events enter. The
real pointer moves, the event goes to whatever lies under it, keys go to the
front application, and system shortcuts work. That is what the desktop, the
menu bar, system dialogs, drags across windows and windows that ignore
background input need.

It holds no policy. The provider (``providers/local_screen.py``) asks it
what is under a point and what has keyboard focus, decides, and only then
asks it to send. A screenshot leaves out the applications the provider
names, which ScreenCaptureKit does by whole application.

Nothing here prompts for access: the grants are read with the preflight
calls, and a capture or an event without them fails.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
import platform
import threading
import time
from typing import Any, Callable, Iterable

from . import keys as keymap
from .base import BadArgument, ComputerUseError, NotDispatched, check_cancelled
from .window_watch import OVERLAY_TITLE

# Both OpenProgram Apps (stable and Dev) and the worker's runtime share this bundle id prefix.
OWN_PREFIX = "ai.openprogram."
# The menu bar and other system chrome belong to this owner; a point there goes to the front application.
_WINDOW_SERVER = "Window Server"
_SECURE = "AXSecureTextField"
# Window levels: the Dock's, and the menu bar's with its status items.
_DOCK_LAYER = 20
_MENU_BAR_LAYERS = frozenset({24, 25})
# How long an application may take to say what is under a point.
_AX_TIMEOUT = 0.5
MAX_IMAGE_SIDE = 1568
MAX_DRAG_STEPS = 48
TICKS_PER_PAGE = 10
LINES_PER_TICK = 3
PIXELS_PER_EVENT = 40
# Keys that select everything in the focused field, and that submit it.
SELECT_ALL = "super+a"
SUBMIT = "Return"
SYSTEM = "macOS"


@dataclass(frozen=True)
class Display:
    """One display: its id, its top left and size in global points, its pixels per point."""
    id: int
    origin: tuple[float, float]
    size: tuple[float, float]
    backing: float = 1.0
    main: bool = False

    def contains(self, point: tuple[float, float]) -> bool:
        return (self.origin[0] <= point[0] < self.origin[0] + self.size[0]
                and self.origin[1] <= point[1] < self.origin[1] + self.size[1])


@dataclass(frozen=True)
class Owner:
    """The application a point or the keyboard focus belongs to."""
    pid: int
    bundle: str = ""
    name: str = ""

    @property
    def own(self) -> bool:
        """OpenProgram itself: either App, the worker, or this process."""
        return self.bundle.lower().startswith(OWN_PREFIX) or self.pid == os.getpid()

    @property
    def label(self) -> str:
        return self.name or self.bundle or f"process {self.pid}"


def hit_row(rows: Iterable[dict], point: tuple[float, float],
            own: Callable[[int], bool] = lambda pid: False) -> dict | None:
    """The window a pointer event at ``point`` most likely reaches, from the window server's rows, front to back.

    The list has no flag for a window that lets events through, and several
    such windows span the whole display: the Dock's, screen-sharing tools'
    overlays, and the App's own overlay, which draws the agent cursor and the
    control glow over the operated window. So only rows that take clicks as
    a rule count: application windows and panels below the Dock's level, the
    menu bar and its status items, and any window of OpenProgram except its
    overlay, so that OpenProgram is never missed. Transparent and empty rows
    take nothing.
    """
    x, y = point
    for row in rows:
        if row.get("kCGWindowName") == OVERLAY_TITLE or float(row.get("kCGWindowAlpha", 1) or 0) <= 0:
            continue
        bounds = row.get("kCGWindowBounds") or {}
        left, top = float(bounds.get("X", 0)), float(bounds.get("Y", 0))
        width, height = float(bounds.get("Width", 0)), float(bounds.get("Height", 0))
        if width <= 0 or height <= 0 or not (left <= x < left + width and top <= y < top + height):
            continue
        layer = int(row.get("kCGWindowLayer", 0) or 0)
        if layer < _DOCK_LAYER or layer in _MENU_BAR_LAYERS or own(int(row.get("kCGWindowOwnerPID", -1))):
            return row
    return None


def scroll_ticks(direction: str, pages: float, pixels: float) -> tuple[int, int, int, bool]:
    """``(ticks, dy, dx, in_pixels)`` of a wheel scroll; a positive delta reveals content above or to the left."""
    if pixels:
        ticks = max(1, int(math.ceil(pixels / PIXELS_PER_EVENT)))
        step, in_pixels = int(round(pixels / ticks)), True
    else:
        ticks = max(1, int(round(pages * TICKS_PER_PAGE)))
        step, in_pixels = LINES_PER_TICK, False
    dy = {"up": step, "down": -step}.get(direction, 0)
    dx = {"left": step, "right": -step}.get(direction, 0)
    return ticks, dy, dx, in_pixels


def _frameworks():
    from . import macos
    state = macos.support()
    if not state["available"]:
        raise ComputerUseError(state["reason"])
    return macos._Frameworks.get()


class MacScreen:
    """This Mac's displays, read whole, and its real pointer and keyboard."""

    system = SYSTEM
    select_all = SELECT_ALL
    submit = SUBMIT

    def __init__(self) -> None:
        self._lock = threading.Lock()

    # -- what it can do now ---------------------------------------------------

    @staticmethod
    def support() -> dict[str, Any]:
        from . import macos
        return macos.support()

    def grants(self) -> dict[str, bool]:
        """Which grants this process holds, read without asking: screen recording and accessibility."""
        _, ax, cg, _ = _frameworks()
        return {"screen_recording": bool(cg.CGPreflightScreenCaptureAccess()),
                "accessibility": bool(ax.AXIsProcessTrusted())}

    def locked(self) -> bool:
        from . import macos
        return macos._screen_locked()

    def version(self) -> str:
        return platform.mac_ver()[0]

    # -- reading --------------------------------------------------------------

    def displays(self) -> list[Display]:
        """The active displays, main first. Needs no grant."""
        _, _, cg, _ = _frameworks()
        error, ids, count = cg.CGGetActiveDisplayList(32, None, None)
        if error:
            raise ComputerUseError(f"the displays could not be listed (error {error})")
        main = int(cg.CGMainDisplayID())
        found = []
        for display_id in list(ids or [])[:count]:
            bounds = cg.CGDisplayBounds(display_id)
            width, height = float(bounds.size.width), float(bounds.size.height)
            backing = 1.0
            mode = cg.CGDisplayCopyDisplayMode(display_id)
            if mode is not None and cg.CGDisplayModeGetWidth(mode):
                backing = float(cg.CGDisplayModeGetPixelWidth(mode)) / float(cg.CGDisplayModeGetWidth(mode))
            found.append(Display(int(display_id), (float(bounds.origin.x), float(bounds.origin.y)),
                                 (width, height), max(1.0, backing), int(display_id) == main))
        return sorted(found, key=lambda display: not display.main)

    def capture(self, display: Display, scale: float, *, region: tuple[float, float, float, float] | None = None,
                hidden: Callable[[Owner], bool] = lambda owner: False) -> bytes:
        """PNG of ``display`` (or of ``region``, display-relative points) at ``scale`` pixels per point.

        The applications ``hidden`` names are left out whole; what lies beneath
        their windows shows instead. The pointer is not drawn.
        """
        from . import macos
        appkit, _, _, sc = _frameworks()
        content = macos._await(lambda cb: sc.SCShareableContent
                               .getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                                   False, True, cb))
        match = next((item for item in content.displays() if int(item.displayID()) == display.id), None)
        if match is None:
            raise NotDispatched(f"display {display.id} is no longer connected; call list_displays.")
        excluded = [app for app in content.applications()
                    if hidden(Owner(int(app.processID()), str(app.bundleIdentifier() or ""),
                                    str(app.applicationName() or "")))]
        rx, ry, rw, rh = region if region else (0.0, 0.0, *display.size)
        config = sc.SCStreamConfiguration.alloc().init()
        if region:
            config.setSourceRect_(((rx, ry), (rw, rh)))
        config.setWidth_(max(1, int(round(rw * scale))))
        config.setHeight_(max(1, int(round(rh * scale))))
        config.setShowsCursor_(False)
        content_filter = sc.SCContentFilter.alloc().initWithDisplay_excludingApplications_exceptingWindows_(
            match, excluded, [])
        image = macos._await(lambda cb: sc.SCScreenshotManager
                             .captureImageWithFilter_configuration_completionHandler_(content_filter, config, cb))
        bitmap = appkit.NSBitmapImageRep.alloc().initWithCGImage_(image)
        data = bitmap.representationUsingType_properties_(appkit.NSBitmapImageFileTypePNG, {})
        if data is None:
            raise ComputerUseError("the screenshot could not be encoded")
        return bytes(data)

    def _owner(self, pid: int, name: str = "") -> Owner:
        appkit, _, _, _ = _frameworks()
        app = appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            return Owner(pid, "", name)
        return Owner(pid, str(app.bundleIdentifier() or ""), str(app.localizedName() or name))

    def front(self) -> Owner | None:
        """The application keys go to. Read from the process manager: NSWorkspace is stale in a worker."""
        from openprogram.host._platform import front
        desk = front.desktop()
        pid = desk.front() if desk is not None else None
        return self._owner(pid) if pid else None

    def owner_at(self, point: tuple[float, float]) -> Owner | None:
        """The application whose window takes a pointer event at ``point``; the menu bar is the front one's.

        Accessibility answers first: its hit test is the window server's own,
        which passes over windows that let events through. The window list is
        the fallback, and is used as well when Accessibility names the App's
        overlay.
        """
        owner = self._accessible_owner(point)
        if owner is not None:
            return owner
        _, _, cg, _ = _frameworks()
        rows = list(cg.CGWindowListCopyWindowInfo(cg.kCGWindowListOptionOnScreenOnly, cg.kCGNullWindowID) or [])
        row = hit_row(rows, point, own=lambda pid: self._owner(pid).own)
        if row is None:
            return None
        if str(row.get("kCGWindowOwnerName") or "") == _WINDOW_SERVER:
            return self.front()
        return self._owner(int(row.get("kCGWindowOwnerPID", -1)), str(row.get("kCGWindowOwnerName") or ""))

    def _accessible_owner(self, point: tuple[float, float]) -> Owner | None:
        _, ax, _, _ = _frameworks()
        if not ax.AXIsProcessTrusted():
            return None
        system = ax.AXUIElementCreateSystemWide()
        ax.AXUIElementSetMessagingTimeout(system, _AX_TIMEOUT)
        code, element = ax.AXUIElementCopyElementAtPosition(system, float(point[0]), float(point[1]), None)
        if code != 0 or element is None:
            return None
        code, pid = ax.AXUIElementGetPid(element, None)
        if code != 0 or not pid:
            return None
        code, window = ax.AXUIElementCopyAttributeValue(element, "AXWindow", None)
        if code == 0 and window is not None:
            code, title = ax.AXUIElementCopyAttributeValue(window, "AXTitle", None)
            if code == 0 and str(title or "") == OVERLAY_TITLE:
                return None
        return self._owner(int(pid))

    def secure_input(self) -> bool:
        """Whether secure keyboard entry is on: a password field has focus somewhere on this Mac."""
        _, _, cg, _ = _frameworks()
        info = cg.CGSessionCopyCurrentDictionary() or {}
        return bool(info.get("kCGSSessionSecureInputPID"))

    def focus_is_password(self) -> bool:
        """Whether the element with keyboard focus, system-wide, is a password field."""
        _, ax, _, _ = _frameworks()
        system = ax.AXUIElementCreateSystemWide()
        code, element = ax.AXUIElementCopyAttributeValue(system, "AXFocusedUIElement", None)
        if code != 0 or element is None:
            return False
        for name in ("AXRole", "AXSubrole"):
            code, value = ax.AXUIElementCopyAttributeValue(element, name, None)
            if code == 0 and str(value or "") == _SECURE:
                return True
        return False

    def pointer(self) -> tuple[float, float]:
        _, _, cg, _ = _frameworks()
        location = cg.CGEventGetLocation(cg.CGEventCreate(None))
        return float(location.x), float(location.y)

    # -- sending, at the HID tap ----------------------------------------------

    def _post(self, cg, event) -> None:
        cg.CGEventPost(cg.kCGHIDEventTap, event)

    def _mouse(self, cg, kind, point, button, flags: int = 0, click_state: int = 0):
        source = cg.CGEventSourceCreate(cg.kCGEventSourceStateHIDSystemState)
        event = cg.CGEventCreateMouseEvent(source, kind, point, button)
        if flags:
            cg.CGEventSetFlags(event, flags)
        if click_state:
            cg.CGEventSetIntegerValueField(event, cg.kCGMouseEventClickState, click_state)
        self._post(cg, event)

    def _key(self, cg, keycode: int, down: bool, flags: int, text: str = "") -> None:
        source = cg.CGEventSourceCreate(cg.kCGEventSourceStateHIDSystemState)
        event = cg.CGEventCreateKeyboardEvent(source, keycode, down)
        cg.CGEventSetFlags(event, flags)
        if text:
            cg.CGEventKeyboardSetUnicodeString(event, len(text.encode("utf-16-le")) // 2, text)
        self._post(cg, event)

    def _holding(self, cg, flags: int, body: Callable[[], None]) -> None:
        """Hold the modifier keys of ``flags`` down around ``body``, releasing them whatever happens."""
        held, now = [], 0
        try:
            for flag, code in keymap.MODIFIER_KEYCODES.items():
                if flags & flag:
                    now |= flag
                    self._key(cg, code, True, now)
                    held.append((flag, code))
                    time.sleep(0.008)
            body()
        finally:
            for flag, code in reversed(held):
                now &= ~flag
                self._key(cg, code, False, now)
                time.sleep(0.008)

    def move(self, point: tuple[float, float]) -> None:
        _, _, cg, _ = _frameworks()
        with self._lock:
            self._mouse(cg, cg.kCGEventMouseMoved, point, cg.kCGMouseButtonLeft)
        time.sleep(0.02)

    def click(self, point: tuple[float, float], button: str, count: int, modifiers: str = "",
              hold: float = 0.0) -> None:
        _, _, cg, _ = _frameworks()
        try:
            flags = keymap.modifier_flags(modifiers)
        except keymap.KeyNameError as exc:
            raise BadArgument(str(exc)) from None
        down, up, code = {
            "left": (cg.kCGEventLeftMouseDown, cg.kCGEventLeftMouseUp, cg.kCGMouseButtonLeft),
            "right": (cg.kCGEventRightMouseDown, cg.kCGEventRightMouseUp, cg.kCGMouseButtonRight),
            "middle": (cg.kCGEventOtherMouseDown, cg.kCGEventOtherMouseUp, cg.kCGMouseButtonCenter),
        }[button]

        def gesture() -> None:
            self._mouse(cg, cg.kCGEventMouseMoved, point, cg.kCGMouseButtonLeft, flags)
            time.sleep(0.03)
            for click in range(1, count + 1):
                self._mouse(cg, down, point, code, flags, click)
                time.sleep(max(0.03, hold))
                self._mouse(cg, up, point, code, flags, click)
                if click < count:
                    time.sleep(0.06)

        with self._lock:
            self._holding(cg, flags, gesture)

    def drag(self, start: tuple[float, float], end: tuple[float, float], seconds: float) -> None:
        _, _, cg, _ = _frameworks()
        left = cg.kCGMouseButtonLeft
        distance = math.hypot(end[0] - start[0], end[1] - start[1])
        steps = max(2, min(MAX_DRAG_STEPS, int(distance / 12) + 1))
        with self._lock:
            self._mouse(cg, cg.kCGEventMouseMoved, start, left)
            time.sleep(0.03)
            self._mouse(cg, cg.kCGEventLeftMouseDown, start, left, click_state=1)
            try:
                time.sleep(0.12)
                for step in range(1, steps + 1):
                    t = step / steps
                    point = (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
                    self._mouse(cg, cg.kCGEventLeftMouseDragged, point, left, click_state=1)
                    time.sleep(max(0.012, seconds / steps))
                time.sleep(0.08)
            finally:
                # The button never stays down, also when the drag was cancelled halfway.
                self._mouse(cg, cg.kCGEventLeftMouseUp, end, left, click_state=1)

    def scroll(self, point: tuple[float, float] | None, direction: str, pages: float, pixels: float) -> None:
        """Wheel events where the pointer is, after moving it to ``point``: the wheel goes to the window under it."""
        _, _, cg, _ = _frameworks()
        ticks, dy, dx, in_pixels = scroll_ticks(direction, pages, pixels)
        unit = cg.kCGScrollEventUnitPixel if in_pixels else cg.kCGScrollEventUnitLine
        with self._lock:
            if point is not None:
                self._mouse(cg, cg.kCGEventMouseMoved, point, cg.kCGMouseButtonLeft)
                time.sleep(0.03)
            for _ in range(ticks):
                check_cancelled()
                source = cg.CGEventSourceCreate(cg.kCGEventSourceStateHIDSystemState)
                event = cg.CGEventCreateScrollWheelEvent(source, unit, 2, dy, dx)
                self._post(cg, event)
                time.sleep(0.03)

    def press(self, chord: str, hold: float = 0.0) -> None:
        """A key chord in our spelling (``super+shift+z``) to the front application."""
        _, _, cg, _ = _frameworks()
        try:
            parsed = keymap.parse_chord(chord)
        except keymap.KeyNameError as exc:
            raise BadArgument(str(exc)) from None
        flags = parsed.flags & ~next((flag for flag, code in keymap.MODIFIER_KEYCODES.items()
                                      if code == parsed.keycode), 0)

        def key() -> None:
            self._key(cg, parsed.keycode, True, parsed.flags)
            time.sleep(max(0.012, min(hold, 10.0)))
            self._key(cg, parsed.keycode, False, parsed.flags)

        with self._lock:
            self._holding(cg, flags, key)

    def type(self, text: str) -> None:
        """Characters to the front application as typed, a newline as Return and a tab as Tab."""
        _, _, cg, _ = _frameworks()
        with self._lock:
            for index, char in enumerate(text):
                if index % 40 == 0:
                    check_cancelled()
                if char in "\n\t":
                    code = 36 if char == "\n" else 48
                    self._key(cg, code, True, 0)
                    self._key(cg, code, False, 0)
                else:
                    self._key(cg, 0, True, 0, char)
                    self._key(cg, 0, False, 0, char)
                time.sleep(0.004)

    def paste(self, text: str) -> None:
        """``text`` through the clipboard and Command-V; the clipboard is restored unless the user changed it."""
        from . import macos
        appkit, _, _, _ = _frameworks()
        board = appkit.NSPasteboard.generalPasteboard()
        saved = macos._save_pasteboard(board)
        board.clearContents()
        board.setString_forType_(text, appkit.NSPasteboardTypeString)
        written = board.changeCount()
        try:
            self.press("super+v")
            time.sleep(0.6)
        finally:
            if board.changeCount() == written:
                macos._restore_pasteboard(board, saved)


_BACKEND: MacScreen | None = None
_BACKEND_LOCK = threading.Lock()


def backend() -> MacScreen:
    global _BACKEND
    with _BACKEND_LOCK:
        if _BACKEND is None:
            _BACKEND = MacScreen()
        return _BACKEND


__all__ = ["Display", "MacScreen", "OWN_PREFIX", "Owner", "backend", "hit_row", "scroll_ticks"]

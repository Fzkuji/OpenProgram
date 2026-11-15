"""macOS capture for a recording: an event tap, Accessibility lookups and screenshots.

The event tap is listen-only and runs on its own thread's run loop. Its
callback copies the few fields it needs and returns at once; a second
thread looks up elements, watches the focused window and takes
screenshots, so a slow application never stalls the tap.
"""
from __future__ import annotations

import contextlib
import os
import queue
import threading
import time
from typing import Any

from openprogram.programs.tools.computer_use.computer_use_agent.desktop import keys as keymap
from openprogram.programs.tools.computer_use.computer_use_agent.desktop.macos import _Frameworks
from openprogram.programs.tools.computer_use.computer_use_agent.desktop.tree import role_name

from .session import Recording, RecordingError

MAX_IMAGE_SIDE = 1440
MAX_TEXT = 160
SCROLL_GAP = 0.6
POLL_SECONDS = 0.4
VALUE_DELAY = 0.25

# Events in these applications are dropped: OpenProgram itself (its
# recording bar included), credential prompts and password managers. A
# fixed list: what a recording keeps does not follow computer_use.refused_apps.
DROPPED_BUNDLES = frozenset({
    "ai.openprogram.desktop", "ai.openprogram.runtime",
    "com.apple.securityagent", "com.apple.loginwindow", "com.apple.keychainaccess", "com.apple.passwords",
    "com.1password.1password", "com.agilebits.onepassword7", "com.agilebits.onepassword-osx",
    "com.bitwarden.desktop", "com.lastpass.lastpassmacdesktop", "com.dashlane.dashlanephonefinal",
    "org.keepassxc.keepassxc", "com.apple.coreservices.uiagent",
})

_SECURE = "AXSecureTextField"
_TEXT_ROLE_NAMES = ("text field", "text area", "combo box", "search field")
_ELEMENT_ATTRS = ["AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXValue", "AXPlaceholderValue",
                  "AXIdentifier", "AXHelp"]
_COMMAND, _CONTROL, _OPTION, _SHIFT = keymap.COMMAND, keymap.CONTROL, keymap.OPTION, keymap.SHIFT
_ARROWS = {123: "left", 124: "right", 125: "down", 126: "up"}
_SPECIAL = {36: "return", 76: "enter", 48: "tab", 53: "escape", 117: "delete", 115: "home",
            119: "end", 116: "page_up", 121: "page_down", **_ARROWS}
_BACKSPACE = 51
_KEY_NAMES = {**{code: name for name, code in keymap._CHARACTERS.items() if name != " "},
              49: "space", **{code: name for name, code in keymap._NAMED.items()
                              if name in {"return", "tab", "backspace", "escape", "delete", "home", "end",
                                          "page_up", "page_down", "left", "right", "up", "down"}
                              or name.startswith("f")}}

# Quartz constants, by value so the module imports without Quartz.
_LEFT_DOWN, _RIGHT_DOWN, _OTHER_DOWN, _KEY_DOWN, _SCROLL = 1, 3, 25, 10, 22
_TAP_DISABLED = {0xFFFFFFFE, 0xFFFFFFFF}
_CLICK_STATE = 1          # kCGMouseEventClickState
_KEYCODE = 9              # kCGKeyboardEventKeycode
_SCROLL_Y, _SCROLL_X = 11, 12   # kCGScrollWheelEventDeltaAxis1/2


def _clip(value: Any, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def check_access() -> None:
    """Raise :class:`RecordingError` naming the missing grant."""
    try:
        _, ax, cg, _ = _Frameworks.get()
    except ImportError as exc:
        raise RecordingError(f"Recording needs PyObjC: {exc}") from exc
    if not ax.AXIsProcessTrusted():
        raise RecordingError(
            "OpenProgram does not have Accessibility access. Grant it in System Settings > "
            "Privacy & Security > Accessibility, then start the recording again.", "accessibility")
    if not cg.CGPreflightScreenCaptureAccess():
        raise RecordingError(
            "OpenProgram does not have Screen Recording access. Grant it in System Settings > "
            "Privacy & Security > Screen & System Audio Recording, then start the recording again.",
            "screen_recording")


class Capture:
    """Feeds one :class:`Recording` from the macOS input stream."""

    def __init__(self, recording: Recording) -> None:
        check_access()
        self.appkit, self.ax, self.cg, self.sc = _Frameworks.get()
        self.recording = recording
        self.system = self.ax.AXUIElementCreateSystemWide()
        self.queue: queue.Queue = queue.Queue()
        self.stopping = threading.Event()
        self.loop = None
        self.tap = None
        self.own_pids = {os.getpid()}
        self._apps: dict[int, dict] = {}
        self._focus: tuple[int, str] | None = None
        self._scroll: dict | None = None
        self._typing: dict | None = None    # the element typed into, for reading its value
        self._ready = threading.Event()
        self._error = ""
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ life cycle
    def start(self) -> None:
        tap = threading.Thread(target=self._run_tap, name="recording-tap", daemon=True)
        work = threading.Thread(target=self._run_work, name="recording-work", daemon=True)
        self._threads = [tap, work]
        tap.start()
        if not self._ready.wait(5) or self._error:
            self.stopping.set()
            raise RecordingError(self._error or "The input monitor did not start.")
        self._focus = None
        self._poll_focus(initial=True)
        work.start()

    def stop(self) -> None:
        self.stopping.set()
        if self.loop is not None:
            self.cg.CFRunLoopStop(self.loop)
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(3)
        self._flush_scroll()
        self._read_typed_value()

    # ------------------------------------------------------------------ tap thread
    def _run_tap(self) -> None:
        cg = self.cg
        mask = 0
        for kind in (_LEFT_DOWN, _RIGHT_DOWN, _OTHER_DOWN, _KEY_DOWN, _SCROLL):
            mask |= 1 << kind

        def callback(proxy, kind, event, refcon):
            if kind in _TAP_DISABLED:
                if self.tap is not None:
                    cg.CGEventTapEnable(self.tap, True)
                return event
            with contextlib.suppress(Exception):
                self.queue.put(self._copy(kind, event))
            return event

        self.tap = cg.CGEventTapCreate(cg.kCGSessionEventTap, cg.kCGHeadInsertEventTap,
                                       cg.kCGEventTapOptionListenOnly, mask, callback, None)
        if self.tap is None:
            self._error = ("macOS refused to let OpenProgram watch input. Check Accessibility and "
                           "Input Monitoring in System Settings > Privacy & Security.")
            self._ready.set()
            return
        source = cg.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        self.loop = cg.CFRunLoopGetCurrent()
        cg.CFRunLoopAddSource(self.loop, source, cg.kCFRunLoopCommonModes)
        cg.CGEventTapEnable(self.tap, True)
        self._ready.set()
        while not self.stopping.is_set():
            cg.CFRunLoopRunInMode(cg.kCFRunLoopDefaultMode, 0.5, False)
        cg.CGEventTapEnable(self.tap, False)
        cg.CFMachPortInvalidate(self.tap)

    def _copy(self, kind: int, event) -> dict:
        cg = self.cg
        point = cg.CGEventGetLocation(event)
        row = {"kind": kind, "t": self.recording.elapsed_ms(), "point": (float(point.x), float(point.y)),
               "flags": int(cg.CGEventGetFlags(event))}
        if kind in (_LEFT_DOWN, _RIGHT_DOWN, _OTHER_DOWN):
            row["count"] = int(cg.CGEventGetIntegerValueField(event, _CLICK_STATE))
        elif kind == _KEY_DOWN:
            row["code"] = int(cg.CGEventGetIntegerValueField(event, _KEYCODE))
            _, text = cg.CGEventKeyboardGetUnicodeString(event, 8, None, None)
            row["text"] = text or ""
        elif kind == _SCROLL:
            row["dy"] = int(cg.CGEventGetIntegerValueField(event, _SCROLL_Y))
            row["dx"] = int(cg.CGEventGetIntegerValueField(event, _SCROLL_X))
        return row

    # ------------------------------------------------------------------ work thread
    def _run_work(self) -> None:
        last_poll = 0.0
        while not self.stopping.is_set():
            try:
                row = self.queue.get(timeout=0.15)
            except queue.Empty:
                row = None
            try:
                if row is not None:
                    self._handle(row)
                now = time.monotonic()
                if self._scroll and now - self._scroll["last"] >= SCROLL_GAP:
                    self._flush_scroll()
                if self._typing and not self._typing.get("read") and now - self._typing["last"] >= VALUE_DELAY:
                    self._read_typed_value()
                if now - last_poll >= POLL_SECONDS:
                    last_poll = now
                    self._poll_focus()
            except Exception as exc:  # one bad lookup never ends the recording
                self.recording.error = str(exc)[:300]

    def _handle(self, row: dict) -> None:
        kind = row["kind"]
        if kind != _SCROLL:
            self._flush_scroll()
        if kind in (_LEFT_DOWN, _RIGHT_DOWN, _OTHER_DOWN):
            self._read_typed_value()
            self._typing = None
            self._click(row)
        elif kind == _KEY_DOWN:
            self._key(row)
        elif kind == _SCROLL:
            self._scroll_row(row)

    def _click(self, row: dict) -> None:
        x, y = row["point"]
        element = self._element_at(x, y)
        pid = self._pid(element)
        app = self._app(pid)
        if app is None:
            return
        event = {"t": row["t"], "type": "click", "app": app, "window": self._window_title(element),
                 "point": [round(x), round(y)], "count": max(1, row.get("count") or 1),
                 "button": {_LEFT_DOWN: "left", _RIGHT_DOWN: "right"}.get(row["kind"], "middle"),
                 "element": self._describe(element)}
        if row["flags"] & (_COMMAND | _CONTROL | _OPTION | _SHIFT):
            event["modifiers"] = self._modifiers(row["flags"])
        self.recording.add(event, screenshot=lambda: self._screenshot(x, y))

    def _key(self, row: dict) -> None:
        code, flags = row["code"], row["flags"]
        focused = self._focused_element()
        pid = self._pid(focused) if focused is not None else self._focused_pid()
        app = self._app(pid)
        if app is None:
            return
        chord = flags & (_COMMAND | _CONTROL | _OPTION)
        if chord or code in _SPECIAL:
            name = _KEY_NAMES.get(code) or _SPECIAL.get(code) or (row["text"] or f"key{code}").lower()
            keys = "+".join(self._modifiers(flags) + [name]) if chord or flags & _SHIFT else name
            self._read_typed_value()
            self._typing = None
            event = {"t": row["t"], "type": "key", "keys": keys, "app": app,
                     "window": self._window_title(focused)}
            point = row["point"]
            self.recording.add(event, screenshot=lambda: self._screenshot(*point))
            return
        element = self._describe(focused)
        element.pop("value", None)   # the typed text replaces it; it changes with every key
        event = {"t": row["t"], "type": "type", "app": app, "window": self._window_title(focused),
                 "element": element}
        if element.get("secure"):
            event["secure"] = True
        elif code == _BACKSPACE:
            event["backspace"] = True
        elif row["text"] and row["text"].isprintable():
            event["char"] = row["text"]
        else:
            return
        self.recording.add(event)
        if not element.get("secure") and focused is not None and element.get("role", "").startswith(_TEXT_ROLE_NAMES):
            self._typing = {"ref": focused, "element": element, "last": time.monotonic(), "read": False}

    def _read_typed_value(self) -> None:
        """The field's text once typing pauses: what an input method actually entered."""
        typing = self._typing
        if not typing or typing.get("read"):
            return
        typing["read"] = True
        value = self._attr(typing["ref"], "AXValue")
        if isinstance(value, str):
            self.recording.set_typing_value(typing["element"], value)

    def _scroll_row(self, row: dict) -> None:
        now = time.monotonic()
        if self._scroll is None:
            x, y = row["point"]
            element = self._element_at(x, y)
            app = self._app(self._pid(element))
            if app is None:
                return
            self._scroll = {"t": row["t"], "app": app, "window": self._window_title(element),
                            "element": self._describe(element), "dx": 0, "dy": 0, "last": now}
        self._scroll["dx"] += row.get("dx", 0)
        self._scroll["dy"] += row.get("dy", 0)
        self._scroll["last"] = now

    def _flush_scroll(self) -> None:
        burst, self._scroll = self._scroll, None
        if not burst:
            return
        dx, dy = burst.pop("dx"), burst.pop("dy")
        burst.pop("last")
        if abs(dy) >= abs(dx):
            direction = "up" if dy > 0 else "down"
        else:
            direction = "left" if dx > 0 else "right"
        self.recording.add({**burst, "type": "scroll", "direction": direction})

    def _poll_focus(self, initial: bool = False) -> None:
        pid = self._focused_pid()
        if pid <= 0:
            return
        app_element = self.ax.AXUIElementCreateApplication(pid)
        window = self._attr(app_element, "AXFocusedWindow")
        title = _clip(self._attr(window, "AXTitle") if window is not None else "", 200)
        focus = (pid, title)
        if focus == self._focus:
            return
        self._focus = focus
        app = self._app(pid)
        if app is None:
            return
        frame = self._frame(window) if window is not None else None
        if frame:
            cx, cy = frame[0] + frame[2] / 2, frame[1] + frame[3] / 2
        else:
            loc = self.cg.CGEventGetLocation(self.cg.CGEventCreate(None))
            cx, cy = float(loc.x), float(loc.y)
        event = {"type": "switch", "app": app, "window": title}
        if initial:
            event["t"] = 0
        self.recording.add(event, screenshot=lambda: self._screenshot(cx, cy, marker=False))

    # ------------------------------------------------------------------ Accessibility
    def _attr(self, element, name):
        if element is None:
            return None
        code, value = self.ax.AXUIElementCopyAttributeValue(element, name, None)
        return value if code == 0 else None

    def _attrs(self, element, names) -> dict:
        code, values = self.ax.AXUIElementCopyMultipleAttributeValues(element, names, 0, None)
        if code != 0 or values is None:
            return {}
        out = {}
        for name, value in zip(names, values):
            if type(value).__name__ == "AXValueRef":
                continue
            out[name] = value
        return out

    def _frame(self, element):
        position, size = self._attr(element, "AXPosition"), self._attr(element, "AXSize")
        if position is None or size is None:
            return None
        ok, point = self.ax.AXValueGetValue(position, self.ax.kAXValueCGPointType, None)
        ok2, extent = self.ax.AXValueGetValue(size, self.ax.kAXValueCGSizeType, None)
        if not (ok and ok2):
            return None
        return float(point.x), float(point.y), float(extent.width), float(extent.height)

    def _element_at(self, x: float, y: float):
        code, element = self.ax.AXUIElementCopyElementAtPosition(self.system, x, y, None)
        return element if code == 0 else None

    def _focused_element(self):
        return self._attr(self.system, "AXFocusedUIElement")

    def _focused_pid(self) -> int:
        app = self._attr(self.system, "AXFocusedApplication")
        return self._pid(app) if app is not None else -1

    def _pid(self, element) -> int:
        if element is None:
            return -1
        code, pid = self.ax.AXUIElementGetPid(element, None)
        return int(pid) if code == 0 else -1

    def _app(self, pid: int) -> dict | None:
        """``{"name", "bundle"}`` of a process, or None when its events are dropped."""
        if pid <= 0 or pid in self.own_pids:
            return None
        if pid not in self._apps:
            running = self.appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            bundle = str(running.bundleIdentifier() or "") if running is not None else ""
            name = str(running.localizedName() or "") if running is not None else ""
            self._apps[pid] = {"name": name or bundle or f"process {pid}", "bundle": bundle}
        app = self._apps[pid]
        if app["bundle"].lower() in DROPPED_BUNDLES or app["bundle"].lower().startswith("ai.openprogram."):
            return None
        return app

    def _window_title(self, element) -> str:
        window = self._attr(element, "AXWindow") if element is not None else None
        if window is None:
            pid = self._pid(element) if element is not None else self._focused_pid()
            if pid > 0:
                window = self._attr(self.ax.AXUIElementCreateApplication(pid), "AXFocusedWindow")
        return _clip(self._attr(window, "AXTitle"), 200) if window is not None else ""

    def _describe(self, element) -> dict:
        """Role, name, value and identifier of an element, with up to two labelled ancestors."""
        if element is None:
            return {}
        values = self._attrs(element, _ELEMENT_ATTRS)
        role = str(values.get("AXRole") or "")
        subrole = str(values.get("AXSubrole") or "")
        secure = _SECURE in (role, subrole)
        out: dict[str, Any] = {"role": role_name(role, subrole if subrole != _SECURE else "")}
        for key, attr in (("title", "AXTitle"), ("description", "AXDescription"),
                          ("placeholder", "AXPlaceholderValue"), ("identifier", "AXIdentifier"),
                          ("help", "AXHelp")):
            text = _clip(values.get(attr))
            if text:
                out[key] = text
        if secure:
            out["secure"] = True
        else:
            value = values.get("AXValue")
            if isinstance(value, str) and _clip(value):
                out["value"] = _clip(value)
        ancestors = []
        parent = self._attr(element, "AXParent")
        for _ in range(8):
            if parent is None or len(ancestors) >= 2:
                break
            names = self._attrs(parent, ["AXRole", "AXTitle", "AXDescription"])
            parent_role = str(names.get("AXRole") or "")
            if parent_role in ("AXWindow", "AXApplication"):
                break
            label = _clip(names.get("AXTitle")) or _clip(names.get("AXDescription"))
            if label:
                ancestors.append(f'{role_name(parent_role)} "{label}"')
            parent = self._attr(parent, "AXParent")
        if ancestors:
            out["ancestors"] = ancestors
        return out

    @staticmethod
    def _modifiers(flags: int) -> list[str]:
        return [name for bit, name in ((_CONTROL, "ctrl"), (_OPTION, "alt"), (_SHIFT, "shift"),
                                       (_COMMAND, "cmd")) if flags & bit]

    # ------------------------------------------------------------------ screenshots
    def _screenshot(self, x: float, y: float, *, marker: bool = True) -> bytes | None:
        """JPEG of the display under the point, without OpenProgram's windows."""
        sc, appkit = self.sc, self.appkit
        content = _await(lambda cb: sc.SCShareableContent
                         .getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(True, True, cb))
        display = None
        for candidate in content.displays():
            f = candidate.frame()
            if f.origin.x <= x < f.origin.x + f.size.width and f.origin.y <= y < f.origin.y + f.size.height:
                display = candidate
                break
        display = display or (content.displays()[0] if content.displays() else None)
        if display is None:
            return None
        excluded = [app for app in content.applications()
                    if int(app.processID()) in self.own_pids
                    or str(app.bundleIdentifier() or "").lower().startswith("ai.openprogram.")]
        frame = display.frame()
        width, height = float(frame.size.width), float(frame.size.height)
        backing = max([1.0] + [float(s.backingScaleFactor()) for s in appkit.NSScreen.screens() or []])
        scale = min(backing, MAX_IMAGE_SIDE / max(width, height, 1))
        config = sc.SCStreamConfiguration.alloc().init()
        config.setWidth_(max(1, int(round(width * scale))))
        config.setHeight_(max(1, int(round(height * scale))))
        config.setShowsCursor_(True)
        content_filter = sc.SCContentFilter.alloc().initWithDisplay_excludingApplications_exceptingWindows_(
            display, excluded, [])
        image = _await(lambda cb: sc.SCScreenshotManager
                       .captureImageWithFilter_configuration_completionHandler_(content_filter, config, cb))
        if marker:
            image = self._mark(image, (x - frame.origin.x) * config.width() / width,
                               (y - frame.origin.y) * config.height() / height)
        bitmap = appkit.NSBitmapImageRep.alloc().initWithCGImage_(image)
        data = bitmap.representationUsingType_properties_(appkit.NSBitmapImageFileTypeJPEG,
                                                          {appkit.NSImageCompressionFactor: 0.7})
        return bytes(data) if data is not None else None

    def _mark(self, image, px: float, py: float):
        """The image with a ring where the pointer was."""
        cg = self.cg
        w, h = cg.CGImageGetWidth(image), cg.CGImageGetHeight(image)
        context = cg.CGBitmapContextCreate(None, w, h, 8, 0, cg.CGColorSpaceCreateDeviceRGB(),
                                           cg.kCGImageAlphaPremultipliedLast)
        if context is None:
            return image
        cg.CGContextDrawImage(context, cg.CGRectMake(0, 0, w, h), image)
        radius = max(10.0, w / 90)
        center_y = h - py
        cg.CGContextSetRGBStrokeColor(context, 1.0, 1.0, 1.0, 0.95)
        cg.CGContextSetLineWidth(context, radius / 3 + 2)
        cg.CGContextStrokeEllipseInRect(context, cg.CGRectMake(px - radius, center_y - radius, 2 * radius, 2 * radius))
        cg.CGContextSetRGBStrokeColor(context, 0.94, 0.27, 0.27, 1.0)
        cg.CGContextSetLineWidth(context, radius / 3)
        cg.CGContextStrokeEllipseInRect(context, cg.CGRectMake(px - radius, center_y - radius, 2 * radius, 2 * radius))
        return cg.CGBitmapContextCreateImage(context) or image


def _await(start, seconds: float = 5.0):
    done, result = threading.Event(), []

    def callback(value, error):
        result.extend((value, error))
        done.set()

    start(callback)
    if not done.wait(seconds):
        raise RecordingError("Screen capture timed out")
    if result[1] is not None or result[0] is None:
        raise RecordingError(f"Screen capture failed: {result[1]}")
    return result[0]


__all__ = ["Capture", "DROPPED_BUNDLES", "check_access"]

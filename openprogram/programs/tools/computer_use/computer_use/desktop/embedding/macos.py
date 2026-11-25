"""macOS: the tab shows a live capture of the window and passes input back to it.

The App captures the window (ScreenCaptureKit, through Chromium's window
capture) and draws it in the tab. This module does the rest:

* **Placement.** The window is moved and sized through Accessibility to
  the tab's rectangle on screen. Under the App it is hidden by the tab that
  shows it; its menus, pop-ups and input-method panels are separate windows
  the app opens next to it, so they appear where the tab shows the window.
  If the app raises the window over the App, it covers the tab exactly.
* **Input.** Pointer, wheel and key events from the tab are posted to the
  window's process through SkyLight with the window's id stamped on them,
  the same route the computer_use backend takes, so the app is not
  activated and the shared cursor does not move. They go through one queue
  so they arrive in the order the user made them.
* **Watching.** A view that only shows the window records it for input
  and leaves it where it is, at its size; a point in the view maps to the
  screen through the window's current frame.
* **Giving back.** Detaching puts the window back where it was.

Every window id is a window server id (``CGWindowID``).
"""
from __future__ import annotations

import os
import queue
import threading
import time
from typing import Any

from . import EmbedError
from .keycodes import mac_keycode, modifier_flags, typed_text

_MIN_SIZE = (120, 80)
_OWN_PREFIX = "ai.openprogram."
# DOM button numbers: 0 primary, 1 auxiliary (middle), 2 secondary.
_BUTTON_NUMBER = {0: 0, 1: 2, 2: 1}
_QUEUE_LIMIT = 2000


def _desktop():
    from ..macos import frameworks
    return frameworks


def _computer():
    from ..macos import computer
    return computer.backend()


def _ax_value(kind: str, a: float, b: float):
    _, ax, cg, _ = _desktop()._frameworks_cached()
    if kind == "point":
        return ax.AXValueCreate(ax.kAXValueCGPointType, cg.CGPoint(a, b))
    return ax.AXValueCreate(ax.kAXValueCGSizeType, cg.CGSize(a, b))


def window_rows(cg, window_id: int | None = None) -> list[dict]:
    if window_id:
        return list(cg.CGWindowListCopyWindowInfo(cg.kCGWindowListOptionIncludingWindow, window_id) or [])
    options = cg.kCGWindowListOptionOnScreenOnly | cg.kCGWindowListExcludeDesktopElements
    return list(cg.CGWindowListCopyWindowInfo(options, cg.kCGNullWindowID) or [])


def candidate_rows(rows: list[dict], exclude_pids: set[int]) -> list[dict]:
    """The rows that are ordinary app windows a person could embed: layer 0,
    visible, not tiny, not the App's own."""
    out = []
    for row in rows:
        bounds = row.get("kCGWindowBounds") or {}
        pid = int(row.get("kCGWindowOwnerPID", -1))
        if (row.get("kCGWindowLayer") != 0 or float(row.get("kCGWindowAlpha", 1) or 0) <= 0
                or pid in exclude_pids or pid <= 0
                or float(bounds.get("Width", 0)) < _MIN_SIZE[0] or float(bounds.get("Height", 0)) < _MIN_SIZE[1]):
            continue
        out.append(row)
    return out


class MacEmbedder:
    mode = "stream"
    place_input = True
    view_input = True

    def __init__(self) -> None:
        self.attached: dict[int, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._events: queue.Queue = queue.Queue(maxsize=_QUEUE_LIMIT)
        self._pump: threading.Thread | None = None

    # ------------------------------------------------------------------ listing
    def list_windows(self, exclude_pids: set[int]) -> list[dict[str, Any]]:
        macos = _desktop()
        appkit, _, cg, _ = macos._frameworks_cached()
        computer = _computer()
        excluded = set(exclude_pids) | {os.getpid()}
        out = []
        apps: dict[int, Any] = {}
        for row in candidate_rows(window_rows(cg), excluded):
            pid = int(row["kCGWindowOwnerPID"])
            if pid not in apps:
                apps[pid] = appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            app = apps[pid]
            if app is None or app.activationPolicy() != appkit.NSApplicationActivationPolicyRegular:
                continue
            bundle = str(app.bundleIdentifier() or "")
            if bundle.startswith(_OWN_PREFIX):
                continue
            name = str(app.localizedName() or row.get("kCGWindowOwnerName") or "")
            path = str(app.bundleURL().path()) if app.bundleURL() else ""
            bounds = row.get("kCGWindowBounds") or {}
            out.append({
                "id": int(row["kCGWindowNumber"]),
                "pid": pid,
                "app": name,
                "title": str(row.get("kCGWindowName") or ""),
                "icon": computer._icon(path) if computer is not None and path else "",
                "bounds": [float(bounds.get(k, 0)) for k in ("X", "Y", "Width", "Height")],
            })
        return out

    # ------------------------------------------------------------------ attach
    def _element(self, pid: int, window_id: int):
        macos = _desktop()
        _, ax, _, _ = macos._frameworks()
        computer = _computer()
        for window in computer._windows(ax.AXUIElementCreateApplication(pid)):
            if computer._window_id(window, pid, computer._frame(window)) == window_id:
                return computer, window
        raise EmbedError("That window is not open on this desktop any more, or it is on another Space.")

    def attach(self, window_id: int, host: dict[str, Any]) -> dict[str, Any]:
        macos = _desktop()
        macos._unlocked()
        appkit, _, cg, _ = macos._frameworks()
        rows = window_rows(cg, window_id)
        if not rows or int(rows[0].get("kCGWindowNumber", 0)) != window_id:
            raise EmbedError("That window is closed.")
        pid = int(rows[0]["kCGWindowOwnerPID"])
        app = appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is not None and app.isHidden():
            app.unhide()
        with self._lock:
            record = self.attached.get(window_id)
            if record is None or record.get("watched"):
                computer, element = self._element(pid, window_id)
                if computer._attr(element, "AXMinimized"):
                    computer._set(element, "AXMinimized", False)
                    time.sleep(0.3)
                record = {"pid": pid, "element": element, "original": computer._frame(element),
                          "frame": computer._frame(element), "app": str(rows[0].get("kCGWindowOwnerName") or "")}
                self.attached[window_id] = record
            self._place(record, host)
            return self._describe(window_id, record)

    def watch(self, window_id: int) -> dict[str, Any]:
        """Record the window for input and state without moving, sizing or unhiding it."""
        macos = _desktop()
        macos._unlocked()
        _, _, cg, _ = macos._frameworks()
        with self._lock:
            record = self.attached.get(window_id)
            if record is not None:
                return self._describe(window_id, record)
        rows = window_rows(cg, window_id)
        if not rows or int(rows[0].get("kCGWindowNumber", 0)) != window_id:
            raise EmbedError("That window is closed.")
        pid = int(rows[0]["kCGWindowOwnerPID"])
        computer, element = self._element(pid, window_id)
        with self._lock:
            record = self.attached.get(window_id)
            if record is None:
                record = {"pid": pid, "element": element, "original": None, "watched": True,
                          "frame": computer._frame(element), "app": str(rows[0].get("kCGWindowOwnerName") or "")}
                self.attached[window_id] = record
            return self._describe(window_id, record)

    def _place(self, record: dict[str, Any], host: dict[str, Any]) -> None:
        if not host.get("visible", True):
            return
        x, y, width, height = host["rect"]
        if width < 1 or height < 1:
            return
        computer = _computer()
        element = record["element"]
        frame = computer._frame(element)
        target = (round(x), round(y), round(width), round(height))
        if frame is not None and all(abs(a - b) < 1 for a, b in zip(frame, target)):
            record["frame"] = frame
            return
        # Position first so the new size fits the screen, then again in case
        # the app kept the window on screen while it grew.
        computer._set(element, "AXPosition", _ax_value("point", target[0], target[1]))
        computer._set(element, "AXSize", _ax_value("size", target[2], target[3]))
        computer._set(element, "AXPosition", _ax_value("point", target[0], target[1]))
        record["frame"] = computer._frame(element) or frame

    def _describe(self, window_id: int, record: dict[str, Any]) -> dict[str, Any]:
        frame = record.get("frame")
        title = _computer()._attr(record["element"], "AXTitle")
        return {"mode": self.mode, "id": window_id, "pid": record["pid"], "app": record.get("app", ""),
                "title": str(title or ""), "frame": list(frame) if frame else None, "open": True,
                "placed": not record.get("watched"), "input": True}

    def _record(self, window_id: int) -> dict[str, Any]:
        record = self.attached.get(window_id)
        if record is None:
            raise EmbedError("That window is not embedded.")
        return record

    def layout(self, window_id: int, host: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            record = self._record(window_id)
            if record.get("watched"):
                raise EmbedError("That window is only shown here, not placed.")
            self._place(record, host)
            return self._describe(window_id, record)

    def state(self, window_id: int) -> dict[str, Any]:
        _, _, cg, _ = _desktop()._frameworks_cached()
        rows = window_rows(cg, window_id)
        if not rows or int(rows[0].get("kCGWindowNumber", 0)) != window_id:
            with self._lock:
                self.attached.pop(window_id, None)
            return {"id": window_id, "open": False}
        with self._lock:
            record = self.attached.get(window_id)
            if record is not None:
                record["frame"] = _computer()._frame(record["element"]) or record.get("frame")
                return self._describe(window_id, record)
        return {"id": window_id, "open": True, "title": str(rows[0].get("kCGWindowName") or "")}

    def detach(self, window_id: int) -> None:
        with self._lock:
            record = self.attached.pop(window_id, None)
        if record is None or record.get("watched") or not record.get("original"):
            return
        x, y, width, height = record["original"]
        computer = _computer()
        try:
            computer._set(record["element"], "AXSize", _ax_value("size", width, height))
            computer._set(record["element"], "AXPosition", _ax_value("point", x, y))
        except Exception:
            pass

    # ------------------------------------------------------------------ input
    def input(self, window_id: int, events: list[dict[str, Any]]) -> None:
        if window_id not in self.attached:
            raise EmbedError("That window is not embedded.")
        for event in events:
            try:
                self._events.put_nowait((window_id, event))
            except queue.Full:
                # A stalled app: drop moves rather than block the socket.
                if event.get("type") not in ("move", "wheel"):
                    raise EmbedError("The window is not taking input.")
        with self._lock:
            if self._pump is None or not self._pump.is_alive():
                self._pump = threading.Thread(target=self._run, name="embedded-window-input", daemon=True)
                self._pump.start()

    def _run(self) -> None:
        while True:
            try:
                window_id, event = self._events.get(timeout=30)
            except queue.Empty:
                with self._lock:
                    if self._events.empty():
                        self._pump = None
                        return
                continue
            record = self.attached.get(window_id)
            if record is None:
                continue
            try:
                self._post(window_id, record, event)
            except Exception:
                # One event that cannot be posted must not stop the rest.
                pass

    def _post(self, window_id: int, record: dict[str, Any], event: dict[str, Any]) -> None:
        from ..macos.skylight import SUBTYPE_DEFAULT, SUBTYPE_TOUCH, skylight
        macos = _desktop()
        _, _, cg, _ = macos._frameworks_cached()
        sky = skylight()
        pid = record["pid"]
        kind = event.get("type")
        flags = modifier_flags(event.get("mods"))
        if kind == "focus":
            if sky.available:
                sky.focus_without_raise(pid, window_id)
            return
        if kind in ("key", "text"):
            source = cg.CGEventSourceCreate(cg.kCGEventSourceStateHIDSystemState)

            def key(code: int, down: bool, text: str = "", key_flags: int = 0) -> None:
                ev = cg.CGEventCreateKeyboardEvent(source, code, down)
                cg.CGEventSetFlags(ev, key_flags)
                if text:
                    cg.CGEventKeyboardSetUnicodeString(ev, len(text.encode("utf-16-le")) // 2, text)
                if sky.available:
                    sky.post(pid, ev, keyboard=True)
                else:
                    cg.CGEventPostToPid(pid, ev)

            if kind == "text":
                for char in str(event.get("text") or "")[:4000]:
                    key(0, True, char)
                    key(0, False, char)
                return
            code = mac_keycode(str(event.get("code") or ""))
            if code is None:
                text = typed_text(str(event.get("key") or ""), event.get("mods"))
                if text and event.get("down"):
                    key(0, True, text, flags)
                    key(0, False, text, flags)
                return
            key(code, bool(event.get("down")), typed_text(str(event.get("key") or ""), event.get("mods")), flags)
            return
        if kind not in ("move", "down", "up", "wheel") or not sky.available:
            return
        if kind == "down":
            # The window may have moved on its own since the last layout.
            record["frame"] = _computer()._frame(record["element"]) or record.get("frame")
            sky.focus_without_raise(pid, window_id)
            # The same pacing as the computer_use background click: the focus
            # settles first, and AppKit hit-tests against the last pointer move.
            time.sleep(0.05)
            record["primer"] = True
        frame = record.get("frame")
        if not frame:
            return
        local = (float(event.get("x", 0)), float(event.get("y", 0)))
        point = cg.CGPoint(frame[0] + local[0], frame[1] + local[1])
        source = cg.CGEventSourceCreate(cg.kCGEventSourceStateHIDSystemState)
        button = _BUTTON_NUMBER.get(int(event.get("button", 0) or 0), 0)
        click_state, subtype = 0, SUBTYPE_DEFAULT
        if kind == "wheel":
            ev = cg.CGEventCreateScrollWheelEvent(source, cg.kCGScrollEventUnitPixel, 2,
                                                  int(round(-float(event.get("dy", 0)))),
                                                  int(round(-float(event.get("dx", 0)))))
            cg.CGEventSetLocation(ev, point)
        elif kind == "move":
            held = int(event.get("buttons", 0) or 0)
            if held & 1:
                etype, code = cg.kCGEventLeftMouseDragged, cg.kCGMouseButtonLeft
            elif held & 2:
                etype, code, button = cg.kCGEventRightMouseDragged, cg.kCGMouseButtonRight, 1
            elif held & 4:
                etype, code, button = cg.kCGEventOtherMouseDragged, cg.kCGMouseButtonCenter, 2
            else:
                etype, code = cg.kCGEventMouseMoved, cg.kCGMouseButtonLeft
            click_state = 1 if held else 0
            ev = cg.CGEventCreateMouseEvent(source, etype, point, code)
        else:
            down = kind == "down"
            etype, code = {
                0: (cg.kCGEventLeftMouseDown if down else cg.kCGEventLeftMouseUp, cg.kCGMouseButtonLeft),
                1: (cg.kCGEventRightMouseDown if down else cg.kCGEventRightMouseUp, cg.kCGMouseButtonRight),
                2: (cg.kCGEventOtherMouseDown if down else cg.kCGEventOtherMouseUp, cg.kCGMouseButtonCenter),
            }[button]
            ev = cg.CGEventCreateMouseEvent(source, etype, point, code)
            click_state = max(1, min(int(event.get("clicks", 1) or 1), 3))
            subtype = SUBTYPE_TOUCH
        if flags:
            cg.CGEventSetFlags(ev, flags)
        group = sky.next_group() if kind == "down" else getattr(sky, "_group", 0) or 1
        if record.pop("primer", False):
            move = cg.CGEventCreateMouseEvent(source, cg.kCGEventMouseMoved, point, cg.kCGMouseButtonLeft)
            sky.stamp_mouse(move, pid=pid, window_id=window_id, click_state=0, button=0,
                            subtype=SUBTYPE_TOUCH, group=group, location=local)
            sky.post(pid, move)
            time.sleep(0.012)
        if kind == "up":
            # A control's mouseDown polls for the mouseUp; one too soon is dropped.
            wait = 0.028 - (time.monotonic() - record.get("down_at", 0.0))
            if wait > 0:
                time.sleep(wait)
        sky.stamp_mouse(ev, pid=pid, window_id=window_id, click_state=click_state, button=button,
                        subtype=subtype, group=group, location=local)
        sky.post(pid, ev)
        if kind == "down":
            record["down_at"] = time.monotonic()


__all__ = ["MacEmbedder", "candidate_rows", "window_rows"]

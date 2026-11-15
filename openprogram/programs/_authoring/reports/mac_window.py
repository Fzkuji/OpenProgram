"""One exact macOS window, captured and operated in the background for the WeChat readers.

``window_session(app, window_id)`` binds a ScreenCaptureKit window and its
Accessibility window, holds a per-window lock for as long as the session
lasts, and offers:

- ``observe()``: a PNG of the window (``img_path``) and a fresh table of its
  controls (``elements``, ``element_bounds``), secure text fields left out;
- ``dispatch({"call": ..., "args": {"target": <control id>, ...}})``:
  ``window_press``, ``window_select``, ``window_set_text`` or
  ``window_scroll`` on one control of that table, through Accessibility only;
- ``validate()``: the application has not exited or restarted and the window
  is still there and not minimized.

There is never foreground activation, global mouse or keyboard input, or
clipboard use. A window that exposes no Accessibility window of its own
(WeChat for macOS can list only its AXApplication element) is observed by
screenshot only (``capture_only``) and every action on it is refused.
"""

from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import platform
import shutil
import tempfile
import threading
import time
import uuid


class WindowUnavailable(RuntimeError):
    """A window operation cannot run safely in the background."""


def _cancel():
    from openprogram.runtime.conversation.run_control import check_cancelled

    check_cancelled()


def window_support():
    """Whether this host can capture and operate windows: macOS 14+, the frameworks, and both permissions."""
    missing = [name for name in ("AppKit", "ApplicationServices", "Quartz", "ScreenCaptureKit")
               if importlib.util.find_spec(name) is None]
    result = {"available": False, "missing_dependencies": missing}
    if platform.system() != "Darwin" or missing:
        return {**result, "reason": "macOS window dependencies unavailable"}
    import ScreenCaptureKit

    if not hasattr(ScreenCaptureKit, "SCScreenshotManager"):
        return {**result, "reason": "macOS 14 or newer is required"}
    from openprogram.host.system_access import capability_registry, report

    access = report()
    required = {spec.id: spec for spec in capability_registry() if "computer_use:desktop" in spec.operations}
    rows = [row for row in access["capabilities"] if row.get("id") in required]
    present = {row["id"] for row in rows}
    for identifier, spec in required.items():
        if identifier not in present:
            rows.append({"id": identifier, "status": "unknown", "label": spec.label,
                         "detail": "Required desktop permission is missing from the report.",
                         "instruction": "Check the execution host's desktop access."})
    blocked = [row for row in rows if row.get("status") != "granted"]
    if not required:
        return {**result, "system_access": rows, "reason": "Desktop permission requirements unavailable"}
    return {**result, "available": not blocked, "system_access": rows,
            "reason": " ".join(row["label"] + ": " + row["detail"] + " " + row["instruction"] for row in blocked)}


class MacWindow:
    # True when the exact captured window has no associated Accessibility
    # window: the session observes it by screenshot and refuses every action.
    capture_only = False

    def __init__(self, app_name, window_id=None):
        self._deadline = time.monotonic() + 15
        if platform.system() != "Darwin":
            raise WindowUnavailable("Background application windows require macOS")
        if not isinstance(app_name, str) or (window_id is not None and (type(window_id) is not int or window_id <= 0)):
            raise WindowUnavailable("Invalid application or window identifier")
        self.check_permissions()
        try:
            import AppKit
            import ApplicationServices
            import Quartz
            import ScreenCaptureKit
        except ImportError as exc:
            raise WindowUnavailable("macOS window dependencies unavailable") from exc
        self.appkit, self.ax, self.cg, self.sc = AppKit, ApplicationServices, Quartz, ScreenCaptureKit
        # Initialize the WindowServer connection before asynchronous capture in
        # a headless worker. This does not activate an application.
        AppKit.NSApplication.sharedApplication()
        content = self._await(lambda cb: self.sc.SCShareableContent
                              .getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                                  True, False, cb))
        matches = [w for w in content.windows() if w.owningApplication()
                   and app_name in (w.owningApplication().applicationName(), w.owningApplication().bundleIdentifier())
                   and w.windowLayer() == 0 and w.frame().size.width > 1
                   and (window_id is None or w.windowID() == window_id)]
        if len(matches) != 1:
            raise WindowUnavailable(f"Window not unique: requested={app_name!r}/{window_id}, matches={len(matches)}")
        self.window = matches[0]
        self.pid = int(self.window.owningApplication().processID())
        app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(self.pid)
        if app is None or app.launchDate() is None:
            raise WindowUnavailable("Cannot determine target application launch identity")
        self.identity = {"pid": self.pid, "window_id": int(self.window.windowID()),
                         "app_name": str(self.window.owningApplication().applicationName()),
                         "bundle_id": str(self.window.owningApplication().bundleIdentifier()),
                         # NSDate.__str__ can return a bridged string subclass.
                         "launch_time": str(str(app.launchDate()))}
        self.root = self.ax.AXUIElementCreateApplication(self.pid)
        self.ax.AXUIElementSetMessagingTimeout(self.root, 2.0)
        bounds = self.window.frame()
        candidates = []
        for window in list(self.attr(self.root, "AXWindows") or []):
            frame = self.element_frame(window)
            if frame and max(abs(frame["x"] - bounds.origin.x), abs(frame["y"] - bounds.origin.y),
                             abs(frame["width"] - bounds.size.width),
                             abs(frame["height"] - bounds.size.height)) < 2:
                candidates.append(window)
        if len(candidates) > 1:
            raise WindowUnavailable("Cannot uniquely associate the captured window with Accessibility "
                                    f"(window_id={self.identity['window_id']}, matches={len(candidates)})")
        # Without a matching Accessibility window the ScreenCaptureKit identity
        # is still exact: the window is read by screenshot and has no controls.
        self.ax_window = candidates[0] if candidates else None
        self.capture_only = self.ax_window is None
        self.elements = {}
        self.element_bounds = {}
        self.validate()
        self._deadline = None

    def check_permissions(self):
        support = window_support()
        if not support["available"]:
            raise WindowUnavailable(support["reason"])

    def _await(self, start):
        completed, result = threading.Event(), []

        def callback(value, error):
            result.extend((value, error))
            completed.set()
        start(callback)
        deadline = time.monotonic() + 8
        while not completed.wait(0.02):
            _cancel()
            if time.monotonic() >= deadline:
                raise WindowUnavailable("Window capture timed out")
        if result[1] or result[0] is None:
            raise WindowUnavailable(f"Window capture failed: {result[1]}")
        return result[0]

    def attr(self, element, name):
        _cancel()
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise WindowUnavailable("Window Accessibility observation timed out")
        code, value = self.ax.AXUIElementCopyAttributeValue(element, name, None)
        return value if code == 0 else None

    def element_frame(self, element):
        position, size = self.attr(element, "AXPosition"), self.attr(element, "AXSize")
        if position is None or size is None:
            return None
        ok1, point = self.ax.AXValueGetValue(position, self.ax.kAXValueCGPointType, None)
        ok2, extent = self.ax.AXValueGetValue(size, self.ax.kAXValueCGSizeType, None)
        if not ok1 or not ok2:
            return None
        return {"x": float(point.x), "y": float(point.y), "width": float(extent.width), "height": float(extent.height)}

    def validate(self):
        _cancel()
        self.check_permissions()
        app = self.appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(self.pid)
        if not app or app.isTerminated() or str(app.launchDate()) != self.identity["launch_time"]:
            raise WindowUnavailable("Target application exited or restarted")
        if self.capture_only:
            self._validate_captured_window()
            return
        windows = self.attr(self.root, "AXWindows")
        if windows is None:
            raise WindowUnavailable("Cannot read target application's Accessibility windows; closure not confirmed")
        if self.ax_window not in windows:
            raise WindowUnavailable("Target window is no longer in the application's Accessibility window list")
        if self.attr(self.ax_window, "AXMinimized"):
            raise WindowUnavailable("Target window is minimized")

    def _validate_captured_window(self):
        """Confirm the exact captured window still exists and is on screen, without Accessibility."""
        window_id = self.identity["window_id"]
        rows = self.cg.CGWindowListCopyWindowInfo(self.cg.kCGWindowListOptionIncludingWindow, window_id) or []
        row = next((r for r in rows if r.get("kCGWindowNumber") == window_id), None)
        if row is None or row.get("kCGWindowOwnerPID") != self.pid:
            raise WindowUnavailable("Target window no longer exists")
        if not row.get("kCGWindowIsOnscreen"):
            # Without an Accessibility window, minimized cannot be told apart
            # from another Space, and a minimized capture can be stale.
            raise WindowUnavailable("Target window is not on screen (minimized or on another Space)")

    def observe(self):
        """Capture the window to a new private PNG and read its controls; the caller owns the file."""
        self._deadline = time.monotonic() + 15
        try:
            return self._observe()
        finally:
            self._deadline = None

    def _observe(self):
        self.validate()
        config = self.sc.SCStreamConfiguration.alloc().init()
        config.setWidth_(int(self.window.frame().size.width))
        config.setHeight_(int(self.window.frame().size.height))
        config.setShowsCursor_(False)
        content_filter = self.sc.SCContentFilter.alloc().initWithDesktopIndependentWindow_(self.window)
        image = self._await(lambda cb: self.sc.SCScreenshotManager
                            .captureImageWithFilter_configuration_completionHandler_(content_filter, config, cb))
        bitmap = self.appkit.NSBitmapImageRep.alloc().initWithCGImage_(image)
        data = bitmap.representationUsingType_properties_(self.appkit.NSBitmapImageFileTypePNG, {})
        directory = tempfile.mkdtemp(prefix="openprogram-window-")
        try:
            path = Path(directory) / "observation.png"
            path.write_bytes(bytes(data))
            self.elements = {}
            self.element_bounds = {}
            pending = [] if self.capture_only else [(self.ax_window, 0)]
            while pending and len(self.elements) < 400:
                _cancel()
                element, depth = pending.pop()
                role = str(self.attr(element, "AXRole") or "")
                # Secure text fields and their values are never read.
                if role == "AXSecureTextField" or self.attr(element, "AXSubrole") == "AXSecureTextField":
                    continue
                token = uuid.uuid4().hex[:12]
                _, actions = self.ax.AXUIElementCopyActionNames(element, None)
                code_value, writable = self.ax.AXUIElementIsAttributeSettable(element, "AXValue", None)
                allowed = [str(a) for a in (actions or []) if a in ("AXPress", "AXScrollUpByPage", "AXScrollDownByPage")]
                if role == "AXRow":
                    select_code, selectable = self.ax.AXUIElementIsAttributeSettable(element, "AXSelected", None)
                    if select_code == 0 and selectable:
                        allowed.append("AXSelect")
                can_write = code_value == 0 and bool(writable) and role in ("AXTextField", "AXTextArea", "AXComboBox")
                self.elements[token] = (element, allowed, can_write)
                self.element_bounds[token] = self.element_frame(element)
                if depth < 20:
                    pending.extend((child, depth + 1) for child in reversed(self.attr(element, "AXChildren") or []))
            self.validate()
            return {"img_path": str(path)}
        except BaseException:
            # A failed observation never hands out a path, so its image goes here.
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def dispatch(self, plan):
        if self.capture_only:
            name = str((plan or {}).get("call") or "action")
            return {"success": False, "action_status": "refused", "reason_code": "window_controls_unavailable",
                    "target": self.identity, "control_id": None,
                    "error": f"{name} refused: this window is observed by screenshot only."}
        self._deadline = time.monotonic() + 10
        try:
            return self._dispatch(plan)
        finally:
            self._deadline = None

    def _dispatch(self, plan):
        self.validate()
        name, args = plan.get("call"), plan.get("args") or {}
        token = args.get("target")
        if token not in self.elements:
            raise WindowUnavailable("Unknown or stale control id; observe the window again")
        element, allowed, writable = self.elements[token]
        ancestor = element
        for _ in range(64):
            if ancestor == self.ax_window:
                break
            ancestor = self.attr(ancestor, "AXParent")
            if ancestor is None:
                raise WindowUnavailable("Control no longer belongs to the target window")
        else:
            raise WindowUnavailable("Cannot verify control ownership")
        bounds = self.element_frame(element)
        if name == "window_select" and "AXSelect" in allowed:
            code = self.ax.AXUIElementSetAttributeValue(element, "AXSelected", True)
        elif name == "window_set_text" and writable:
            value = args.get("text")
            if not isinstance(value, str) or len(value) > 10000:
                raise WindowUnavailable("Text must be a string of at most 10000 characters")
            code = self.ax.AXUIElementSetAttributeValue(element, "AXValue", value)
        else:
            action = {"window_press": "AXPress",
                      "window_scroll": {"up": "AXScrollUpByPage", "down": "AXScrollDownByPage"}.get(
                          args.get("direction"))}.get(name)
            if action not in allowed:
                raise WindowUnavailable("This control does not support the requested background action")
            code = self.ax.AXUIElementPerformAction(element, action)
        if code != 0:
            raise WindowUnavailable(f"Accessibility action failed ({code}); no foreground fallback")
        self.element_bounds[token] = bounds
        return {"success": True, "action_status": "applied", "target": self.identity,
                "control_id": token, "control_bounds": bounds}


@contextmanager
def window_session(app_name, window_id=None):
    """Bind one window and hold its lock: one background operation per window at a time."""
    import fcntl

    window = MacWindow(app_name, window_id)
    directory = Path(tempfile.gettempdir()) / f"openprogram-window-locks-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise WindowUnavailable("Unsafe window lock directory")
    fd = os.open(directory / f"{window.pid}-{window.identity['window_id']}.lock",
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WindowUnavailable("Another operation currently owns this window") from exc
        yield window
    finally:
        os.close(fd)


__all__ = ["MacWindow", "WindowUnavailable", "window_session", "window_support"]

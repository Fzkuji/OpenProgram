"""macOS backend for the ``computer_use`` tool.

It reads one application window as a screenshot plus an indexed
Accessibility tree, and acts on it in the background, the way Codex's
macOS computer use does: the user's frontmost app, key window and cursor
are left alone.

* Element actions (press, focus, set value, select text, secondary
  actions, page scrolling) go through the Accessibility API.
* Keyboard input is posted to the application's process.
* Pointer input at a coordinate (clicks, drags, wheel scrolling) is posted
  to the process through SkyLight with the stamps a real gesture carries,
  so the window takes it without being raised. See :mod:`.skylight`.

Coordinates the model gives are pixels of the last screenshot of that
window. They are kept relative to the window: a step converts them to
screen points against where the window is at that moment
(:func:`screen_point`), and each posted pointer event carries the point
both on screen and inside the window (:func:`window_location`), which is
where the receiving app hit-tests it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib.util
import math
import os
from pathlib import Path
import platform
import subprocess
import threading
import time
from typing import Any

from . import audio
from . import keys as keymap
from . import ocr
from . import window_watch
from .base import StaleElement
from .base import (CUE_LEAD_SECONDS, HOVER_SECONDS, INCLUDE, MAX_COPY_CHARS, MAX_TEXT_CHARS, OCR_SPARSE_NODES,
                   AudioClip, ComputerUseError, NotAnswering, RefusedApps, refuse_password_field, NotDispatched, Observation, Report,
                   check_cancelled as _check_cancelled,
                   click_args, click_words as _click_words, find_text, menu_key as _menu_key, menu_path,
                   save_recording as _save_recording, scroll_args, seconds_arg as _seconds,
                   window_node_count as _window_node_count)
from .processes import running_applications
from .skylight import SUBTYPE_DEFAULT, SUBTYPE_TOUCH, skylight
from .tree import _BIDI_MARKS, Node, Snapshot, TRANSPARENT_ROLES, diff, number, rebind, render, role_name, snapshot
import logging

_log = logging.getLogger(__name__)


# OpenProgram's own windows, refused where they could show an approval card.
OWN_BUNDLES = frozenset({"ai.openprogram.desktop", "ai.openprogram.runtime"})

# The system Open and Save dialogs (NSOpenPanel / NSSavePanel). A sandboxed
# app's dialog is drawn by the openAndSavePanelService process: Accessibility
# reaches into it and window-routed clicks land, but keys posted to the app
# never arrive there. On screen it is a window of the app with the bounds of
# an off-screen window of the service, which holds its Accessibility tree.
_FILE_PANELS = frozenset({"open-panel", "save-panel"})
_PANEL_SERVICE = "com.apple.appkit.xpc.openAndSavePanelService"
_DIALOG_SUBROLES = frozenset({"AXDialog", "AXSystemDialog"})
# What the dialog's Cancel button reads when its window has no AXCancelButton.
_CANCEL_TITLES = frozenset({"cancel", "取消", "abbrechen", "annuler", "cancelar", "annulla", "キャンセル", "취소"})
FILE_PANEL_NOTE = ("This is the system file dialog (Open or Save). To open a file, call open_file(app, path) with its "
                   "absolute path: it cancels this dialog and opens the file directly. macOS runs the dialog in a "
                   "separate process: clicks (element_index or x,y) and set_value reach it in the background, keys "
                   "and typed text do not, and its file list is only in the screenshot and the text read off it.")
FILE_PANEL_OPEN_NOTE = ("The app has the system file dialog open, so keys sent to this window do not arrive. To open "
                        "a file, call open_file(app, path) with its absolute path; get_app_state(window=<the "
                        "dialog's title>) reads the dialog.")

# Opening one of these, or a file with its execute bit, runs a program, and
# a terminal runs whatever document it is handed.
_LAUNCHING_SUFFIXES = frozenset({
    ".app", ".command", ".tool", ".sh", ".bash", ".zsh", ".csh", ".terminal", ".workflow", ".action",
    ".pkg", ".mpkg", ".prefpane", ".fileloc", ".webloc", ".inetloc",
})
_SHELL_BUNDLES = frozenset({
    "com.apple.terminal", "com.googlecode.iterm2", "dev.warp.warp-stable", "net.kovidgoyal.kitty",
    "org.alacritty", "com.mitchellh.ghostty", "co.zeit.hyper", "com.github.wez.wezterm",
})
OPEN_TIMEOUT = 20.0

APP_DIRECTORIES = (
    "/Applications", "/Applications/Utilities", "/System/Applications",
    "/System/Applications/Utilities", "/System/Library/CoreServices/Applications",
    str(Path.home() / "Applications"),
)

MAX_NODES = 1500
MAX_DEPTH = 60
MAX_IMAGE_SIDE = 1568
MAX_DRAG_STEPS = 24
# A cell lies this many levels at most below the row that selects it.
_ROW_DEPTH = 3
# After an action the tool waits for the window to settle: until two quick
# reads agree (at most SETTLE_MAX seconds), or up to BUSY_MAX seconds while
# a progress or busy indicator is on screen.
SETTLE_MIN = 0.3
SETTLE_MAX = 1.5
BUSY_MAX = 5.0
QUICK_NODES = 400
# An app handles one Accessibility request at a time and answers when it is
# done. A request that changes the app (an action, a new value) waits
# ACTION_TIMEOUT seconds for that answer, one check of whether the app
# answers at all waits ANSWER_TIMEOUT, and a call waits up to UNANSWERED_MAX
# seconds for an app that does not answer before it gives up.
ACTION_TIMEOUT = 1.0
ANSWER_TIMEOUT = 0.5
UNANSWERED_MAX = 10.0
_BUSY_ROLES = frozenset({"AXBusyIndicator", "AXProgressIndicator"})
# Wheel notches per page: ten notches of three lines cover about one window.
TICKS_PER_PAGE = 10
LINES_PER_TICK = 3
PIXELS_PER_EVENT = 40
# Longest wait for a page to finish scrolling an element into view.
SCROLL_SETTLE_MAX = 0.8
# Scans of the running apps before an app counts as not running (see _running_match).
_MATCH_ATTEMPTS = 3

_ERROR_TYPE = 5        # kAXValueAXErrorType
_INVALID_ELEMENT = -25202
# kAXErrorCannotComplete. After the whole timeout it means the app got the
# request and has not answered yet; at once, that the request could not be made.
_NOT_ANSWERED = -25204
_SECURE = "AXSecureTextField"
_TEXT_ROLES = frozenset({"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"})
_SETTABLE_ROLES = _TEXT_ROLES | {"AXSlider", "AXIncrementor", "AXStepper", "AXDateField", "AXColorWell"}
_TOGGLE_ROLES = frozenset({"AXCheckBox", "AXRadioButton", "AXSwitch", "AXToggle"})
# Typing at one of these would land in whatever field holds keyboard focus.
_NOT_TEXT_ROLES = frozenset({
    "AXButton", "AXPopUpButton", "AXMenuButton", "AXCheckBox", "AXRadioButton", "AXSlider",
    "AXMenuItem", "AXMenuBarItem", "AXLink", "AXImage", "AXStaticText", "AXDisclosureTriangle",
    "AXIncrementor", "AXStepper", "AXTabGroup", "AXToolbar",
})
_ATTRS = ["AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXValue", "AXPlaceholderValue",
          "AXEnabled", "AXFocused", "AXSelected", "AXExpanded", "AXChildren", "AXHelp"]
_BUTTONS = {"left": 0, "right": 1, "middle": 2}


def support() -> dict[str, Any]:
    """Whether this host can run the backend, without prompting for access."""
    if platform.system() != "Darwin":
        return {"available": False, "reason": "computer_use currently supports macOS only"}
    missing = [name for name in ("AppKit", "ApplicationServices", "Quartz", "ScreenCaptureKit")
               if importlib.util.find_spec(name) is None]
    if missing:
        return {"available": False, "reason": "missing PyObjC frameworks: " + ", ".join(missing)}
    return {"available": True, "reason": ""}


class _Frameworks:
    _lock = threading.Lock()
    _loaded = None

    @classmethod
    def get(cls):
        with cls._lock:
            if cls._loaded is None:
                import AppKit
                import ApplicationServices
                import Quartz
                import ScreenCaptureKit
                # A headless worker needs its WindowServer connection before
                # an asynchronous capture. This activates nothing.
                AppKit.NSApplication.sharedApplication()
                cls._loaded = (AppKit, ApplicationServices, Quartz, ScreenCaptureKit)
            return cls._loaded


def _frameworks():
    state = support()
    if not state["available"]:
        raise ComputerUseError(state["reason"])
    appkit, ax, cg, sc = _Frameworks.get()
    if not ax.AXIsProcessTrusted():
        raise ComputerUseError(
            "OpenProgram does not have Accessibility access. Grant it in System Settings > "
            "Privacy & Security > Accessibility, then try again.")
    return appkit, ax, cg, sc


def _frameworks_cached():
    return _Frameworks.get()


def _screen_locked() -> bool:
    """Whether the user's session is locked.

    macOS answers no Accessibility query about a window while the screen is
    locked, so every application then reads as having no windows.
    """
    try:
        session = _Frameworks.get()[2].CGSessionCopyCurrentDictionary()
    except Exception:
        return False
    return bool(session) and bool(session.get("CGSSessionScreenIsLocked"))


def _unlocked() -> None:
    """Stop a read or a step while the screen is locked, before anything is asked of the app."""
    if _screen_locked():
        raise NotDispatched(
            "The Mac's screen is locked. macOS lets no application window be read or operated while it is "
            "locked; the applications and their windows are still open. Continue once the user has unlocked "
            "the screen.")


def _appkit():
    """AppKit alone, for finding applications: that needs no Accessibility grant."""
    state = support()
    if not state["available"]:
        raise ComputerUseError(state["reason"])
    return _Frameworks.get()[0]


@dataclass
class WindowState:
    """What the model last saw of one application window."""
    pid: int
    window_title: str
    window_id: int = 0
    elements: dict[int, Any] = field(default_factory=dict)
    nodes: list[Node] = field(default_factory=list)
    by_index: dict[int, Node] = field(default_factory=dict)
    snapshot: Snapshot | None = None
    origin: tuple[float, float] = (0.0, 0.0)
    size: tuple[float, float] = (0.0, 0.0)
    scale: float = 1.0           # screenshot pixels per screen point
    has_screenshot: bool = False
    sparse: bool = False         # the tree shows little of the window, so reads add OCR text and shapes
    text_boxes: list[ocr.TextBox] = field(default_factory=list)
    shapes: list[ocr.Shape] = field(default_factory=list)
    # Every window of the app at that read, so a later read can tell one that opened since.
    window_keys: frozenset = frozenset()


class MacComputer:
    """One backend per worker; state is kept per (session, pid)."""

    def __init__(self) -> None:
        self._states: dict[tuple[str, int], WindowState] = {}
        self._lock = threading.RLock()
        self._icons: dict[str, str] = {}
        self._recordings: dict[str, audio.Recording] = {}
        # Keeps the operated window under a live capture so macOS marks it,
        # and tells the App where it is for the control overlay.
        self._watch = window_watch.WindowWatch(report=_report_control)
        # pid -> (window ids searched for, the elements found for them)
        self._remote: dict[int, tuple[frozenset[int], dict[int, Any]]] = {}
        # Out-of-process file dialogs: pid -> whether it is the panel service;
        # the service's window id -> the app window that shows it; app pid ->
        # the file dialogs on screen for it now, as (host window id, bounds).
        self._services: dict[int, bool] = {}
        self._panel_ids: dict[int, int] = {}
        self._live_panels: dict[int, list[tuple[int, tuple[float, float, float, float]]]] = {}

    # ------------------------------------------------------------------ AX helpers
    def _attr(self, element, name):
        _, ax, _, _ = _frameworks_cached()
        code, value = ax.AXUIElementCopyAttributeValue(element, name, None)
        return value if code == 0 else None

    def _attrs(self, element, names):
        _, ax, _, _ = _frameworks_cached()
        code, values = ax.AXUIElementCopyMultipleAttributeValues(element, names, 0, None)
        if code != 0 or values is None:
            return {}
        out = {}
        for name, value in zip(names, values):
            if type(value).__name__ == "AXValueRef" and ax.AXValueGetType(value) == _ERROR_TYPE:
                continue
            out[name] = value
        return out

    def _actions(self, element) -> list[str]:
        _, ax, _, _ = _frameworks_cached()
        code, names = ax.AXUIElementCopyActionNames(element, None)
        return [str(n) for n in names or []] if code == 0 else []

    def _settable(self, element, name) -> bool:
        _, ax, _, _ = _frameworks_cached()
        code, value = ax.AXUIElementIsAttributeSettable(element, name, None)
        return code == 0 and bool(value)

    def _frame(self, element) -> tuple[float, float, float, float] | None:
        _, ax, _, _ = _frameworks_cached()
        values = self._attrs(element, ["AXPosition", "AXSize"])
        if "AXPosition" not in values or "AXSize" not in values:
            return None
        ok1, point = ax.AXValueGetValue(values["AXPosition"], ax.kAXValueCGPointType, None)
        ok2, size = ax.AXValueGetValue(values["AXSize"], ax.kAXValueCGSizeType, None)
        if not ok1 or not ok2:
            return None
        return float(point.x), float(point.y), float(size.width), float(size.height)

    def _set(self, element, name, value) -> int:
        _, ax, _, _ = _frameworks_cached()
        return self._taken(ax.AXUIElementSetAttributeValue, element, name, value)

    def _perform(self, element, action) -> int:
        _, ax, _, _ = _frameworks_cached()
        return self._taken(ax.AXUIElementPerformAction, element, action)

    def _taken(self, request, element, *args) -> int:
        """Send a request that changes the app (an action, a new value); 0 when the app took it.

        The call returns once the app has handled the request. A handler that
        outlasts the wait (a button that saves a file, a command that runs a
        dialog, a menu that stays open) makes it return kAXErrorCannotComplete,
        although the app received the request once and is carrying it out. That
        counts as taken: sending it again, or clicking instead, would do it
        twice. The same code at once is a request that could not be made.
        """
        _, ax, _, _ = _frameworks_cached()
        ax.AXUIElementSetMessagingTimeout(element, ACTION_TIMEOUT)
        started = time.monotonic()
        code = request(element, *args)
        if code == _NOT_ANSWERED and time.monotonic() - started >= ACTION_TIMEOUT * 0.9:
            return 0
        return code

    def _answers(self, pid: int) -> bool:
        """Whether the app answers Accessibility requests now: False only when one went unanswered."""
        _, ax, _, _ = _frameworks_cached()
        root = ax.AXUIElementCreateApplication(pid)
        ax.AXUIElementSetMessagingTimeout(root, ANSWER_TIMEOUT)
        started = time.monotonic()
        code, _ = ax.AXUIElementCopyAttributeValue(root, "AXRole", None)
        return not (code == _NOT_ANSWERED and time.monotonic() - started >= ANSWER_TIMEOUT * 0.9)

    def _await_answer(self, app) -> None:
        """Wait for ``app`` to answer again; NotAnswering once it has stayed silent for UNANSWERED_MAX seconds.

        A busy app answers no read (its windows then look closed) and keeps a
        step sent to it for later. Requests are handled in order, so the answer
        to this one comes after the app has finished what it was sent before,
        or while it shows the dialog or menu that step opened.
        """
        pid = int(app.processIdentifier())
        started = time.monotonic()
        while not self._answers(pid):
            _check_cancelled()
            waited = time.monotonic() - started
            if waited >= UNANSWERED_MAX:
                raise NotAnswering(str(app.localizedName() or f"pid {pid}"), waited)

    # ------------------------------------------------------------------ apps
    def _window_rows(self) -> list[dict]:
        _, _, cg, _ = _frameworks()
        return list(cg.CGWindowListCopyWindowInfo(
            cg.kCGWindowListOptionAll | cg.kCGWindowListExcludeDesktopElements, cg.kCGNullWindowID) or [])

    def _app_rows(self) -> list[dict[str, Any]]:
        appkit, _, _, _ = _frameworks()
        windows: dict[int, int] = {}
        for row in self._window_rows():
            if row.get("kCGWindowLayer") == 0 and row.get("kCGWindowAlpha", 1) > 0:
                pid = int(row.get("kCGWindowOwnerPID", -1))
                windows[pid] = windows.get(pid, 0) + 1
        running = []
        refused = RefusedApps(OWN_BUNDLES)
        for app in running_applications(appkit):
            if app.activationPolicy() != appkit.NSApplicationActivationPolicyRegular:
                continue
            bundle = str(app.bundleIdentifier() or "")
            running.append({
                "name": str(app.localizedName() or ""), "bundle_id": bundle,
                "pid": int(app.processIdentifier()), "active": bool(app.isActive()),
                "hidden": bool(app.isHidden()), "windows": windows.get(int(app.processIdentifier()), 0),
                "path": str(app.bundleURL().path()) if app.bundleURL() else "",
                "refused": refused.hits(bundle),
            })
        return running

    def _installed(self) -> list[dict[str, str]]:
        appkit = _appkit()
        manager = appkit.NSFileManager.defaultManager()
        seen, rows = set(), []
        for directory in APP_DIRECTORIES:
            try:
                names = sorted(os.listdir(directory))
            except OSError:
                continue
            for name in names:
                if not name.endswith(".app"):
                    continue
                path = os.path.join(directory, name)
                bundle = appkit.NSBundle.bundleWithPath_(path)
                bundle_id = str(bundle.bundleIdentifier() or "") if bundle else ""
                if not bundle_id or bundle_id.lower() in seen:
                    continue
                seen.add(bundle_id.lower())
                display = str(manager.displayNameAtPath_(path) or name)
                if display.endswith(".app"):
                    display = display[:-4]
                rows.append({"name": name[:-4], "display_name": display, "bundle_id": bundle_id, "path": path})
        return rows

    def list_apps(self) -> str:
        running = self._app_rows()
        running_ids = {row["bundle_id"].lower() for row in running}
        lines = ["Running applications:"]
        for row in sorted(running, key=lambda r: (not r["active"], r["name"].lower())):
            state = []
            if row["active"]:
                state.append("frontmost")
            if row["hidden"]:
                state.append("hidden")
            state.append(f"{row['windows']} window" + ("" if row["windows"] == 1 else "s"))
            if row["refused"]:
                state.append("not controllable")
            lines.append(f"- {row['name']} ({row['bundle_id'] or 'no bundle id'}, pid {row['pid']}; {', '.join(state)})")
        refused = RefusedApps(OWN_BUNDLES)
        installed = [row for row in self._installed() if row["bundle_id"].lower() not in running_ids
                     and not refused.hits(row["bundle_id"])]
        if installed:
            lines.append("")
            lines.append("Installed, not running (get_app_state launches them in the background):")
            for row in installed:
                label = row["display_name"] if row["display_name"] == row["name"] else f"{row['display_name']} / {row['name']}"
                lines.append(f"- {label} ({row['bundle_id']})")
        return "\n".join(lines)

    def _running_match(self, query: str):
        # NSRunningApplication's lookup by process id now and then answers nil
        # for a live app (about 1 in 8000 lookups while other threads look up
        # too), so one scan can miss an app that is running; a miss is only
        # believed after the scan misses again.
        for attempt in range(_MATCH_ATTEMPTS):
            if attempt:
                time.sleep(0.05)
            app = self._running_match_once(query)
            if app is not None:
                return app
        return None

    def _running_match_once(self, query: str):
        appkit = _appkit()
        q = query.strip().lower()
        pid = int(q[4:]) if q.startswith("pid:") and q[4:].isdigit() else None
        for app in running_applications(appkit):
            if app.isTerminated():
                continue
            if pid is not None:
                if int(app.processIdentifier()) == pid:
                    return app
                continue
            names = {str(app.bundleIdentifier() or "").lower(), str(app.localizedName() or "").lower()}
            url = app.bundleURL()
            if url is not None:
                path = str(url.path())
                names.update({path.lower(), Path(path).stem.lower()})
            if q in names:
                return app
        return None

    def app_key(self, query: str) -> str:
        """One key for an application however it is named (a name, a bundle id, a path); "" when it is not running."""
        if not isinstance(query, str) or not query.strip():
            return ""
        app = self._running_match_once(query)
        if app is None:
            return ""
        return str(app.bundleIdentifier() or "") or f"pid:{int(app.processIdentifier())}"

    def resolve_app(self, query: str, *, launch: bool = True):
        """The running application for ``query``, launched in the background if needed."""
        if not isinstance(query, str) or not query.strip():
            raise ComputerUseError("app is required: a name, bundle id or .app path from list_apps")
        app = self._running_match(query)
        if app is None:
            # A refused app says so whether or not it is running.
            target = self._installed_match(query)
            if target is not None:
                self._refuse(target["bundle_id"], target["display_name"])
            if not launch:
                raise NotDispatched(f"{query!r} is not running")
            if target is None:
                raise ComputerUseError(f"No application matches {query!r}. Call list_apps to see what is available.")
            subprocess.run(["/usr/bin/open", "-g", "-b", target["bundle_id"]], check=False,
                           capture_output=True, timeout=20)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                _check_cancelled()
                app = self._running_match(target["bundle_id"])
                if app is not None and app.isFinishedLaunching():
                    break
                time.sleep(0.2)
            if app is None:
                raise ComputerUseError(f"{target['display_name']} did not start")
            self._wait_for_window(int(app.processIdentifier()), 5)
        self._refuse(str(app.bundleIdentifier() or ""), str(app.localizedName() or ""))
        return app

    def _installed_match(self, query: str) -> dict[str, str] | None:
        q = query.strip().lower()
        if q.endswith(".app") and os.path.isdir(query):
            appkit = _appkit()
            bundle = appkit.NSBundle.bundleWithPath_(query)
            if bundle and bundle.bundleIdentifier():
                return {"bundle_id": str(bundle.bundleIdentifier()), "display_name": Path(query).stem, "path": query}
        for row in self._installed():
            if q in {row["bundle_id"].lower(), row["name"].lower(), row["display_name"].lower(), row["path"].lower()}:
                return row
        return None

    def app_info(self, query: str) -> dict[str, Any]:
        """What the interface shows for an app a rule or an approval names: its own
        display name, bundle id and icon. Never launches anything."""
        info: dict[str, Any] = {"query": query, "name": query, "bundle_id": "", "icon": "",
                                "installed": False, "running": False, "refused": False}
        if not isinstance(query, str) or not query.strip():
            return info
        appkit = _appkit()
        running = self._running_match(query)
        if running is not None:
            path = str(running.bundleURL().path()) if running.bundleURL() else ""
            bundle_id = str(running.bundleIdentifier() or "")
            info.update(running=True, installed=True)
        else:
            row = self._installed_match(query)
            if row is None:
                return info
            path, bundle_id = row.get("path", ""), row["bundle_id"]
            info["installed"] = True
        name = str(appkit.NSFileManager.defaultManager().displayNameAtPath_(path) or "") if path else ""
        info.update(name=(name[:-4] if name.endswith(".app") else name) or query, bundle_id=bundle_id,
                    refused=RefusedApps(OWN_BUNDLES).hits(bundle_id), icon=self._icon(path) if path else "")
        return info

    def running_apps(self) -> list[dict[str, Any]]:
        """The running apps a person can name in a message: regular apps other than
        the refused ones, with the same fields as ``app_info``."""
        appkit = _appkit()
        refused = RefusedApps(OWN_BUNDLES)
        rows = []
        for app in running_applications(appkit):
            bundle_id = str(app.bundleIdentifier() or "")
            if (app.isTerminated() or app.activationPolicy() != appkit.NSApplicationActivationPolicyRegular
                    or not bundle_id or refused.hits(bundle_id) or int(app.processIdentifier()) == os.getpid()):
                continue
            path = str(app.bundleURL().path()) if app.bundleURL() else ""
            name = str(app.localizedName() or "") or Path(path).stem
            rows.append({"query": bundle_id, "name": name, "bundle_id": bundle_id, "icon": self._icon(path) if path else "",
                         "installed": True, "running": True, "refused": False})
        return sorted(rows, key=lambda row: row["name"].lower())

    def _icon(self, path: str, size: int = 64) -> str:
        """The app's icon as a PNG data URL, cached per bundle path."""
        cached = self._icons.get(path)
        if cached is not None:
            return cached
        import base64
        appkit = _appkit()
        image = appkit.NSWorkspace.sharedWorkspace().iconForFile_(path)
        rep = appkit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, size, size, 8, 4, True, False, appkit.NSDeviceRGBColorSpace, 0, 0)
        appkit.NSGraphicsContext.saveGraphicsState()
        try:
            appkit.NSGraphicsContext.setCurrentContext_(appkit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
            image.drawInRect_fromRect_operation_fraction_(
                ((0, 0), (size, size)), ((0, 0), (0, 0)), appkit.NSCompositingOperationCopy, 1.0)
        finally:
            appkit.NSGraphicsContext.restoreGraphicsState()
        data = rep.representationUsingType_properties_(appkit.NSBitmapImageFileTypePNG, {})
        url = "data:image/png;base64," + base64.b64encode(bytes(data)).decode() if data else ""
        self._icons[path] = url
        return url

    def _refuse(self, bundle_id: str, name: str) -> None:
        RefusedApps(OWN_BUNDLES).refuse(name or bundle_id, bundle_id, name)

    def _wait_for_window(self, pid: int, seconds: float) -> None:
        _, ax, _, _ = _frameworks()
        root = ax.AXUIElementCreateApplication(pid)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._windows(root):
                return
            time.sleep(0.15)

    # ------------------------------------------------------------------ windows
    def _windows(self, root) -> list[Any]:
        # Some apps (QuickTime Player with no document open) list the
        # application element itself among their windows.
        windows = [w for w in self._attr(root, "AXWindows") or []
                   if w != root and self._attr(w, "AXRole") == "AXWindow"]
        windows += self._other_space_windows(root, windows)
        return windows + self._service_panel_windows(root, windows)

    def _is_panel_service(self, pid: int) -> bool:
        cache = self.__dict__.setdefault("_services", {})
        if pid not in cache:
            app = _appkit().NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            cache[pid] = app is not None and str(app.bundleIdentifier() or "") == _PANEL_SERVICE
        return cache[pid]

    def _panels_for(self, pid: int) -> list[tuple[int, int, int, tuple[float, float, float, float]]]:
        """The out-of-process file dialogs on screen for ``pid`` (see :func:`panel_hosts`).

        Also records which app window shows each service window, so both
        report the window id the app's window has on screen.
        """
        try:
            rows = self._window_rows()
            owners = {int(r.get("kCGWindowOwnerPID", -1)) for r in rows}
            services = {p for p in owners if p > 0 and p != pid and self._is_panel_service(p)}
            panels = panel_hosts(rows, pid, services) if services else []
        except Exception:  # detecting a dialog only adds to a read; it must not fail it
            panels = []
        self.__dict__.setdefault("_panel_ids", {}).update({sid: hid for _, sid, hid, _ in panels})
        self.__dict__.setdefault("_live_panels", {})[pid] = [(hid, bounds) for _, _, hid, bounds in panels]
        return panels

    def _service_panel_windows(self, root, windows: list[Any]) -> list[Any]:
        """File dialog windows of the app that only the panel service's Accessibility tree has.

        A sandboxed app lists its out-of-process dialog among its windows
        while the dialog's link to it holds; when that drops, the dialog stays
        on screen and is read from the service process instead.
        """
        _, ax, _, _ = _frameworks()
        error, pid = ax.AXUIElementGetPid(root, None)
        if error:
            return []
        panels = self._panels_for(pid)
        if not panels or not skylight().available:
            return []
        known = {self._ax_window_id(w) for w in windows}
        frames = [f for f in (self._frame(w) for w in windows) if f is not None]
        found = []
        for service, sid, hid, bounds in panels:
            if hid in known or any(_same_frame(f, bounds) for f in frames):
                continue
            for window in self._attr(ax.AXUIElementCreateApplication(service), "AXWindows") or []:
                if skylight().window_id(window) == sid:
                    found.append(window)
                    break
        return found

    def _other_space_windows(self, root, windows: list[Any]) -> list[Any]:
        """Windows of the app on other Spaces, which ``AXWindows`` leaves out.

        Their ids are the app's off-screen ordinary windows that no listed
        window has; their elements come from SkyLight's remote-token lookup,
        searched again only when that set of ids changes.
        """
        if not skylight().available:
            return []
        _, ax, cg, _ = _frameworks()
        error, pid = ax.AXUIElementGetPid(root, None)
        if error:
            return []
        onscreen = {int(r.get("kCGWindowNumber", 0)) for r in cg.CGWindowListCopyWindowInfo(
            cg.kCGWindowListOptionOnScreenOnly, cg.kCGNullWindowID) or []}
        listed = {self._ax_window_id(w) for w in windows}
        wanted = set()
        for row in self._window_rows():
            wid = int(row.get("kCGWindowNumber", 0))
            bounds = row.get("kCGWindowBounds") or {}
            if (int(row.get("kCGWindowOwnerPID", -1)) == pid and row.get("kCGWindowLayer") == 0
                    and row.get("kCGWindowAlpha", 1) > 0 and wid not in onscreen and wid not in listed
                    and float(bounds.get("Width", 0)) >= 80 and float(bounds.get("Height", 0)) >= 60):
                wanted.add(wid)
        if not wanted:
            return []
        key = frozenset(wanted)
        cached = self._remote.get(pid)
        if cached is None or cached[0] != key:
            cached = (key, skylight().remote_windows(pid, wanted))
            self._remote[pid] = cached
        return [element for wid, element in sorted(cached[1].items()) if wid in wanted]

    def _ax_window_id(self, window) -> int:
        """The window server's id of an Accessibility window; for a file dialog
        drawn by the panel service, the id of the app window that shows it."""
        wid = (skylight().window_id(window) or 0) if skylight().available else 0
        return self.__dict__.get("_panel_ids", {}).get(wid, wid)

    def _pick_window(self, root, which, *, window_id=None, prefer_id: int = 0):
        """The window ``which`` (title or index) or ``window_id`` names; else the one
        the model last read (``prefer_id``) while it is open; else the focused one."""
        windows = self._windows(root)
        if window_id not in (None, "", 0):
            try:
                wanted = int(window_id)
            except (TypeError, ValueError) as exc:
                raise ComputerUseError("window_id must be an integer from list_windows") from exc
            for window in windows:
                if self._ax_window_id(window) == wanted:
                    return window
            raise ComputerUseError(f"window_id {wanted} is not an open window of this app; call list_windows")
        if which in (None, "") and prefer_id:
            for window in windows:
                if self._ax_window_id(window) == prefer_id:
                    return window
        if which not in (None, ""):
            if isinstance(which, int) or (isinstance(which, str) and which.strip().isdigit()):
                index = int(which)
                if 0 <= index < len(windows):
                    return windows[index]
                raise ComputerUseError(f"window {index} does not exist; the app has {len(windows)} window(s)")
            needle = str(which).strip().lower()
            for window in windows:
                if needle in str(self._attr(window, "AXTitle") or "").lower():
                    return window
            titles = [t for w in windows if (t := str(self._attr(w, "AXTitle") or ""))]
            detail = "; its windows are " + ", ".join(f'"{t}"' for t in titles[:8]) if titles else ""
            error, pid = _frameworks()[1].AXUIElementGetPid(root, None)
            if not error and self.__dict__.get("_live_panels", {}).get(pid):
                detail += ("; the system file dialog is on screen but Accessibility does not reach it now: "
                           "call open_file(app, path) to open a file")
            raise ComputerUseError(f"no window title contains {which!r}{detail}")
        for name in ("AXFocusedWindow", "AXMainWindow"):
            window = self._attr(root, name)
            if window is not None and window in windows:
                return window
        return windows[0] if windows else None

    def _window_key(self, window) -> Any:
        return self._ax_window_id(window) or ("title", str(self._attr(window, "AXTitle") or ""))

    def _new_front_window(self, root, previous: WindowState):
        """The app's focused window when it opened since the model's last read.

        A step that brings up a dialog or a document leaves the model holding
        the window under it; the next read follows the new one instead.
        """
        if not previous.window_keys:
            return None
        windows = self._windows(root)
        for name in ("AXFocusedWindow", "AXMainWindow"):
            window = self._attr(root, name)
            if window is not None and window in windows:
                if self._window_key(window) not in previous.window_keys:
                    return window
                break
        # A dialog can open without taking the focus: a background app's
        # out-of-process file dialog leaves its document window focused.
        for window in windows:
            if self._window_key(window) not in previous.window_keys and (
                    str(self._attr(window, "AXSubrole") or "") in _DIALOG_SUBROLES or self._file_panel(window)):
                return window
        return None

    def _panel_element(self, element) -> bool:
        """Whether ``element`` is itself the system Open or Save dialog."""
        if str(self._attr(element, "AXIdentifier") or "") in _FILE_PANELS:
            return True
        live = [p for panels in self.__dict__.get("_live_panels", {}).values() for p in panels]
        if not live:
            return False
        wid = self._ax_window_id(element)
        if wid and any(wid == hid for hid, _ in live):
            return True
        frame = self._frame(element)
        return frame is not None and any(_same_frame(frame, bounds) for _, bounds in live)

    def _file_panel(self, window) -> bool:
        """Whether ``window`` is the system Open or Save dialog, or holds one as a sheet or child window."""
        return self._panel_in(window) is not None

    def _panel_in(self, window):
        """The file dialog ``window`` is or holds (as a sheet or child window), else None."""
        if window is None:
            return None
        if self._panel_element(window):
            return window
        for child in self._attr(window, "AXChildren") or []:
            if self._attr(child, "AXRole") in ("AXSheet", "AXWindow") and self._panel_element(child):
                return child
        return None

    def _open_panel(self, pid: int):
        """The app's open file dialog, True when one is on screen that Accessibility does not reach, else None."""
        _, ax, _, _ = _frameworks()
        for window in self._windows(ax.AXUIElementCreateApplication(pid)):
            panel = self._panel_in(window)
            if panel is not None:
                return panel
        return True if self.__dict__.get("_live_panels", {}).get(pid) else None

    def _refuse_file_panel(self, app, pid: int) -> None:
        """Keys posted to the app are dropped while its file dialog is open; say so instead of sending them.

        That is a dialog in or over the focused window, or one standing as a
        window of its own (app-modal); a sheet on another document window
        leaves keys to the focused one alone.
        """
        _, ax, _, _ = _frameworks()
        root = ax.AXUIElementCreateApplication(pid)
        if (self._file_panel(self._attr(root, "AXFocusedWindow"))
                or any(self._panel_element(w) for w in self._windows(root))
                or self.__dict__.get("_live_panels", {}).get(pid)):
            raise ComputerUseError(
                f"{app.localizedName()} has the system file dialog open, and macOS does not deliver keys sent in "
                "the background to it, nor to the window under it. To open a file, call open_file(app, path) with "
                "its absolute path: it cancels the dialog first. Otherwise read the dialog (get_app_state with "
                "window=<its title>), click its items and buttons, and set_value its name field.")

    def _window_id(self, window, pid: int, frame) -> int:
        """The window server's id for an Accessibility window."""
        wid = self._ax_window_id(window)
        if wid:
            return wid
        if frame is None:
            return 0
        x, y, width, height = frame
        for row in self._window_rows():
            if int(row.get("kCGWindowOwnerPID", -1)) != pid or row.get("kCGWindowLayer") != 0:
                continue
            bounds = row.get("kCGWindowBounds") or {}
            if (abs(float(bounds.get("X", 0)) - x) < 3 and abs(float(bounds.get("Y", 0)) - y) < 3
                    and abs(float(bounds.get("Width", 0)) - width) < 3 and abs(float(bounds.get("Height", 0)) - height) < 3):
                return int(row.get("kCGWindowNumber", 0))
        return 0

    # ------------------------------------------------------------------ tree
    def _node(self, element, depth: int):
        values = self._attrs(element, _ATTRS)
        role = str(values.get("AXRole") or "AXUnknown")
        subrole = str(values.get("AXSubrole") or "")
        label = values.get("AXTitle") or values.get("AXDescription") or ""
        label = str(label) if isinstance(label, str) else ""
        help_text = values.get("AXHelp")
        if not label and isinstance(help_text, str):
            label = help_text
        raw_value = values.get("AXValue")
        value = ""
        flags: list[str] = []
        secure = role == _SECURE or subrole == _SECURE
        if secure:
            flags.append("secure")
        elif role in _TOGGLE_ROLES and (isinstance(raw_value, (int, float, bool))
                                        or str(raw_value).strip().lower() in ("0", "1", "true", "false")):
            on = raw_value if isinstance(raw_value, (int, float, bool)) else str(raw_value).strip().lower() in ("1", "true")
            flags.append("checked" if on else "unchecked")
        elif isinstance(raw_value, str):
            value = raw_value
        elif isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            value = f"{raw_value:g}" if isinstance(raw_value, float) else str(raw_value)
        if values.get("AXEnabled") is False:
            flags.append("disabled")
        if values.get("AXFocused") is True:
            flags.append("focused")
        if values.get("AXSelected") is True:
            flags.append("selected")
        if values.get("AXExpanded") is True:
            flags.append("expanded")
        if role in _SETTABLE_ROLES and not secure and self._settable(element, "AXValue"):
            flags.append("settable")
        placeholder = values.get("AXPlaceholderValue")
        node = Node(role=role, subrole=subrole, depth=depth, label=label, value=value,
                    placeholder=str(placeholder) if isinstance(placeholder, str) else "",
                    flags=flags, actions=self._actions(element))
        children = values.get("AXChildren") or []
        return node, list(children)

    def _walk(self, roots: list[tuple[Any, int]], nodes, elements, budget: list[int], *, menus_open_only=False,
              seen: set | None = None):
        stack = list(reversed(roots))
        # An element reached twice is a cycle (an app listing itself as its
        # window's child); reading it again would repeat it to MAX_DEPTH.
        seen = set() if seen is None else seen
        while stack:
            _check_cancelled()
            if budget[0] <= 0:
                return False
            element, depth = stack.pop()
            try:
                if element in seen:
                    continue
                seen.add(element)
            except TypeError:  # an unhashable element: walk it without the guard
                pass
            node, children = self._node(element, depth)
            transparent = (node.role in TRANSPARENT_ROLES and not node.label and not node.value
                           and not [a for a in node.actions if a not in ("AXScrollToVisible", "AXShowMenu")])
            child_depth = depth
            if not transparent:
                node.index = len(nodes)
                nodes.append(node)
                elements.append(element)
                budget[0] -= 1
                child_depth = depth + 1
            if child_depth > MAX_DEPTH:
                continue
            if menus_open_only and node.role == "AXMenuBarItem" and "selected" not in node.flags:
                continue
            stack.extend((child, child_depth) for child in reversed(children))
        return True

    def _read_tree(self, root, window):
        """The window, the menus and dialogs beside it, and the menu bar with only open menus
        expanded (the menu action reads closed ones), so every read covers the same ground."""
        nodes: list[Node] = []
        elements: list[Any] = []
        budget = [MAX_NODES]
        complete = True
        # The application element is never part of the tree; a child pointing back at it is a cycle.
        seen: set = {root}
        if window is not None:
            complete = self._walk([(window, 0)], nodes, elements, budget, seen=seen)
        # Menus and popovers opened by the app sit beside its windows.
        for child in self._attr(root, "AXChildren") or []:
            role = str(self._attr(child, "AXRole") or "")
            if role in ("AXMenu", "AXPopover") or (
                    role == "AXWindow" and child != window
                    and self._attr(child, "AXSubrole") in ("AXFloatingWindow", "AXSystemDialog", "AXDialog")):
                complete = self._walk([(child, 0)], nodes, elements, budget, seen=seen) and complete
        menubar = self._attr(root, "AXMenuBar")
        if menubar is not None and budget[0] > 0:
            complete = self._walk([(menubar, 0)], nodes, elements, budget, menus_open_only=True,
                                  seen=seen) and complete
        return nodes, elements, complete

    # ------------------------------------------------------------------ screenshot
    def _backing_scale(self) -> float:
        appkit, _, _, _ = _frameworks()
        return max([1.0] + [float(s.backingScaleFactor()) for s in appkit.NSScreen.screens() or []])

    def _capture(self, pid: int, frame, window_id: int = 0, *, region=None) -> tuple[bytes | None, float]:
        """PNG of the window (or of ``region``, window-relative points) and its pixels per point.

        The window is found by its window-server id, falling back to its
        frame; windows on another Space or covered by others are captured
        as they are, without being brought forward.
        """
        appkit, _, _, sc = _frameworks()
        x, y, width, height = frame
        content = _await(lambda cb: sc.SCShareableContent
                         .getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(True, False, cb))
        match = None
        for window in content.windows():
            owner = window.owningApplication()
            if owner is None or int(owner.processID()) != pid:
                continue
            if window_id and int(window.windowID()) == window_id:
                match = window
                break
            f = window.frame()
            if (match is None and window.windowLayer() == 0 and max(
                    abs(f.origin.x - x), abs(f.origin.y - y), abs(f.size.width - width), abs(f.size.height - height)) < 3):
                match = window
        if match is None:
            return None, 1.0
        rx, ry, rw, rh = region if region else (0.0, 0.0, width, height)
        scale = min(self._backing_scale(), MAX_IMAGE_SIDE / max(rw, rh, 1))
        config = sc.SCStreamConfiguration.alloc().init()
        if region:
            config.setSourceRect_(((rx, ry), (rw, rh)))
        config.setWidth_(max(1, int(round(rw * scale))))
        config.setHeight_(max(1, int(round(rh * scale))))
        config.setShowsCursor_(False)
        content_filter = sc.SCContentFilter.alloc().initWithDesktopIndependentWindow_(match)
        image = _await(lambda cb: sc.SCScreenshotManager
                       .captureImageWithFilter_configuration_completionHandler_(content_filter, config, cb))
        bitmap = appkit.NSBitmapImageRep.alloc().initWithCGImage_(image)
        data = bitmap.representationUsingType_properties_(appkit.NSBitmapImageFileTypePNG, {})
        return bytes(data), float(config.width()) / rw if rw else scale

    # ------------------------------------------------------------------ state
    def get_state(self, session: str, query: str, *, window=None, window_id=None, full=False,
                  as_diff=True, include: str = "all") -> Observation:
        """Read one window: its tree, its screenshot, or both (``include``).

        Indices stay stable for the same window (see :func:`tree.number`);
        a tree read without ``full`` returns only what changed since the
        model last saw it. A screenshot-only read updates the coordinate
        frame and leaves the tree and its indices as they were.
        """
        include = str(include or "all").lower()
        if include not in INCLUDE:
            raise ComputerUseError("include must be all, tree or screenshot")
        _unlocked()
        _, ax, _, _ = _frameworks()
        app = self.resolve_app(query)
        self._await_answer(app)
        with self._lock:
            pid = int(app.processIdentifier())
            root = ax.AXUIElementCreateApplication(pid)
            ax.AXUIElementSetMessagingTimeout(root, 3.0)
            key = (session, pid)
            previous = self._states.get(key)
            target = self._pick_window(root, window, window_id=window_id,
                                       prefer_id=previous.window_id if previous else 0)
            followed = ""
            if previous is not None and window in (None, "") and window_id in (None, "", 0) and include != "screenshot":
                fresh = self._new_front_window(root, previous)
                if fresh is not None and fresh != target:
                    followed, target = previous.window_title, fresh
            frame = self._frame(target) if target is not None else None
            title = str(self._attr(target, "AXTitle") or "") if target is not None else ""
            minimized = bool(self._attr(target, "AXMinimized")) if target is not None else False
            wid = self._window_id(target, pid, frame) if target is not None else 0
            same_window = bool(previous) and (previous.window_id == wid if wid else previous.window_title == title)
            self._control(session, pid, str(app.localizedName() or ""), target, wid)
            if include == "screenshot" and not same_window:
                raise ComputerUseError("Read this window with include=all first; a screenshot-only read keeps the "
                                       "tree of the window the model already has.")
            image, scale = (None, 1.0)
            if include != "tree" and frame is not None and frame[2] > 1 and frame[3] > 1 and not minimized:
                image, scale = self._capture(pid, frame, wid)
            name = str(app.localizedName() or query)
            lines = [f"App: {name} ({app.bundleIdentifier() or 'no bundle id'}, pid {pid})"
                     + ("" if app.isActive() else ", in the background")]
            if target is None:
                lines.append("Window: none open. The tree lists the menu bar only.")
            else:
                notes = [n for n, on in (("minimized", minimized), ("app hidden", bool(app.isHidden()))) if on]
                note = f" ({', '.join(notes)})" if notes else ""
                where = f"window_id {wid}, " if wid else ""
                lines.append(f'Window: "{title}"{note}, {where}{int(frame[2])}x{int(frame[3])} points'
                             if frame else f'Window: "{title}"{note}')
                if image is not None:
                    w, h = int(round(frame[2] * scale)), int(round(frame[3] * scale))
                    lines.append(f"Screenshot: {w}x{h} pixels; x,y coordinates in actions are pixels of this screenshot.")
                elif include != "tree":
                    lines.append("Screenshot: unavailable (the window is minimized or its app is hidden; "
                                 "show_window brings it back). Use element indices.")
                if followed:
                    lines.append(f'It opened over "{followed}" after the last step, so this state and the next '
                                 "actions are of it; get_app_state(window=...) reads another window.")
            windows = self._windows(root)
            others = [w for w in windows if w != target]
            file_panel = self._file_panel(target)
            if file_panel:
                lines.append(FILE_PANEL_NOTE)
            elif target is not None and (any(self._file_panel(w) for w in others)
                                         or self.__dict__.get("_live_panels", {}).get(pid)):
                lines.append(FILE_PANEL_OPEN_NOTE)
            if others:
                lines.append("Other windows: " + "; ".join(
                    f'"{t}"' if (t := str(self._attr(w, "AXTitle") or "")) else "(untitled)" for w in others[:8])
                    + ". list_windows shows their ids; pass window=<title> or window_id to read one.")
            if include == "screenshot":
                previous.origin = (frame[0], frame[1]) if frame else previous.origin
                previous.size = (frame[2], frame[3]) if frame else previous.size
                previous.scale, previous.has_screenshot = scale, image is not None
                previous.text_boxes = self._read_text(image, frame, scale) if previous.sparse else []
                previous.shapes = self._read_shapes(image, frame, scale, previous.text_boxes) if previous.sparse else []
                lines.append("")
                lines.append("Tree not read (include=screenshot); element indices are those of the last tree.")
                lines.extend(self._text_section(previous.text_boxes, previous.shapes))
                return Observation(text="\n".join(lines), image=image, app=name)
            nodes, elements, complete = self._read_tree(root, target)
            prior = previous.snapshot if same_window else None
            fresh = number(nodes, prior)
            body, is_diff = diff(prior if (as_diff and not full) else None, nodes)
            state = WindowState(
                pid=pid, window_title=title, window_id=wid,
                elements={node.index: element for node, element in zip(nodes, elements)},
                nodes=nodes, by_index={node.index: node for node in nodes}, snapshot=snapshot(nodes, fresh),
                origin=(frame[0], frame[1]) if frame else (0.0, 0.0),
                size=(frame[2], frame[3]) if frame else (0.0, 0.0), scale=scale, has_screenshot=image is not None,
                window_keys=frozenset(self._window_key(w) for w in windows))
            if include == "tree" and same_window and previous.has_screenshot:
                state.scale, state.has_screenshot = previous.scale, True
            # A file dialog's file list is not in the tree, so its names come from the screenshot.
            state.sparse = target is not None and (file_panel or _window_node_count(nodes) < OCR_SPARSE_NODES)
            if state.sparse:
                state.text_boxes = self._read_text(image, frame, scale)
                state.shapes = self._read_shapes(image, frame, scale, state.text_boxes)
            self._states[key] = state
            if not complete:
                lines.append(f"The tree was cut at {MAX_NODES} elements.")
            lines.append("")
            lines.append("Accessibility tree changes since the last state:" if is_diff else "Accessibility tree:")
            lines.append(body or "(empty)")
            lines.extend(self._text_section(state.text_boxes, state.shapes))
            return Observation(text="\n".join(lines), image=image, app=name)

    def _read_text(self, image: bytes | None, frame, scale: float) -> list[ocr.TextBox]:
        if image is None or frame is None:
            return []
        try:
            return ocr.recognize(image, frame[2] * scale, frame[3] * scale)
        except Exception:  # OCR only adds to a read; a failure must not fail it
            return []

    def _read_shapes(self, image: bytes | None, frame, scale: float, text: list[ocr.TextBox]) -> list[ocr.Shape]:
        if image is None or frame is None:
            return []
        try:
            return ocr.detect_shapes(image, frame[2] * scale, frame[3] * scale, text, scale)
        except Exception:  # like OCR, shapes only add to a read
            return []

    @staticmethod
    def _text_section(boxes: list[ocr.TextBox], shapes: list[ocr.Shape] = ()) -> list[str]:
        out = []
        if boxes:
            out += ["", "Text on the screenshot (OCR; this app exposes little to Accessibility, so act on it "
                        "with x,y at a line's centre):", ocr.render(boxes)]
        if shapes:
            out += ["", "Icons and controls without text on the screenshot (outlines; check the screenshot "
                        "for what each is, then act with x,y at its centre):", ocr.render_shapes(list(shapes))]
        return out

    def _state_for(self, session: str, query: str, app=None) -> tuple[Any, WindowState]:
        app = app if app is not None else self.resolve_app(query, launch=False)
        state = self._states.get((session, int(app.processIdentifier())))
        if state is None:
            raise NotDispatched(f"Call get_app_state for {query!r} first; actions use its element indices and screenshot.")
        return app, state

    def _element(self, state: WindowState, index) -> Any:
        if index is None or isinstance(index, bool):
            raise NotDispatched("element_index is required")
        try:
            index = int(index)
        except (TypeError, ValueError) as exc:
            raise NotDispatched("element_index must be an integer") from exc
        element = state.elements.get(index)
        if element is None:
            raise StaleElement(f"element_index {index} is not in the last state of this window. "
                                   "Use an index from the latest tree (get_app_state, or find).")
        _, ax, _, _ = _frameworks()
        code, _ = ax.AXUIElementCopyAttributeValue(element, "AXRole", None)
        if code == _INVALID_ELEMENT:
            element = self._rebind(state, index)
            if element is None:
                raise StaleElement(f"element {index} no longer exists. Call get_app_state for a fresh tree.")
        return element

    def _rebind(self, state: WindowState, index: int):
        """The element now behind ``index`` when the app replaced the one read earlier (see :func:`tree.rebind`)."""
        try:
            _, ax, _, _ = _frameworks()
            root = ax.AXUIElementCreateApplication(state.pid)
            ax.AXUIElementSetMessagingTimeout(root, 3.0)
            windows = self._windows(root)
            if state.window_id:
                found = [w for w in windows if self._ax_window_id(w) == state.window_id]
            else:
                found = [w for w in windows if str(self._attr(w, "AXTitle") or "") == state.window_title]
            if len(found) != 1:
                return None
            nodes, elements, _ = self._read_tree(root, found[0])
            return rebind(state, index, nodes, elements)
        except Exception:
            # The step then fails as it did before: the model reads the window again.
            _log.debug("re-reading a window for element %s failed pid=%s", index, state.pid, exc_info=True)
            return None

    def _node_at(self, state: WindowState, index) -> Node:
        return state.by_index[int(index)]

    def _point(self, state: WindowState, x, y) -> tuple[float, float]:
        if x is None or y is None:
            raise NotDispatched("x and y are required when no element_index is given")
        if not state.has_screenshot:
            raise NotDispatched("This window has no screenshot to take coordinates from; use element_index.")
        try:
            px, py = float(x), float(y)
        except (TypeError, ValueError) as exc:
            raise NotDispatched("x and y must be numbers") from exc
        width, height = state.size[0] * state.scale, state.size[1] * state.scale
        if not (math.isfinite(px) and math.isfinite(py) and 0 <= px <= width and 0 <= py <= height):
            raise NotDispatched(f"({x}, {y}) is outside the {int(width)}x{int(height)} screenshot")
        return screen_point(px, py, origin=state.origin, scale=state.scale)

    def _follow_window(self, state: WindowState) -> None:
        """Move ``state.origin`` to where the window is now, if the window server knows it."""
        if not state.window_id:
            return
        try:
            _, _, cg, _ = _frameworks()
            rows = cg.CGWindowListCopyWindowInfo(cg.kCGWindowListOptionIncludingWindow, state.window_id) or []
        except Exception:
            return
        origin = live_origin(rows, state.window_id)
        if origin is not None:
            state.origin = origin

    def _center(self, element) -> tuple[float, float]:
        frame = self._frame(element)
        if frame is None or frame[2] <= 0 or frame[3] <= 0:
            raise NotDispatched("This element has no on-screen frame to point at.")
        return frame[0] + frame[2] / 2, frame[1] + frame[3] / 2

    def _visible_rect(self, state: WindowState, element) -> tuple[float, float, float, float] | None:
        """The part of the window where ``element`` can show: the window cut by its scroll areas and web area.

        An element scrolled above a web page sits under the browser's toolbar,
        still inside the window; only its scrolling ancestors know it is hidden.
        """
        rect = (state.origin[0], state.origin[1], state.size[0], state.size[1]) if state.size[0] > 0 else None
        node, depth = self._attr(element, "AXParent"), 0
        while node is not None and depth < 40:
            if self._attr(node, "AXRole") in ("AXScrollArea", "AXWebArea"):
                frame = self._frame(node)
                if frame is not None and frame[2] > 0 and frame[3] > 0:
                    rect = frame if rect is None else _intersect(rect, frame)
            node, depth = self._attr(node, "AXParent"), depth + 1
        return rect

    def _shown(self, rect, frame) -> bool:
        """Whether the centre of ``frame`` lies inside ``rect`` (no rect: nothing to check)."""
        if frame is None or frame[2] <= 0 or frame[3] <= 0:
            return False
        if rect is None:
            return True
        x, y = frame[0] + frame[2] / 2, frame[1] + frame[3] / 2
        return rect[0] <= x <= rect[0] + rect[2] and rect[1] <= y <= rect[1] + rect[3]

    def _reachable_center(self, state: WindowState, element) -> tuple[float, float]:
        """Where the pointer can reach ``element``: its centre, scrolled into view first.

        A long page or list keeps the elements scrolled out of view in its
        tree, with no frame or with one outside what is shown, where a pointer
        event lands on nothing or on the toolbar. Those that offer
        AXScrollToVisible are scrolled into view, and the centre is read again
        once it stops moving.
        """
        frame = self._frame(element)
        if self._shown(self._visible_rect(state, element), frame):
            return frame[0] + frame[2] / 2, frame[1] + frame[3] / 2
        if "AXScrollToVisible" in self._actions(element) and self._perform(element, "AXScrollToVisible") == 0:
            frame = self._settled_frame(state, element)
        if (frame is not None and frame[2] > 0 and frame[3] > 0
                and not self._shown(self._visible_rect(state, element), frame)
                and self._scroll_areas_to(element, frame)):
            # Native lists and tables often lack AXScrollToVisible; their scroll bars move them.
            frame = self._settled_frame(state, element)
        if frame is None or frame[2] <= 0 or frame[3] <= 0:
            raise NotDispatched(
                "This element has no on-screen frame to point at: it is hidden, such as an option of a menu or "
                "dropdown that is closed now. Open what holds it (click the dropdown or field), then use the "
                "indices the result shows.")
        if not self._shown(self._visible_rect(state, element), frame):
            raise NotDispatched(
                "This element is outside the visible part of the window and could not be scrolled into view; "
                "scroll toward it first, or act on it with set_value or perform_secondary_action.")
        return frame[0] + frame[2] / 2, frame[1] + frame[3] / 2

    def _settled_frame(self, state: WindowState, element):
        """The element's frame once a scroll has stopped: pages scroll smoothly, so two reads must agree."""
        deadline = time.monotonic() + SCROLL_SETTLE_MAX
        frame = previous = None
        while time.monotonic() < deadline:
            time.sleep(0.05)
            frame = self._frame(element)
            if frame is not None and frame == previous:
                break
            previous = frame
        self._follow_window(state)
        return frame

    def _scroll_areas_to(self, element, frame) -> bool:
        """Centre ``element`` in each scroll area that holds it by setting the area's scroll bars.

        The position inside an area's contents is the element's frame less the
        contents' frame; a scroll bar's value is the share of the scrollable
        length scrolled past. Returns whether any bar moved.
        """
        moved, node, depth = False, self._attr(element, "AXParent"), 0
        while node is not None and depth < 40:
            if self._attr(node, "AXRole") == "AXScrollArea":
                area = self._frame(node)
                contents = self._attr(node, "AXContents") or []
                inner = self._frame(contents[0]) if contents else None
                if area and inner:
                    for axis, bar_name in ((1, "AXVerticalScrollBar"), (0, "AXHorizontalScrollBar")):
                        shown, total = area[axis + 2], inner[axis + 2]
                        bar = self._attr(node, bar_name)
                        if total <= shown or bar is None or not self._settable(bar, "AXValue"):
                            continue
                        middle = frame[axis] + frame[axis + 2] / 2 - inner[axis]
                        value = max(0.0, min(1.0, (middle - shown / 2) / (total - shown)))
                        moved = self._set(bar, "AXValue", value) == 0 or moved
            node, depth = self._attr(node, "AXParent"), depth + 1
        return moved

    # ------------------------------------------------------------------ pointer (background)
    def _route(self, state: WindowState):
        """The SkyLight route for this window, or an explanation of why not."""
        sky = skylight()
        if not sky.available:
            raise ComputerUseError("Background pointer input is unavailable on this macOS; use element_index actions.")
        if not state.window_id:
            raise ComputerUseError("The window has no window-server id to route pointer input to; use element_index actions.")
        return sky

    def _covering(self, state: WindowState) -> list[Any]:
        """Other windows stacked over the operated one, deepest first, as Accessibility windows.

        An app orders its window to the front when it handles a mouse-down,
        even one delivered in the background. Raising these again after the
        gesture puts the stack back as the user left it. The front app's
        windows stay above the operated one anyway and are left alone.
        """
        try:
            appkit, ax, cg, _ = _frameworks()
            front = appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
            rows = cg.CGWindowListCopyWindowInfo(cg.kCGWindowListOptionOnScreenOnly, cg.kCGNullWindowID) or []
            above = covering_rows(rows, state.window_id, int(front.processIdentifier()) if front is not None else -1)
            sky, windows, found = skylight(), {}, []
            for row in above:
                pid = int(row["kCGWindowOwnerPID"])
                if pid not in windows:
                    windows[pid] = list(self._attr(ax.AXUIElementCreateApplication(pid), "AXWindows") or [])
                wid = int(row["kCGWindowNumber"])
                found.extend(w for w in windows[pid] if sky.window_id(w) == wid)
            return found
        except Exception:
            # The order is a courtesy; a step never fails because of it.
            return []

    def _restore_order(self, covering: list[Any]) -> None:
        for window in covering:
            self._perform(window, "AXRaise")

    def _mouse_event(self, cg, kind, point, button_code, flags=0):
        source = cg.CGEventSourceCreate(cg.kCGEventSourceStateHIDSystemState)
        event = cg.CGEventCreateMouseEvent(source, kind, point, button_code)
        if flags:
            cg.CGEventSetFlags(event, flags)
        return event

    def _post_mouse(self, sky, cg, state, pid, event, point, *, click_state, button, subtype, group):
        sky.stamp_mouse(event, pid=pid, window_id=state.window_id, click_state=click_state, button=button,
                        subtype=subtype, group=group, location=window_location(point, state.origin))
        sky.post(pid, event)

    def _primer(self, sky, cg, state, pid, point, group) -> None:
        """A mouseMoved at the point first: AppKit hit-tests against the last tracked position."""
        move = self._mouse_event(cg, cg.kCGEventMouseMoved, point, cg.kCGMouseButtonLeft)
        self._post_mouse(sky, cg, state, pid, move, point, click_state=0, button=0, subtype=SUBTYPE_TOUCH, group=group)
        time.sleep(0.012)

    def _with_modifiers(self, pid: int, flags: int, body) -> None:
        """Hold modifier keys in the target process around a pointer gesture."""
        held = []
        try:
            for flag, code in keymap.MODIFIER_KEYCODES.items():
                if flags & flag:
                    self._post_key_event(pid, code, True, flags)
                    held.append(code)
                    time.sleep(0.008)
            body()
        finally:
            for code in reversed(held):
                self._post_key_event(pid, code, False, 0)
                time.sleep(0.008)

    def _background_click(self, state: WindowState, pid: int, point, button: str, count: int, flags: int,
                          hold: float = 0.0) -> None:
        sky = self._route(state)
        _, _, cg, _ = _frameworks()
        kinds = {
            "left": (cg.kCGEventLeftMouseDown, cg.kCGEventLeftMouseUp, cg.kCGMouseButtonLeft),
            "right": (cg.kCGEventRightMouseDown, cg.kCGEventRightMouseUp, cg.kCGMouseButtonRight),
            "middle": (cg.kCGEventOtherMouseDown, cg.kCGEventOtherMouseUp, cg.kCGMouseButtonCenter),
        }
        down, up, code = kinds[button]
        number = _BUTTONS[button]
        sky.focus_without_raise(pid, state.window_id)
        time.sleep(0.05)
        group = sky.next_group()

        def gesture():
            self._primer(sky, cg, state, pid, point, group)
            for click in range(1, count + 1):
                event = self._mouse_event(cg, down, point, code, flags)
                self._post_mouse(sky, cg, state, pid, event, point, click_state=click, button=number,
                                 subtype=SUBTYPE_TOUCH, group=group)
                # An NSButton's mouseDown enters a tracking loop that polls for
                # the mouseUp; too tight a gap and the click is dropped.
                time.sleep(max(0.028, hold))
                event = self._mouse_event(cg, up, point, code, flags)
                self._post_mouse(sky, cg, state, pid, event, point, click_state=click, button=number,
                                 subtype=SUBTYPE_TOUCH, group=group)
                if count > 1:
                    time.sleep(0.08)

        covering = self._covering(state)
        try:
            self._with_modifiers(pid, flags, gesture)
        finally:
            self._restore_order(covering)

    def _background_drag(self, state: WindowState, pid: int, start, end) -> None:
        sky = self._route(state)
        _, _, cg, _ = _frameworks()
        left = cg.kCGMouseButtonLeft
        covering = self._covering(state)
        sky.focus_without_raise(pid, state.window_id)
        time.sleep(0.05)
        group = sky.next_group()
        try:
            self._primer(sky, cg, state, pid, start, group)
            event = self._mouse_event(cg, cg.kCGEventLeftMouseDown, start, left)
            self._post_mouse(sky, cg, state, pid, event, start, click_state=1, button=0, subtype=SUBTYPE_DEFAULT,
                             group=group)
            time.sleep(0.12)
            distance = math.hypot(end[0] - start[0], end[1] - start[1])
            steps = max(2, min(MAX_DRAG_STEPS, int(distance / 12) + 1))
            for step in range(1, steps + 1):
                t = step / steps
                point = (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
                event = self._mouse_event(cg, cg.kCGEventLeftMouseDragged, point, left)
                self._post_mouse(sky, cg, state, pid, event, point, click_state=1, button=0,
                                 subtype=SUBTYPE_DEFAULT, group=group)
                time.sleep(0.016)
            time.sleep(0.08)
            event = self._mouse_event(cg, cg.kCGEventLeftMouseUp, end, left)
            self._post_mouse(sky, cg, state, pid, event, end, click_state=1, button=0, subtype=SUBTYPE_DEFAULT,
                             group=group)
        finally:
            self._restore_order(covering)

    def _background_scroll(self, state: WindowState, pid: int, point, direction: str, pages: float,
                           pixels: float = 0.0) -> None:
        sky = self._route(state)
        _, _, cg, _ = _frameworks()
        if pixels:
            ticks = max(1, int(math.ceil(pixels / PIXELS_PER_EVENT)))
            step, unit = int(round(pixels / ticks)), cg.kCGScrollEventUnitPixel
        else:
            ticks = max(1, int(round(pages * TICKS_PER_PAGE)))
            step, unit = LINES_PER_TICK, cg.kCGScrollEventUnitLine
        # macOS: a positive vertical delta reveals content above.
        dy = {"up": step, "down": -step}.get(direction, 0)
        dx = {"left": step, "right": -step}.get(direction, 0)
        group = sky.next_group()
        self._primer(sky, cg, state, pid, point, group)
        for _ in range(ticks):
            _check_cancelled()
            source = cg.CGEventSourceCreate(cg.kCGEventSourceStateHIDSystemState)
            event = cg.CGEventCreateScrollWheelEvent(source, unit, 2, dy, dx)
            cg.CGEventSetLocation(event, point)
            self._post_mouse(sky, cg, state, pid, event, point, click_state=0, button=0, subtype=SUBTYPE_DEFAULT, group=group)
            time.sleep(0.03)

    # ------------------------------------------------------------------ keyboard (background)
    def _post_key_event(self, pid: int, keycode: int, down: bool, flags: int, text: str = "") -> None:
        _, _, cg, _ = _frameworks()
        source = cg.CGEventSourceCreate(cg.kCGEventSourceStateHIDSystemState)
        event = cg.CGEventCreateKeyboardEvent(source, keycode, down)
        cg.CGEventSetFlags(event, flags)
        if text:
            cg.CGEventKeyboardSetUnicodeString(event, len(text.encode("utf-16-le")) // 2, text)
        sky = skylight()
        if sky.available:
            sky.post(pid, event, keyboard=True)
        else:
            cg.CGEventPostToPid(pid, event)

    def _post_chord(self, pid: int, chord: keymap.Chord, duration: float = 0.0) -> None:
        flags = 0
        for flag, code in keymap.MODIFIER_KEYCODES.items():
            if chord.flags & flag and code != chord.keycode:
                flags |= flag
                self._post_key_event(pid, code, True, flags)
                time.sleep(0.008)
        self._post_key_event(pid, chord.keycode, True, chord.flags)
        time.sleep(max(0.012, min(float(duration or 0), 10.0)))
        self._post_key_event(pid, chord.keycode, False, chord.flags)
        for flag, code in reversed(list(keymap.MODIFIER_KEYCODES.items())):
            if chord.flags & flag and code != chord.keycode:
                flags &= ~flag
                self._post_key_event(pid, code, False, flags)
                time.sleep(0.008)

    def _post_text(self, pid: int, text: str) -> None:
        for index, char in enumerate(text):
            if index % 40 == 0:
                _check_cancelled()
            if char == "\n":
                self._post_chord(pid, keymap.Chord(36, 0))
                continue
            if char == "\t":
                self._post_chord(pid, keymap.Chord(48, 0))
                continue
            self._post_key_event(pid, 0, True, 0, char)
            self._post_key_event(pid, 0, False, 0, char)
            time.sleep(0.004)

    def _refuse_secure_input(self) -> None:
        """Secure keyboard entry means a password field has focus somewhere."""
        _, _, cg, _ = _frameworks()
        info = cg.CGSessionCopyCurrentDictionary() or {}
        if info.get("kCGSSessionSecureInputPID"):
            raise ComputerUseError("Secure keyboard entry is active (a password field is focused somewhere on this Mac); "
                                   "typing is refused until it closes.")

    # ------------------------------------------------------------------ text helpers
    def _focus(self, element) -> None:
        if self._attr(element, "AXFocused") is not True and self._settable(element, "AXFocused"):
            self._set(element, "AXFocused", True)
            time.sleep(0.08)

    def _refuse_secure(self, node: Node | None, element=None) -> None:
        if node is not None and "secure" in node.flags:
            refuse_password_field("typing into this password field")
        if element is not None:
            role = str(self._attr(element, "AXRole") or "")
            subrole = str(self._attr(element, "AXSubrole") or "")
            if _SECURE in (role, subrole):
                refuse_password_field("typing into the focused password field")

    def _focused_element(self, pid: int):
        _, ax, _, _ = _frameworks()
        return self._attr(ax.AXUIElementCreateApplication(pid), "AXFocusedUIElement")

    def _keys_reach(self, pid: int, state) -> bool:
        """Whether posted keys reach the app's window. They do not while it is on
        another Space: clicks and Accessibility actions arrive there, keys are dropped."""
        _, ax, cg, _ = _frameworks()
        root = ax.AXUIElementCreateApplication(pid)
        windows = self._windows(root)
        window = None
        if state is not None and state.window_id:
            window = next((w for w in windows if self._ax_window_id(w) == state.window_id), None)
        if window is None:
            focused = self._attr(root, "AXFocusedWindow")
            window = focused if focused is not None else (windows[0] if windows else None)
        if window is None or self._attr(window, "AXMinimized"):
            return True
        wid = self._ax_window_id(window)
        if not wid:
            return True
        return any(int(r.get("kCGWindowNumber", 0)) == wid for r in cg.CGWindowListCopyWindowInfo(
            cg.kCGWindowListOptionOnScreenOnly, cg.kCGNullWindowID) or [])

    def _insert(self, pid: int, text: str) -> bool:
        """Put ``text`` at the focused field's caret through Accessibility, replacing its selection."""
        element = self._focused_element(pid)
        if element is None or not self._settable(element, "AXSelectedText"):
            return False
        return self._set(element, "AXSelectedText", text) == 0

    # ------------------------------------------------------------------ actions
    # Actions that need no earlier state of the window, and actions that need
    # it only to resolve an element_index (otherwise they act on the focus).
    STATELESS = frozenset({"menu", "show_window", "wait", "open_file"})
    AT_FOCUS = frozenset({"copy", "press_key", "type_text", "paste"})

    def act(self, session: str, action: str, args: dict[str, Any]) -> str:
        """Run one action and return a short description of what happened."""
        _unlocked()
        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            raise ComputerUseError(f"unknown action {action!r}")
        app = self.resolve_app(args.get("app") or "", launch=action == "open_file")
        stateless = action in self.STATELESS or (action in self.AT_FOCUS and args.get("element_index") is None)
        if not stateless:
            # A window the model has not read fails here, before anything is waited for.
            self._state_for(session, args.get("app") or "", app)
        if action != "wait":
            # A step sent to a busy app would run whenever it comes back, on whatever it shows then.
            self._await_answer(app)
        with self._lock:
            if stateless:
                return handler(app, int(app.processIdentifier()), self._states.get(
                    (session, int(app.processIdentifier()))), args)
            app, state = self._state_for(session, args.get("app") or "", app)
            # The window may have moved since it was read; points follow it.
            self._follow_window(state)
            self._control(session, state.pid, str(app.localizedName() or ""), None, state.window_id)
            self._cue(app, state, action, args)
            return handler(app, int(app.processIdentifier()), state, args)

    def _control(self, session: str, pid: int, app_name: str, window, window_id: int) -> None:
        """Mark ``window_id`` as the window this session controls (see window_watch)."""
        if not window_id:
            return
        info: dict[str, Any] = {"pid": pid, "app_name": app_name}
        try:
            from openprogram.host.resources.native_window import control_owner
            info["owner"] = control_owner()
            if window is None:
                _, ax, _, _ = _frameworks()
                window = next((w for w in self._windows(ax.AXUIElementCreateApplication(pid))
                               if self._ax_window_id(w) == window_id), None)
            if window is not None:
                info["buttons"] = self._title_buttons(window)
        except Exception:
            # The overlay is a courtesy; a step never fails because of it.
            _log.debug("window overlay info unavailable pid=%s", pid, exc_info=True)
        self._watch.touch(session, window_id, info)

    def _title_buttons(self, window) -> list[int] | None:
        """Where the window's close, minimize and zoom buttons sit, relative to the window."""
        origin = self._frame(window)
        frames = [self._frame(button) for button in (self._attr(window, name) for name in
                  ("AXCloseButton", "AXMinimizeButton", "AXZoomButton")) if button is not None]
        frames = [f for f in frames if f is not None and f[2] > 0 and f[3] > 0]
        if origin is None or not frames:
            return None
        left, top = min(f[0] for f in frames), min(f[1] for f in frames)
        right, bottom = max(f[0] + f[2] for f in frames), max(f[1] + f[3] for f in frames)
        return [round(left - origin[0]), round(top - origin[1]), round(right - left), round(bottom - top)]

    def _cue(self, app, state, action: str, args: dict[str, Any]) -> None:
        """Show the user where this step lands: the App draws its agent cursor there."""
        try:
            # A window on another Space or in the Dock is not where the point is.
            if state.window_id and state.window_id not in self._onscreen_ids(int(app.processIdentifier())):
                return
            if args.get("element_index") is not None:
                point = self._center(self._element(state, args["element_index"]))
            elif args.get("x") is not None and args.get("y") is not None:
                point = self._point(state, args.get("x"), args.get("y"))
            else:
                return
            from openprogram.host.resources.native_window import note_action_point
            note_action_point(str(app.localizedName() or ""), point, action)
        except Exception:
            # The cue is a courtesy; a step never fails because of it.
            return
        # Let the cursor arrive before the step acts.
        time.sleep(CUE_LEAD_SECONDS)

    def _do_click(self, app, pid, state, args) -> str:
        button, count = click_args(args)
        try:
            flags = keymap.modifier_flags(args.get("key"))
        except keymap.KeyNameError as exc:
            raise ComputerUseError(str(exc)) from exc
        hold = _seconds(args.get("duration"), "duration")
        index = args.get("element_index")
        if index is not None:
            element = self._element(state, index)
            node = self._node_at(state, index)
            actions = self._actions(element)
            simple = count == 1 and not flags and not hold
            if simple and button == "left":
                row = self._row_of(element, node)
                for action in ("AXPress", "AXConfirm", "AXPick"):
                    if action in actions and self._perform(element, action) == 0:
                        return f"pressed {node.body()}"
                    # Outlook's message cells answer a press with an error and select the row all the same.
                    if action in actions and row is not None and self._attr(row, "AXSelected") is True:
                        return f"selected {node.body()}"
                if row is not None and self._select_row(row):
                    return f"selected {node.body()}"
                if node.role in _TEXT_ROLES and self._settable(element, "AXFocused"):
                    if self._set(element, "AXFocused", True) == 0:
                        return f"focused {node.body()}"
            if simple and button == "right" and "AXShowMenu" in actions:
                if self._perform(element, "AXShowMenu") == 0:
                    return f"opened the context menu of {node.body()}"
            self._background_click(state, pid, self._reachable_center(state, element), button, count, flags, hold)
            return f"{_click_words(button, count)} {node.body()}" + (f" (held {hold:g}s)" if hold else "")
        point = self._point(state, args.get("x"), args.get("y"))
        self._background_click(state, pid, point, button, count, flags, hold)
        return (f"{_click_words(button, count)} at ({args.get('x')}, {args.get('y')})"
                + (f" (held {hold:g}s)" if hold else ""))

    def _row_of(self, element, node):
        """The row of a list or table that ``element`` is or lies in, when it can be selected.

        Selecting the row is what a click on it does, and it needs no mouse:
        a mouse-down, even one delivered in the background, lets the app bring
        its window forward or make itself the front app.
        """
        if node.role not in ("AXRow", "AXOutlineRow", "AXCell"):
            return None
        candidate = element
        for _ in range(_ROW_DEPTH):
            if candidate is None:
                return None
            if self._settable(candidate, "AXSelected"):
                return candidate
            candidate = self._attr(candidate, "AXParent")
            if candidate is None or self._attr(candidate, "AXRole") not in ("AXRow", "AXOutlineRow", "AXCell"):
                return None
        return None

    def _select_row(self, row) -> bool:
        return self._attr(row, "AXSelected") is True or self._set(row, "AXSelected", True) == 0

    def _do_hover(self, app, pid, state, args) -> str:
        """Move the pointer over an element or point, in the app only, and rest there."""
        index = args.get("element_index")
        if index is not None:
            point, what = self._reachable_center(state, self._element(state, index)), self._node_at(state, index).body()
        else:
            point, what = self._point(state, args.get("x"), args.get("y")), f"({args.get('x')}, {args.get('y')})"
        rest = _seconds(args.get("duration"), "duration") or HOVER_SECONDS
        sky = self._route(state)
        _, _, cg, _ = _frameworks()
        group = sky.next_group()
        # Approach in a few steps so tracking areas see the pointer enter.
        for fraction in (0.0, 0.5, 1.0):
            at = (point[0] - 12 * (1 - fraction), point[1] - 12 * (1 - fraction))
            move = self._mouse_event(cg, cg.kCGEventMouseMoved, at, cg.kCGMouseButtonLeft)
            self._post_mouse(sky, cg, state, pid, move, at, click_state=0, button=0, subtype=SUBTYPE_TOUCH,
                             group=group)
            time.sleep(0.03)
        time.sleep(rest)
        return f"hovered over {what} for {rest:g}s"

    def _do_wait(self, app, pid, state, args) -> str:
        """Let time pass, then report what changed (a download, an animation, a reply)."""
        rest = _seconds(args.get("duration"), "duration") or 1.0
        deadline = time.monotonic() + rest
        while time.monotonic() < deadline:
            _check_cancelled()
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return f"waited {rest:g}s"

    def _do_drag(self, app, pid, state, args) -> str:
        start = self._point(state, args.get("x"), args.get("y"))
        end = self._point(state, args.get("to_x"), args.get("to_y"))
        self._background_drag(state, pid, start, end)
        return f"dragged from ({args.get('x')}, {args.get('y')}) to ({args.get('to_x')}, {args.get('to_y')})"

    def _do_scroll(self, app, pid, state, args) -> str:
        direction, pages, pixels = scroll_args(args)
        amount = f"{pixels:g} pixels" if pixels else f"{pages:g} page(s)"
        index = args.get("element_index")
        if index is not None and pixels:
            element = self._element(state, index)
            self._background_scroll(state, pid, self._center(element), direction, pages, pixels)
            return f"scrolled {direction} {amount} over {self._node_at(state, index).body()}"
        if index is not None:
            element = self._element(state, index)
            node = self._node_at(state, index)
            action = {"up": "AXScrollUpByPage", "down": "AXScrollDownByPage",
                      "left": "AXScrollLeftByPage", "right": "AXScrollRightByPage"}[direction]
            target = element
            for _ in range(12):
                if target is None:
                    break
                if action in self._actions(target):
                    for _ in range(max(1, int(round(pages)))):
                        self._perform(target, action)
                        time.sleep(0.05)
                    return f"scrolled {direction} {pages:g} page(s) in {node.body()}"
                bar = self._attr(target, "AXVerticalScrollBar" if direction in ("up", "down") else "AXHorizontalScrollBar")
                if bar is not None and self._settable(bar, "AXValue"):
                    current = self._attr(bar, "AXValue")
                    if isinstance(current, (int, float)):
                        step = self._page_fraction(target, direction) * pages
                        sign = -1 if direction in ("up", "left") else 1
                        self._set(bar, "AXValue", max(0.0, min(1.0, float(current) + sign * step)))
                        return f"scrolled {direction} {pages:g} page(s) in {node.body()}"
                target = self._attr(target, "AXParent")
            self._background_scroll(state, pid, self._center(element), direction, pages)
            return f"scrolled {direction} {pages:g} page(s) over {node.body()}"
        point = self._point(state, args.get("x"), args.get("y"))
        self._background_scroll(state, pid, point, direction, pages, pixels)
        return f"scrolled {direction} {amount} at ({args.get('x')}, {args.get('y')})"

    def _page_fraction(self, area, direction) -> float:
        frame = self._frame(area)
        contents = self._attr(area, "AXContents") or self._attr(area, "AXChildren") or []
        inner = self._frame(contents[0]) if contents else None
        if frame and inner:
            visible = frame[3] if direction in ("up", "down") else frame[2]
            total = inner[3] if direction in ("up", "down") else inner[2]
            if total > visible > 0:
                return 0.9 * visible / (total - visible)
        return 0.25

    def _do_press_key(self, app, pid, state, args) -> str:
        try:
            chord = keymap.parse_chord(str(args.get("key") or ""))
        except keymap.KeyNameError as exc:
            raise ComputerUseError(str(exc)) from exc
        if args.get("element_index") is not None:
            self._focus(self._element(state, args["element_index"]))
        if chord.text:
            self._refuse_secure_input()
        if not self._keys_reach(pid, state):
            raise ComputerUseError(
                "The window is on another Space, where macOS does not deliver keys to it. Use element actions "
                "(click, set_value, menu) instead; type_text and paste still insert text into a focused field.")
        self._refuse_file_panel(app, pid)
        self._post_chord(pid, chord, _seconds(args.get("duration"), "duration"))
        return f"pressed {args.get('key')}"

    def _do_type_text(self, app, pid, state, args) -> str:
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise ComputerUseError("text is required")
        if len(text) > MAX_TEXT_CHARS:
            raise ComputerUseError(f"text is longer than {MAX_TEXT_CHARS} characters; use paste")
        if args.get("element_index") is not None:
            element = self._element(state, args["element_index"])
            node = self._node_at(state, args["element_index"])
            self._refuse_secure(node)
            if node.role in _NOT_TEXT_ROLES:
                raise ComputerUseError(
                    f"element {args['element_index']} is a {role_name(node.role)}, not a text field: typed text "
                    "would land in whatever field has focus. Click it, use set_value, or pick the text field.")
            self._focus(element)
        self._refuse_secure(None, self._focused_element(pid))
        self._refuse_secure_input()
        if not self._keys_reach(pid, state):
            if not self._insert(pid, text):
                raise ComputerUseError(
                    "The window is on another Space, where macOS does not deliver keys to it, and the focused "
                    "element takes no inserted text. Focus a text field first, or use element actions.")
            newline_note = "; a newline went in as text, not as Return" if "\n" in text or "\r" in text else ""
            return (f"inserted {len(text)} character(s) at the focused field (the window is on another Space, "
                    f"where keys do not arrive{newline_note})")
        self._refuse_file_panel(app, pid)
        self._post_text(pid, text)
        return f"typed {len(text)} character(s)"

    def _do_set_value(self, app, pid, state, args) -> str:
        index = args.get("element_index")
        element = self._element(state, index)
        node = self._node_at(state, index)
        self._refuse_secure(node, element)
        if not self._settable(element, "AXValue"):
            raise ComputerUseError(f"element {index} has no settable value; use click or type_text")
        value = args.get("value")
        if value is None:
            raise ComputerUseError("value is required")
        current = self._attr(element, "AXValue")
        if isinstance(current, (int, float)) and not isinstance(current, bool):
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise ComputerUseError("this element takes a number") from exc
        elif not isinstance(value, str):
            value = str(value)
        code = self._set(element, "AXValue", value)
        if code != 0:
            raise ComputerUseError(f"the app refused the value (Accessibility error {code})")
        return f"set {node.body()} to {value!r}"

    def _do_perform_secondary_action(self, app, pid, state, args) -> str:
        index = args.get("element_index")
        element = self._element(state, index)
        node = self._node_at(state, index)
        wanted = str(args.get("action_name") or "").strip()
        if not wanted:
            raise ComputerUseError("action_name is required")
        actions = self._actions(element)
        normalized = {a.lower(): a for a in actions}
        squashed = "ax" + wanted.replace(" ", "").replace("_", "").lower().removeprefix("ax")
        match = normalized.get(wanted.lower()) or normalized.get(squashed)
        if match is None:
            listed = ", ".join(actions) or "none"
            raise ComputerUseError(f"element {index} does not expose {wanted!r}; its actions are: {listed}")
        code = self._perform(element, match)
        if code != 0:
            raise ComputerUseError(f"{match} failed (Accessibility error {code})")
        return f"performed {match} on {node.body()}"

    def _do_select_text(self, app, pid, state, args) -> str:
        _, ax, _, _ = _frameworks()
        index = args.get("element_index")
        element = self._element(state, index)
        node = self._node_at(state, index)
        value = self._attr(element, "AXValue")
        if not isinstance(value, str):
            raise ComputerUseError(f"element {index} has no text value to select in")
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise ComputerUseError("text is required")
        start = find_text(value, text, args.get("prefix") or "", args.get("suffix") or "")
        if start is None:
            raise ComputerUseError(f"{text!r} was not found in element {index} with that prefix and suffix")
        location = utf16_len(value[:start])
        length = utf16_len(text)
        kind = str(args.get("selection_type") or "text")
        if kind == "cursor_before":
            length = 0
        elif kind == "cursor_after":
            location, length = location + length, 0
        elif kind != "text":
            raise ComputerUseError("selection_type must be text, cursor_before or cursor_after")
        self._focus(element)
        selection = ax.AXValueCreate(ax.kAXValueCFRangeType, (location, length))
        code = self._set(element, "AXSelectedTextRange", selection)
        if code != 0:
            raise ComputerUseError(f"the app refused the selection (Accessibility error {code})")
        where = {"text": "selected", "cursor_before": "placed the cursor before", "cursor_after": "placed the cursor after"}[kind]
        return f"{where} {text!r} in {node.body()}"

    def _do_paste(self, app, pid, state, args) -> str:
        appkit, _, _, _ = _frameworks()
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise ComputerUseError("text is required")
        kind = str(args.get("format") or "text").lower()
        if kind == "markdown":
            kind = "md"
        if kind not in ("text", "md", "html"):
            raise ComputerUseError("format must be text, md or html")
        if args.get("element_index") is not None:
            element = self._element(state, args["element_index"])
            self._refuse_secure(self._node_at(state, args["element_index"]))
            self._focus(element)
        self._refuse_secure(None, self._focused_element(pid))
        self._refuse_secure_input()
        if not self._keys_reach(pid, state):
            plain = text if kind != "html" else _strip_tags(text)
            if not self._insert(pid, plain):
                raise ComputerUseError(
                    "The window is on another Space, where macOS does not deliver the paste shortcut, and the "
                    "focused element takes no inserted text. Focus a text field first, or use element actions.")
            return (f"inserted {len(plain)} character(s) at the focused field as plain text (the window is on "
                    "another Space, where the paste shortcut does not arrive)")
        self._refuse_file_panel(app, pid)
        board = appkit.NSPasteboard.generalPasteboard()
        saved = _save_pasteboard(board)
        board.clearContents()
        if kind == "text":
            board.setString_forType_(text, appkit.NSPasteboardTypeString)
        else:
            html = markdown_to_html(text) if kind == "md" else text
            board.setString_forType_(html, appkit.NSPasteboardTypeHTML)
            board.setString_forType_(text if kind == "md" else _strip_tags(text), appkit.NSPasteboardTypeString)
        written = board.changeCount()
        try:
            self._post_chord(pid, keymap.parse_chord("cmd+v"))
            time.sleep(0.6)
        finally:
            # Leave a clipboard the user changed meanwhile alone.
            if board.changeCount() == written:
                _restore_pasteboard(board, saved)
        return f"pasted {len(text)} character(s) as {'markdown' if kind == 'md' else kind}"


    def _do_focus(self, app, pid, state, args) -> str:
        index = args.get("element_index")
        element = self._element(state, index)
        node = self._node_at(state, index)
        if "secure" in node.flags:
            raise ComputerUseError("This is a password field; computer_use does not focus or type into it.")
        if not self._settable(element, "AXFocused"):
            raise ComputerUseError(f"element {index} cannot take keyboard focus; click it instead")
        if self._attr(element, "AXFocused") is True:
            return Report(f"already focused {node.body()}")
        self._set(element, "AXFocused", True)
        # Apps apply focus on their next run-loop turn, sometimes later.
        deadline = time.monotonic() + 0.5
        while self._attr(element, "AXFocused") is not True and time.monotonic() < deadline:
            time.sleep(0.05)
        if self._attr(element, "AXFocused") is not True:
            raise ComputerUseError(f"focus did not move to element {index}; the app keeps its text cursor in "
                                   "another window. Click the element instead.")
        return f"focused {node.body()}"

    def _do_copy(self, app, pid, state, args) -> str:
        """The app's own Copy, read back from the clipboard, which is then restored."""
        appkit, _, _, _ = _frameworks()
        if args.get("element_index") is not None:
            element = self._element(state, args["element_index"])
            self._refuse_secure(self._node_at(state, args["element_index"]))
            self._focus(element)
        self._refuse_secure(None, self._focused_element(pid))
        board = appkit.NSPasteboard.generalPasteboard()
        saved = _save_pasteboard(board)
        before = board.changeCount()
        item = self._shortcut_item(pid, "c", enabled_only=False)
        if item is not None and self._attr(item, "AXEnabled") is False:
            raise ComputerUseError(f"{app.localizedName()}'s Copy is disabled right now: nothing is selected, or "
                                   "focus is on a control. Select text first (select_text, or press_key cmd+a).")
        try:
            if item is None or self._perform(item, "AXPress") != 0:
                self._post_chord(pid, keymap.parse_chord("cmd+c"))
            deadline = time.monotonic() + 1.5
            while board.changeCount() == before and time.monotonic() < deadline:
                time.sleep(0.05)
            if board.changeCount() == before:
                raise ComputerUseError("Copy put nothing on the clipboard; select something first "
                                       "(select_text, or press_key cmd+a).")
            text = str(board.stringForType_(appkit.NSPasteboardTypeString) or "")
        finally:
            if board.changeCount() != before:
                _restore_pasteboard(board, saved)
        if not text:
            return Report("copied something that is not text (an image or a file)")
        clipped = text if len(text) <= MAX_COPY_CHARS else text[:MAX_COPY_CHARS] + "\n…(truncated)"
        return Report(f"copied {len(text)} character(s); the clipboard was restored. Copied text:\n<<<\n{clipped}\n>>>")

    # ------------------------------------------------------------------ menu bar
    def _menu_children(self, item) -> list[Any]:
        """Items under a menu bar item or a submenu item."""
        for child in self._attr(item, "AXChildren") or []:
            if str(self._attr(child, "AXRole") or "") == "AXMenu":
                return [c for c in self._attr(child, "AXChildren") or []]
        return []

    def _menu_title(self, item) -> str:
        return str(self._attr(item, "AXTitle") or "")

    def _menu_line(self, item) -> str:
        title = self._menu_title(item)
        values = self._attrs(item, ["AXEnabled", "AXMenuItemCmdChar", "AXMenuItemCmdModifiers",
                                    "AXMenuItemMarkChar"])
        parts = [title]
        char = values.get("AXMenuItemCmdChar")
        if isinstance(char, str) and char.strip():
            mods = values.get("AXMenuItemCmdModifiers")
            mods = int(mods) if isinstance(mods, (int, float)) else 0
            # kAXMenuItemModifierShift 1, Option 2, Control 4, NoCommand 8
            glyphs = ("⌃" if mods & 4 else "") + ("⌥" if mods & 2 else "") + ("⇧" if mods & 1 else "") \
                + ("" if mods & 8 else "⌘")
            parts.append(f"({glyphs}{char.strip().upper()})")
        if isinstance(values.get("AXMenuItemMarkChar"), str) and values["AXMenuItemMarkChar"].strip():
            parts.append("[checked]")
        if values.get("AXEnabled") is False:
            parts.append("[disabled]")
        if self._menu_children(item):
            parts.append("▸")
        return " ".join(parts)

    def _shortcut_item(self, pid: int, char: str, modifiers: int = 0, *, enabled_only: bool = True):
        """The menu item whose shortcut is ⌘<char>, in any locale (enabled ones only, by default)."""
        _, ax, _, _ = _frameworks()
        bar = self._attr(ax.AXUIElementCreateApplication(pid), "AXMenuBar")
        for top in self._attr(bar, "AXChildren") or [] if bar is not None else []:
            for item in self._menu_children(top):
                values = self._attrs(item, ["AXMenuItemCmdChar", "AXMenuItemCmdModifiers", "AXEnabled"])
                if (str(values.get("AXMenuItemCmdChar") or "").lower() == char
                        and int(values.get("AXMenuItemCmdModifiers") or 0) == modifiers
                        and (values.get("AXEnabled") is not False or not enabled_only)):
                    return item
        return None

    def _do_menu(self, app, pid, state, args) -> str:
        """Walk the menu bar by title: list a menu, or press a leaf item, without opening anything."""
        _, ax, _, _ = _frameworks()
        path = menu_path(args)
        bar = self._attr(ax.AXUIElementCreateApplication(pid), "AXMenuBar")
        if bar is None:
            raise ComputerUseError(f"{app.localizedName()} has no menu bar")
        if not path:
            titles = [self._menu_title(item) for item in self._attr(bar, "AXChildren") or []]
            titles = [t for t in titles if t and t != "Apple"]
            return Report("menu bar: " + " | ".join(titles) + ". Pass menu_path to list a menu or press an item.")
        level = list(self._attr(bar, "AXChildren") or [])
        item, walked = None, []
        for step in path:
            wanted = _menu_key(step)
            item = next((i for i in level if _menu_key(self._menu_title(i)) == wanted), None)
            if item is None:
                titles = [self._menu_title(i) for i in level if self._menu_title(i)]
                where = " > ".join(walked) or "the menu bar"
                raise ComputerUseError(f"{step!r} is not in {where}; it has: " + " | ".join(titles))
            walked.append(self._menu_title(item))
            level = self._menu_children(item)
        if level:
            lines = [self._menu_line(i) for i in level if self._menu_title(i)]
            return Report(" > ".join(walked) + ":\n" + "\n".join(f"- {line}" for line in lines))
        if self._attr(item, "AXEnabled") is False:
            raise ComputerUseError(" > ".join(walked) + " is disabled right now")
        code = self._perform(item, "AXPress")
        if code != 0:
            raise ComputerUseError(f"pressing {' > '.join(walked)} failed (Accessibility error {code})")
        return "chose " + " > ".join(walked)

    def _do_open_file(self, app, pid, state, args) -> str:
        """Open a document in the app the way Finder's Open With does, leaving the app in the background."""
        raw = str(args.get("path") or "").strip()
        if not raw:
            raise ComputerUseError("path is required: the absolute path of the file to open")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise ComputerUseError(f"path must be absolute, not {raw!r}")
        if not path.exists():
            raise ComputerUseError(f"{path} does not exist")
        name = str(app.localizedName() or "the app")
        if (path.suffix.lower() in _LAUNCHING_SUFFIXES or (path.is_file() and os.access(path, os.X_OK))
                or str(app.bundleIdentifier() or "").lower() in _SHELL_BUNDLES):
            raise ComputerUseError(f"open_file opens documents only; opening {path.name} in {name} could run it.")
        cancelled = self._cancel_file_panel(pid, name)
        appkit = _appkit()
        config = appkit.NSWorkspaceOpenConfiguration.configuration()
        config.setActivates_(False)
        finished, errors = threading.Event(), []

        def done(_running, error):
            errors.append(error)
            finished.set()

        appkit.NSWorkspace.sharedWorkspace().openURLs_withApplicationAtURL_configuration_completionHandler_(
            [appkit.NSURL.fileURLWithPath_(str(path))], app.bundleURL(), config, done)
        deadline = time.monotonic() + OPEN_TIMEOUT
        while not finished.wait(0.1):
            _check_cancelled()
            if time.monotonic() > deadline:
                raise ComputerUseError(f"{name} did not confirm opening {path} within {OPEN_TIMEOUT:g} seconds")
        if errors and errors[0] is not None:
            raise ComputerUseError(f"{name} could not open {path}: {errors[0].localizedDescription()}")
        title = self._document_window(pid, path)
        return (("cancelled the file dialog and " if cancelled else "") + f"opened {path} in {name} in the background"
                + (f'; its window is "{title}"' if title else ""))

    def _cancel_file_panel(self, pid: int, name: str, seconds: float = 3.0) -> bool:
        """Close the app's open file dialog with its Cancel button; False when none is open.

        A modal dialog holds back the request to open a document until it
        closes, so ``open_file`` cancels it first.
        """
        panel = self._open_panel(pid)
        if panel is None:
            return False
        if panel is True:
            raise ComputerUseError(
                f"{name} has the system file dialog on screen, but Accessibility does not reach it to press Cancel. "
                "Read it with get_app_state(window=<its title>) and click its Cancel button, then call open_file again.")
        button = self._attr(panel, "AXCancelButton") or self._cancel_button(panel)
        if button is None or self._perform(button, "AXPress") != 0:
            raise ComputerUseError(f"{name} has the system file dialog open and its Cancel button could not be "
                                   "pressed. Click Cancel in the dialog, then call open_file again.")
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(0.15)
            if self._open_panel(pid) is None:
                return True
        raise ComputerUseError(f"{name}'s file dialog did not close after Cancel was pressed.")

    def _cancel_button(self, panel, budget: int = 400):
        """A button of ``panel`` titled Cancel, searched breadth first."""
        queue = list(self._attr(panel, "AXChildren") or [])
        while queue and budget > 0:
            budget -= 1
            element = queue.pop(0)
            if (self._attr(element, "AXRole") == "AXButton"
                    and str(self._attr(element, "AXTitle") or "").strip().lower() in _CANCEL_TITLES):
                return element
            queue.extend(self._attr(element, "AXChildren") or [])
        return None

    def _document_window(self, pid: int, path: Path, seconds: float = 5.0) -> str:
        """The title of the window showing ``path``, once the app has one."""
        _, ax, _, _ = _frameworks()
        root = ax.AXUIElementCreateApplication(pid)
        url = str(_appkit().NSURL.fileURLWithPath_(str(path)).absoluteString())
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for window in self._windows(root):
                title = str(self._attr(window, "AXTitle") or "")
                if str(self._attr(window, "AXDocument") or "") == url or path.name in title:
                    return title
            time.sleep(0.15)
        return ""

    # ------------------------------------------------------------------ windows
    def list_windows(self, query: str) -> str:
        _unlocked()
        _, ax, cg, _ = _frameworks()
        app = self.resolve_app(query, launch=False)
        self._await_answer(app)
        pid = int(app.processIdentifier())
        root = ax.AXUIElementCreateApplication(pid)
        focused = self._attr(root, "AXFocusedWindow")
        main = self._attr(root, "AXMainWindow")
        onscreen = {int(r.get("kCGWindowNumber", 0)) for r in cg.CGWindowListCopyWindowInfo(
            cg.kCGWindowListOptionOnScreenOnly, cg.kCGNullWindowID) or []
            if int(r.get("kCGWindowOwnerPID", -1)) == pid}
        lines = [f"Windows of {app.localizedName()} (pid {pid})" + (", app hidden" if app.isHidden() else "") + ":"]
        windows = self._windows(root)
        for position, window in enumerate(windows):
            frame = self._frame(window)
            wid = self._window_id(window, pid, frame)
            notes = []
            if window == main:
                notes.append("main")
            if window == focused:
                notes.append("focused")
            if self._attr(window, "AXMinimized"):
                notes.append("minimized")
            elif wid and wid not in onscreen and not app.isHidden():
                notes.append("on another Space")
            # A minimized window reports a subrole of its Dock tile, not its own.
            subrole = "" if self._attr(window, "AXMinimized") else str(self._attr(window, "AXSubrole") or "")
            if subrole and subrole != "AXStandardWindow":
                notes.append(role_name("AXWindow", subrole).split("(", 1)[-1].rstrip(")"))
            title = str(self._attr(window, "AXTitle") or "") or "(untitled)"
            bounds = f"{int(frame[2])}x{int(frame[3])} at ({int(frame[0])}, {int(frame[1])})" if frame else "no frame"
            lines.append(f"- {position}: \"{title}\" window_id {wid or '?'}, {bounds}"
                         + (f" [{', '.join(notes)}]" if notes else ""))
        if not windows:
            lines.append("- none open")
        return "\n".join(lines)

    def _do_show_window(self, app, pid, state, args) -> str:
        """Bring a minimized window, or the windows of a hidden app, back without activating the app."""
        _, ax, _, _ = _frameworks()
        root = ax.AXUIElementCreateApplication(pid)
        window = self._pick_window(root, args.get("window"), window_id=args.get("window_id"),
                                   prefer_id=state.window_id if state else 0)
        if window is None:
            raise ComputerUseError(f"{app.localizedName()} has no open window")
        done = []
        if app.isHidden():
            app.unhide()
            done.append("unhid the app")
        if self._attr(window, "AXMinimized"):
            self._set(window, "AXMinimized", False)
            done.append("restored the window from the Dock")
        if not done:
            frame = self._frame(window)
            wid = self._window_id(window, pid, frame)
            if wid and wid not in self._onscreen_ids(pid):
                return Report(
                    "the window is on another Space and stays there: macOS lets only the Dock move windows between "
                    "Spaces. Screenshots, clicks and element actions reach it as it is; keys do not, so type_text "
                    "and paste insert into the focused field and press_key fails.")
            return Report("the window is already showing")
        time.sleep(0.4)
        return "; ".join(done) + " (the app stays in the background)"

    def _onscreen_ids(self, pid: int) -> set[int]:
        _, _, cg, _ = _frameworks()
        return {int(r.get("kCGWindowNumber", 0)) for r in cg.CGWindowListCopyWindowInfo(
            cg.kCGWindowListOptionOnScreenOnly, cg.kCGNullWindowID) or []
            if int(r.get("kCGWindowOwnerPID", -1)) == pid}

    # ------------------------------------------------------------------ reading aids
    def zoom(self, session: str, query: str, x, y, width, height) -> Observation:
        """A region of the last screenshot at the display's full pixel density."""
        _unlocked()
        with self._lock:
            app, state = self._state_for(session, query)
            if not state.has_screenshot:
                raise ComputerUseError("This window has no screenshot to zoom into; call get_app_state first.")
            try:
                px, py, pw, ph = (float(v) for v in (x, y, width, height))
            except (TypeError, ValueError) as exc:
                raise ComputerUseError("zoom needs x, y, width and height in screenshot pixels") from exc
            full_w, full_h = state.size[0] * state.scale, state.size[1] * state.scale
            px, py = max(0.0, px), max(0.0, py)
            pw, ph = min(pw, full_w - px), min(ph, full_h - py)
            if pw < 4 or ph < 4:
                raise ComputerUseError(f"the region must lie inside the {int(full_w)}x{int(full_h)} screenshot")
            region = (px / state.scale, py / state.scale, pw / state.scale, ph / state.scale)
            image, scale = self._capture(state.pid, (*state.origin, *state.size), state.window_id, region=region)
            if image is None:
                raise ComputerUseError("The window could not be captured; call get_app_state again.")
            text = (f"Zoomed region ({int(px)}, {int(py)}) {int(pw)}x{int(ph)} of the last screenshot, at "
                    f"{scale:g}x pixels per point. Coordinates for actions still refer to the last screenshot, "
                    "not to this image.")
            return Observation(text=text, image=image, app=str(app.localizedName() or query))

    def find(self, session: str, query: str, text: str = "", role: str = "") -> str:
        """Elements of the last full tree whose label, value or placeholder contains ``text``."""
        with self._lock:
            _, state = self._state_for(session, query)
            needle = " ".join(str(text or "").split()).lower()
            wanted_role = str(role or "").strip().lower()
            if not needle and not wanted_role:
                raise ComputerUseError("find needs text, role, or both")
            hits = []
            for node in state.nodes:
                if wanted_role and wanted_role not in (node.role.lower(), role_name(node.role).lower(),
                                                       role_name(node.role, node.subrole).lower()):
                    continue
                haystack = " ".join(_BIDI_MARKS.sub("", f"{node.label} {node.value} {node.placeholder}").split()).lower()
                if needle and needle not in haystack:
                    continue
                hits.append(f"{node.index} {node.body()}")
            if not hits and needle and not wanted_role and state.has_screenshot:
                if not state.text_boxes:
                    image, scale = self._capture(state.pid, (*state.origin, *state.size), state.window_id)
                    if image is not None and abs(scale - state.scale) < 1e-3:
                        state.text_boxes = self._read_text(image, (*state.origin, *state.size), scale)
                seen = ocr.matching(state.text_boxes, needle)
                if seen:
                    return (f"no element matches, but the screenshot shows {len(seen)} line(s) with that text "
                            "(OCR; act with x,y at the centre):\n" + ocr.render(seen[:60]))
            if not hits:
                return "no element of the last tree matches; call get_app_state for a fresh tree, or scroll"
            more = f"\n…and {len(hits) - 60} more; narrow the search." if len(hits) > 60 else ""
            return f"{len(hits)} match(es):\n" + "\n".join(hits[:60]) + more

    # ------------------------------------------------------------------ audio
    def start_audio(self, session: str, query: str, max_duration=None) -> Report:
        """Record what ``query`` plays until stop_audio or the limit."""
        try:
            limit = audio.seconds(max_duration)
        except ValueError as exc:
            raise ComputerUseError(str(exc)) from exc
        with self._lock:
            _frameworks()
            running = self._recordings.get(session)
            if running is not None and not running.stopped:
                raise ComputerUseError(f"Already recording {running.app}; call stop_audio_recording first.")
            app = self.resolve_app(query)
            name = str(app.localizedName() or query)
            try:
                recording = audio.start(int(app.processIdentifier()), name, limit)
            except RuntimeError as exc:
                raise ComputerUseError(f"Could not record {name}: {exc}") from exc
            self._recordings[session] = recording
            return Report(f"Recording the sound {name} plays, for up to {limit:g} seconds; the user still hears it "
                          "and nothing else is recorded. Act as usual, then call stop_audio_recording.")

    def stop_audio(self, session: str, query: str = "") -> AudioClip:
        """Stop this session's recording and save it as a WAV."""
        with self._lock:
            recording = self._recordings.get(session)
            if recording is None:
                raise ComputerUseError("Nothing is being recorded; call start_audio_recording first.")
            if query:
                app = self._running_match(query)
                if app is not None and int(app.processIdentifier()) != recording.pid:
                    raise ComputerUseError(f"The recording is of {recording.app}, not {query}.")
            del self._recordings[session]
        data, peak = audio.finish(recording)
        return AudioClip(app=recording.app, path=_save_recording(recording.app, data), data=data,
                         seconds=round(recording.seconds, 2), peak=peak)

    def settle(self, session: str, query: str) -> float:
        """Wait for the window to stop changing after an action; returns the seconds waited."""
        _, ax, _, _ = _frameworks()
        app = self.resolve_app(query, launch=False)
        began = time.monotonic()
        # A step the app is still carrying out leaves it silent; what it shows is read once it answers.
        self._await_answer(app)
        pid = int(app.processIdentifier())
        previous = self._states.get((session, pid))
        root = ax.AXUIElementCreateApplication(pid)
        window = self._pick_window(root, None, prefer_id=previous.window_id if previous else 0)
        start = time.monotonic()
        time.sleep(SETTLE_MIN)
        last, busy = self._quick_signature(root, window)
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= (BUSY_MAX if busy else SETTLE_MAX):
                break
            _check_cancelled()
            time.sleep(0.2)
            current, busy = self._quick_signature(root, window)
            if current == last and not busy:
                break
            last = current
        return time.monotonic() - began

    def _quick_signature(self, root, window) -> tuple[tuple, bool]:
        """A cheap fingerprint of the first QUICK_NODES elements, and whether anything shows busy."""
        out, busy = [], False
        queue = [window] if window is not None else []
        for child in self._attr(root, "AXChildren") or []:
            if str(self._attr(child, "AXRole") or "") in ("AXMenu", "AXSheet", "AXPopover"):
                queue.append(child)
        seen: set = {root}
        while queue and len(out) < QUICK_NODES:
            element = queue.pop(0)
            try:
                if element in seen:
                    continue
                seen.add(element)
            except TypeError:
                pass
            values = self._attrs(element, ["AXRole", "AXTitle", "AXValue", "AXEnabled", "AXChildren"])
            role = str(values.get("AXRole") or "")
            value = values.get("AXValue")
            if role in _BUSY_ROLES and not isinstance(value, (int, float)) or (
                    role == "AXProgressIndicator" and isinstance(value, (int, float)) and value < 1):
                busy = True
            out.append((role, str(values.get("AXTitle") or ""), str(value) if value is not None else "",
                        values.get("AXEnabled")))
            queue.extend(values.get("AXChildren") or [])
        return tuple(out), busy


# ---------------------------------------------------------------------- helpers
def _report_control(info: dict[str, Any], payload: dict[str, Any]) -> None:
    from openprogram.host.resources.native_window import note_control
    note_control(payload, owner=info.get("owner"))



def _intersect(a: tuple[float, float, float, float],
               b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """The overlap of two x, y, width, height rectangles (zero size when they do not meet)."""
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return left, top, max(0.0, right - left), max(0.0, bottom - top)


def screen_point(px: float, py: float, *, origin: tuple[float, float], scale: float) -> tuple[float, float]:
    """A pixel of a window's screenshot as a screen point.

    ``scale`` is the screenshot's pixels per point (2 on a Retina display,
    less when a large window was captured smaller); ``origin`` is the
    window's top left in screen points, top-left origin of the main display.
    """
    return origin[0] + px / scale, origin[1] + py / scale


def window_location(point: tuple[float, float], origin: tuple[float, float]) -> tuple[float, float]:
    """A screen point inside the window, measured from its top left, in points.

    A pointer event posted to one window carries this beside its screen
    location (``CGEventSetWindowLocation``), as KWWK computer-use-core and
    cua-driver set it. AppKit places the click from the screen location: a
    probe window took the click at the same point with either value here.
    """
    return point[0] - origin[0], point[1] - origin[1]


def live_origin(rows: list[dict], window_id: int) -> tuple[float, float] | None:
    """The window's top left now, from the window server's rows, if listed."""
    for row in rows:
        if int(row.get("kCGWindowNumber", 0)) == window_id:
            b = row.get("kCGWindowBounds") or {}
            if float(b.get("Width", 0)) > 0 and float(b.get("Height", 0)) > 0:
                return float(b.get("X", 0)), float(b.get("Y", 0))
    return None


def _bounds(row: dict) -> tuple[float, float, float, float]:
    b = row.get("kCGWindowBounds") or {}
    x, y = float(b.get("X", 0)), float(b.get("Y", 0))
    return x, y, x + float(b.get("Width", 0)), y + float(b.get("Height", 0))


def covering_rows(rows: list[dict], window_id: int, front_pid: int) -> list[dict]:
    """The windows above ``window_id`` that overlap it, deepest first, without the front app's.

    ``rows`` is the window server's on-screen list, front to back.
    """
    rows = [r for r in rows if r.get("kCGWindowLayer") == 0]
    ids = [int(r.get("kCGWindowNumber", 0)) for r in rows]
    if not window_id or window_id not in ids:
        return []
    target = _bounds(rows[ids.index(window_id)])
    above = [r for r in rows[:ids.index(window_id)] if int(r.get("kCGWindowOwnerPID", -1)) != front_pid]
    return [r for r in reversed(above) if (lambda b: b[0] < target[2] and target[0] < b[2]
                                           and b[1] < target[3] and target[1] < b[3])(_bounds(r))]


def _same_frame(a, b, tolerance: float = 3.0) -> bool:
    return all(abs(float(x) - float(y)) < tolerance for x, y in zip(a, b))


def panel_hosts(rows: list[dict], pid: int, services: set[int]) -> list[tuple[int, int, int, tuple]]:
    """The out-of-process file dialogs on screen for ``pid``, as (service pid,
    service window id, app window id, bounds as x, y, width, height).

    Such a dialog is two layer-0 windows with the same bounds: the panel
    service's, which holds its Accessibility tree and stays off screen, and
    the app's, which shows it on screen.
    """
    def frame(row):
        b = row.get("kCGWindowBounds") or {}
        return float(b.get("X", 0)), float(b.get("Y", 0)), float(b.get("Width", 0)), float(b.get("Height", 0))

    def sized(row):
        f = frame(row)
        return row.get("kCGWindowLayer") == 0 and f[2] >= 80 and f[3] >= 60

    hosts = [r for r in rows
             if int(r.get("kCGWindowOwnerPID", -1)) == pid and sized(r) and r.get("kCGWindowIsOnscreen")]
    found = []
    for row in rows:
        owner = int(row.get("kCGWindowOwnerPID", -1))
        if owner not in services or not sized(row):
            continue
        host = next((h for h in hosts if _same_frame(frame(h), frame(row))), None)
        if host is not None:
            found.append((owner, int(row.get("kCGWindowNumber", 0)), int(host.get("kCGWindowNumber", 0)), frame(row)))
    return found


def _await(start):
    done, result = threading.Event(), []

    def callback(value, error):
        result.extend((value, error))
        done.set()

    start(callback)
    deadline = time.monotonic() + 8
    while not done.wait(0.02):
        _check_cancelled()
        if time.monotonic() >= deadline:
            raise ComputerUseError("Window capture timed out")
    if result[1] is not None or result[0] is None:
        raise ComputerUseError(f"Window capture failed: {result[1]}")
    return result[0]


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def markdown_to_html(text: str) -> str:
    from markdown_it import MarkdownIt
    return MarkdownIt("commonmark").enable("table").enable("strikethrough").render(text)


def _strip_tags(html: str) -> str:
    import re
    from html import unescape
    return unescape(re.sub(r"<[^>]+>", "", html))


def _save_pasteboard(board) -> list[dict[str, bytes]]:
    saved = []
    for item in board.pasteboardItems() or []:
        entry = {}
        for kind in item.types() or []:
            data = item.dataForType_(kind)
            if data is not None:
                entry[str(kind)] = bytes(data)
        saved.append(entry)
    return saved


def _restore_pasteboard(board, saved) -> None:
    import AppKit
    board.clearContents()
    items = []
    for entry in saved:
        item = AppKit.NSPasteboardItem.alloc().init()
        for kind, data in entry.items():
            item.setData_forType_(AppKit.NSData.dataWithBytes_length_(data, len(data)), kind)
        items.append(item)
    if items:
        board.writeObjects_(items)


_BACKEND: MacComputer | None = None
_BACKEND_LOCK = threading.Lock()


def backend() -> MacComputer:
    global _BACKEND
    with _BACKEND_LOCK:
        if _BACKEND is None:
            _BACKEND = MacComputer()
        return _BACKEND


__all__ = ["AudioClip", "ComputerUseError", "INCLUDE", "MacComputer", "OWN_BUNDLES", "Observation", "Report",
           "backend", "find_text", "markdown_to_html", "render", "support", "utf16_len"]

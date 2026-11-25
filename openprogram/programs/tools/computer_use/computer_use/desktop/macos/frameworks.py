"""The macOS frameworks the backend loads, the grants it needs, and the limits every part reads."""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib.util
from pathlib import Path
import platform
import threading
from typing import Any
import logging

from ..common import ocr
from ..common.base import ComputerUseError, PermissionMissing, NotDispatched
from ..common.tree import Node, Snapshot


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
                   "separate process: clicks (element or x,y) and set_value reach it in the background, keys "
                   "and typed text do not, and its file list is only in the screenshot and the text read off it.")
FILE_PANEL_OPEN_NOTE = ("The app has the system file dialog open, so keys sent to this window do not arrive. To open "
                        "a file, call open_file(app, path) with its absolute path; get_state(window=<the "
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
# The browser names computer_use takes as a browser's app, for the window of a browser whose macOS name is
# longer ("chrome" is Google Chrome): matched only when no application has the name itself.
_BROWSER_NAMES = {"chrome": "google chrome", "chrome-beta": "google chrome beta", "chrome-dev": "google chrome dev",
                  "chrome-canary": "google chrome canary", "edge": "microsoft edge",
                  "edge-beta": "microsoft edge beta", "edge-dev": "microsoft edge dev",
                  "edge-canary": "microsoft edge canary", "brave": "brave browser"}

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
# A browser window's page: what a browser target claims (see base.claimed).
_CLAIMED_ROLES = frozenset({"AXWebArea"})


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
        raise PermissionMissing(ACCESSIBILITY_MISSING)
    return appkit, ax, cg, sc


ACCESSIBILITY_MISSING = ("OpenProgram does not have Accessibility access, which reading and operating windows "
                         "needs. Grant it in System Settings > Privacy & Security > Accessibility, then try again.")
SCREEN_RECORDING_MISSING = ("OpenProgram does not have Screen Recording access, which window screenshots need. "
                            "Grant it in System Settings > Privacy & Security > Screen & System Audio Recording, "
                            "then try again.")


def missing_permissions() -> list[str]:
    """The grants this process lacks now, each with where to grant it.

    Both checks only read the state: neither shows a prompt, and nothing is
    captured or sent.
    """
    if not support()["available"]:
        return []
    try:
        _, ax, cg, _ = _Frameworks.get()
    except Exception:
        return []
    missing = []
    if not ax.AXIsProcessTrusted():
        missing.append(ACCESSIBILITY_MISSING)
    preflight = getattr(cg, "CGPreflightScreenCaptureAccess", None)
    if preflight is not None and not preflight():
        missing.append(SCREEN_RECORDING_MISSING)
    return missing


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
    # The window's size when it was last followed; a different one from ``size`` means it was resized since.
    live_size: tuple[float, float] | None = None
    scale: float = 1.0           # screenshot pixels per screen point
    has_screenshot: bool = False
    sparse: bool = False         # the tree shows little of the window, so reads add OCR text and shapes
    text_boxes: list[ocr.TextBox] = field(default_factory=list)
    shapes: list[ocr.Shape] = field(default_factory=list)
    # Every window of the app at that read, so a later read can tell one that opened since.
    window_keys: frozenset = frozenset()
    # Roles whose subtrees were left out because another target operates them (see base.claimed).
    left_out: frozenset = frozenset()

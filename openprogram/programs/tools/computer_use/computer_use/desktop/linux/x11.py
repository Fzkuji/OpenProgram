"""The X server and the session: AT-SPI, GDK and GIO loaded once, ``xdotool``/``xprop`` calls, window stacking and classes, processes, and the records the backend keeps."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import threading
from typing import Any

from ..common import audio
from ..common import ocr
from ..common.base import ComputerUseError
from ..common.tree import Node, Snapshot


# A browser window's page: what a browser target claims (see base.claimed).
_CLAIMED_ROLES = frozenset({"document web", "document frame"})

# OpenProgram's own windows, refused where they could show an approval card.
OWN_APPS = frozenset({"ai.openprogram.desktop", "openprogram"})

MAX_NODES = 1500
MAX_DEPTH = 60
MAX_IMAGE_SIDE = 1568
SETTLE_MIN = 0.3
SETTLE_MAX = 1.5
BUSY_MAX = 5.0
QUICK_NODES = 400
NOTCHES_PER_PAGE = 5
PIXELS_PER_NOTCH = 40
LAUNCH_SECONDS = 10.0

_WINDOW_ROLES = frozenset({"frame", "window", "dialog", "alert", "file chooser", "color chooser", "font chooser"})
# Unnamed instances of these only group other elements; their children take their place.
_TRANSPARENT = frozenset({"filler", "panel", "section", "unknown", "redundant object", "grouping",
                          "layered pane", "viewport", "block quote", "form"})
# Actions an element performs by being clicked; left out of the listed secondary actions.
_PRIMARY = ("click", "press", "activate", "toggle", "jump", "open", "select")
_SECURE = "password text"
_TOGGLE_ROLES = frozenset({"check box", "radio button", "toggle button", "check menu item",
                           "radio menu item", "switch"})
_NOT_TEXT_ROLES = frozenset({"push button", "toggle button", "check box", "radio button", "menu item",
                             "menu", "menu bar", "link", "image", "icon", "label", "list item",
                             "table cell", "page tab", "slider", "scroll bar", "switch"})
_BUSY_ROLES = frozenset({"progress bar", "animation"})
_MENU_ITEMS = frozenset({"menu", "menu item", "check menu item", "radio menu item", "tearoff menu item"})
_BUTTONS = {"left": 1, "middle": 2, "right": 3}
# GTK 4 names an unnamed widget after its class ("AdwGizmo", "GtkMenuButton"): not a label.
_CLASS_NAME = re.compile(r"(?:Gtk|Adw|Hdy|Gdk|Gsk)[A-Z]\w*|[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+")
_MODIFIERS = {"shift": "shift", "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "option": "alt", "opt": "alt",
              "super": "super", "cmd": "super", "command": "super", "meta": "super", "win": "super"}


def support() -> dict[str, Any]:
    """Whether this host can run the backend."""
    if platform.system() != "Linux":
        return {"available": False, "reason": "the Linux backend runs on Linux only"}
    if not os.environ.get("DISPLAY"):
        return {"available": False, "reason": "computer_use on Linux needs an X11 session (DISPLAY is not set)"}
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" and not os.environ.get("OPENPROGRAM_ALLOW_XWAYLAND"):
        return {"available": False, "reason": "computer_use on Linux needs an X11 session: Wayland does not let one "
                "app read or drive another's windows. Log in with an X11 (Xorg) session."}
    try:
        _atspi()
    except Exception as exc:  # noqa: BLE001 - any import failure means no backend
        return {"available": False, "reason": f"AT-SPI is not available ({exc}); install python3-gi and "
                "gir1.2-atspi-2.0"}
    if shutil.which("xdotool") is None:
        return {"available": False, "reason": "xdotool is not installed"}
    return {"available": True, "reason": ""}


_GI_LOCK = threading.Lock()
_GI: dict[str, Any] = {}


def _atspi():
    with _GI_LOCK:
        if "Atspi" not in _GI:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi
            Atspi.init()
            _GI["Atspi"] = Atspi
        return _GI["Atspi"]


def _gdk():
    with _GI_LOCK:
        if "Gdk" not in _GI:
            import gi
            gi.require_version("Gdk", "3.0")
            gi.require_version("GdkX11", "3.0")
            from gi.repository import Gdk, GdkX11
            Gdk.init([])
            _GI["Gdk"] = (Gdk, GdkX11)
        return _GI["Gdk"]


def _gio():
    with _GI_LOCK:
        if "Gio" not in _GI:
            from gi.repository import Gio
            _GI["Gio"] = Gio
        return _GI["Gio"]


def _x(*args: Any, check: bool = True, timeout: float = 5.0) -> str:
    """Run xdotool; its stdout, stripped."""
    try:
        done = subprocess.run(["xdotool", *map(str, args)], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ComputerUseError(f"xdotool failed: {exc}") from exc
    if check and done.returncode != 0:
        raise ComputerUseError(f"xdotool {args[0]} failed: {done.stderr.strip() or done.returncode}")
    return done.stdout.strip()


def _clients() -> list[int] | None:
    """The top-level windows the window manager manages (_NET_CLIENT_LIST); None without one."""
    if shutil.which("xprop") is None:
        return None
    try:
        done = subprocess.run(["xprop", "-root", "_NET_CLIENT_LIST"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0 or "#" not in done.stdout:
        return None
    return [int(raw.strip(), 16) for raw in done.stdout.split("#", 1)[1].split(",") if raw.strip()]


def _stacking() -> list[str]:
    """The managed windows bottom to top (_NET_CLIENT_LIST_STACKING), as decimal ids; empty without one."""
    if shutil.which("xprop") is None:
        return []
    try:
        done = subprocess.run(["xprop", "-root", "_NET_CLIENT_LIST_STACKING"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if done.returncode != 0 or "#" not in done.stdout:
        return []
    return [str(int(raw.strip(), 16)) for raw in done.stdout.split("#", 1)[1].split(",") if raw.strip()]


def _put_under(window_id: object, before: list[str]) -> None:
    """Raise again, lowest first, the windows that stood over ``window_id`` in ``before``."""
    window = str(window_id)
    if window not in before:
        return
    for other in before[before.index(window) + 1:][-24:]:
        _x("windowraise", other, check=False)


def _window_class(xid: str) -> str:
    """The instance part of a window's WM_CLASS ("display" of "display", "Display")."""
    if shutil.which("xprop") is not None:
        try:
            done = subprocess.run(["xprop", "-id", xid, "WM_CLASS"], capture_output=True, text=True, timeout=5)
            found = re.search(r'=\s*"([^"]*)"', done.stdout)
            if found:
                return found.group(1)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return _x("getwindowclassname", xid, check=False)


def _window_classes(xid: str) -> list[str]:
    """Both parts of a window's WM_CLASS, lower case; empty when it has none or xprop is missing."""
    if xid in _CLASSES:
        return _CLASSES[xid]
    if shutil.which("xprop") is None:
        return []
    try:
        done = subprocess.run(["xprop", "-id", xid, "WM_CLASS"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = [part.lower() for part in re.findall(r'"([^"]*)"', done.stdout)]
    if len(_CLASSES) > 512:
        _CLASSES.clear()
    _CLASSES[xid] = found  # a window keeps its class for life
    return found


_CLASSES: dict[str, list[str]] = {}
_XLIB: dict[str, Any] = {}
_XLIB_LOCK = threading.Lock()


def _x_children(xid: int) -> list[int]:
    """The direct children of an X window (XQueryTree); empty when libX11 cannot be used."""
    import ctypes
    import ctypes.util
    with _XLIB_LOCK:
        if "x" not in _XLIB:
            _XLIB["x"] = None
            name = ctypes.util.find_library("X11")
            if name and os.environ.get("DISPLAY"):
                x11 = ctypes.cdll.LoadLibrary(name)
                x11.XOpenDisplay.restype = ctypes.c_void_p
                x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
                x11.XQueryTree.argtypes = [
                    ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint)]
                x11.XFree.argtypes = [ctypes.c_void_p]
                display = x11.XOpenDisplay(None)
                if display:
                    _XLIB["x"], _XLIB["display"] = x11, display
        x11, display = _XLIB.get("x"), _XLIB.get("display")
        if x11 is None:
            return []
        root, parent, children, count = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p(), ctypes.c_uint()
        if not x11.XQueryTree(display, xid, ctypes.byref(root), ctypes.byref(parent), ctypes.byref(children),
                              ctypes.byref(count)):
            return []
        if not children.value:
            return []
        try:
            return list((ctypes.c_ulong * count.value).from_address(children.value))
        finally:
            x11.XFree(children)


def _embedded_windows(clients: list[int]) -> list[tuple[int, int]]:
    """Other applications' windows embedded in an OpenProgram App window, with their pids.

    An embedded window is reparented into the App's window, so the window
    manager's client list no longer has it. Found from the windows
    themselves, not the worker's records, so a backend in an agentic
    subprocess sees them too.
    """
    from ..embedding import embedded_children

    def pid_of(xid: int) -> int:
        raw = _x("getwindowpid", xid, check=False)
        return int(raw) if raw.isdigit() else 0

    def is_own(xid: int, _pid: int) -> bool:
        return any(part.startswith("openprogram") for part in _window_classes(str(xid)))

    return embedded_children(clients, is_own=is_own, pid_of=pid_of, children_of=_x_children)


def _pid_for_class(name: str) -> int:
    """The newest process whose name starts with a window class ("Display" -> display-im6.q16)."""
    wanted = name.strip().lower()
    if not wanted:
        return 0
    best = 0
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            for found in (_proc_name(int(entry)).lower(), _proc_exe(int(entry)).lower()):
                if found == wanted or found.startswith((wanted + "-", wanted + ".")):
                    best = max(best, int(entry))
    return best


def _application_directories() -> list[str]:
    """The folders desktop entries are read from (the XDG data folders), in the order they are searched."""
    home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    shared = (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    return [os.path.join(folder, "applications") for folder in (home, *shared) if folder]


def _proc_name(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _proc_exe(pid: int) -> str:
    try:
        return os.path.basename(os.readlink(f"/proc/{pid}/exe"))
    except OSError:
        return ""


def _descendants(pid: int) -> set[int]:
    """``pid`` and its child processes: browsers and Electron apps play sound from a child."""
    parents: dict[int, int] = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                stat = Path(f"/proc/{entry}/stat").read_text(encoding="utf-8")
                parents[int(entry)] = int(stat.rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                continue
    out, frontier = {pid}, [pid]
    while frontier:
        current = frontier.pop()
        for child, parent in parents.items():
            if parent == current and child not in out:
                out.add(child)
                frontier.append(child)
    return out


@dataclass
class App:
    """A running application: its process, its AT-SPI root (when it exposes one), its desktop entry."""
    pid: int
    name: str
    desktop_id: str = ""
    accessible: Any = None

    @property
    def ident(self) -> str:
        return self.desktop_id or _proc_name(self.pid) or self.name


@dataclass
class XWindow:
    xid: int
    title: str
    x: int
    y: int
    width: int
    height: int
    visible: bool


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
    scale: float = 1.0           # screenshot pixels per screen pixel
    has_screenshot: bool = False
    sparse: bool = False
    text_boxes: list[ocr.TextBox] = field(default_factory=list)
    shapes: list[ocr.Shape] = field(default_factory=list)
    # Roles whose subtrees were left out because another target operates them (see base.claimed).
    left_out: frozenset = frozenset()


@dataclass
class _Recording:
    app: str
    pid: int
    limit: float
    chunks: list[bytes] = field(default_factory=list)
    frames: int = 0
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None
    stopped: bool = False
    error: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)

    def take(self, data: bytes) -> None:
        with self.lock:
            room = int(self.limit * audio.SAMPLE_RATE) - self.frames
            if room <= 0:
                return
            data = data[: room * 4]
            self.chunks.append(data)
            self.frames += len(data) // 4

    @property
    def seconds(self) -> float:
        return self.frames / audio.SAMPLE_RATE

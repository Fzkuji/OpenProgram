"""Linux backend for the ``computer_use`` tool, on an X11 session.

It reads one application window as a screenshot plus an indexed AT-SPI
tree, and gives the model the same actions as the macOS backend.

* Element actions (press, toggle, focus, set value, insert and select
  text, scroll bars, menus) go through AT-SPI and run in the background:
  no window is raised and the pointer does not move.
* Pointer input at a coordinate and key presses go through XTEST
  (xdotool). GTK and Qt ignore synthetic events sent to a window that is
  not focused, so these steps bring the window forward for the moment
  of the input and then hand focus and the pointer back.
* Screenshots read the window's own pixels through Gdk; OCR uses
  tesseract when it is installed.
* Recording reads only the app's own audio streams from PulseAudio or
  PipeWire (``parec --monitor-stream``); the user keeps hearing them.

Coordinates the model gives are pixels of the last screenshot of that
window; they are converted to screen pixels here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import base64
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import threading
import time
from typing import Any

from . import audio
from . import keys as keymap
from . import ocr
from .base import (HOVER_SECONDS, INCLUDE, MAX_COPY_CHARS, MAX_TEXT_CHARS, OCR_SPARSE_NODES, AudioClip,
                   ComputerUseError, Observation, RefusedApps, Report, refuse_password_field, check_cancelled, click_args, click_words, find_text,
                   menu_key, menu_path, save_recording, scroll_args, seconds_arg, window_node_count)
from .tree import Node, Snapshot, diff, number, rebind, role_name, snapshot

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


class LinuxComputer:
    """One backend per worker; state is kept per (session, pid)."""

    STATELESS = frozenset({"menu", "show_window", "wait"})
    AT_FOCUS = frozenset({"copy", "press_key", "type_text", "paste"})

    def __init__(self) -> None:
        self._states: dict[tuple[str, int], WindowState] = {}
        self._lock = threading.RLock()
        self._recordings: dict[str, _Recording] = {}
        self._icons: dict[str, str] = {}

    # ------------------------------------------------------------------ AT-SPI helpers
    @staticmethod
    def _safe(call, default=None):
        try:
            return call()
        except Exception:  # noqa: BLE001 - a vanished element raises GLib.Error
            return default

    def _role(self, acc) -> str:
        return self._safe(lambda: acc.get_role_name(), "") or ""

    def _name(self, acc) -> str:
        return self._safe(lambda: acc.get_name(), "") or ""

    def _label(self, acc, role: str) -> str:
        name = self._name(acc)
        if name and _CLASS_NAME.fullmatch(name) and (role in _TRANSPARENT or name.startswith(("Gtk", "Adw", "Hdy"))):
            return ""
        return name or self._safe(lambda: acc.get_description(), "") or ""

    def _children(self, acc) -> list[Any]:
        count = self._safe(lambda: acc.get_child_count(), 0) or 0
        out = []
        for i in range(min(count, 2000)):
            child = self._safe(lambda i=i: acc.get_child_at_index(i))
            if child is not None:
                out.append(child)
        return out

    def _has(self, acc, state_name: str) -> bool:
        Atspi = _atspi()
        state = getattr(Atspi.StateType, state_name)
        return bool(self._safe(lambda: acc.get_state_set().contains(state), False))

    def _ifaces(self, acc) -> set[str]:
        return set(self._safe(lambda: acc.get_interfaces(), []) or [])

    def _actions(self, acc) -> list[str]:
        Atspi = _atspi()
        if "Action" not in self._ifaces(acc):
            return []
        count = self._safe(lambda: Atspi.Action.get_n_actions(acc), 0) or 0
        return [self._safe(lambda i=i: Atspi.Action.get_action_name(acc, i), "") or "" for i in range(count)]

    def _do_action(self, acc, name: str) -> bool:
        Atspi = _atspi()
        for i, action in enumerate(self._actions(acc)):
            if action.lower() == name.lower():
                return bool(self._safe(lambda i=i: Atspi.Action.do_action(acc, i), False))
        return False

    def _extents(self, acc) -> tuple[float, float, float, float] | None:
        Atspi = _atspi()
        if "Component" not in self._ifaces(acc):
            return None
        rect = self._safe(lambda: Atspi.Component.get_extents(acc, Atspi.CoordType.SCREEN))
        if rect is None or rect.width <= 0 or rect.height <= 0:
            return None
        return float(rect.x), float(rect.y), float(rect.width), float(rect.height)

    def _text(self, acc, limit: int = -1) -> str | None:
        Atspi = _atspi()
        if "Text" not in self._ifaces(acc):
            return None
        count = self._safe(lambda: Atspi.Text.get_character_count(acc), 0) or 0
        end = count if limit < 0 else min(count, limit)
        return self._safe(lambda: Atspi.Text.get_text(acc, 0, end), "") or ""

    def _value(self, acc) -> float | None:
        Atspi = _atspi()
        if "Value" not in self._ifaces(acc):
            return None
        return self._safe(lambda: float(Atspi.Value.get_current_value(acc)))

    # ------------------------------------------------------------------ apps
    def _desktop_apps(self) -> list[Any]:
        Atspi = _atspi()
        desktop = Atspi.get_desktop(0)
        return self._children(desktop)

    def _installed(self) -> list[dict[str, str]]:
        """Desktop entries a person would launch from the app grid."""
        try:
            Gio = _gio()
        except ImportError:
            return []
        rows = []
        for info in Gio.AppInfo.get_all():
            if not info.should_show():
                continue
            app_id = (info.get_id() or "").removesuffix(".desktop")
            executable = os.path.basename(info.get_executable() or "")
            rows.append({"name": info.get_display_name() or info.get_name() or app_id, "desktop_id": app_id,
                         "executable": executable, "icon": self._icon_name(info)})
        return sorted(rows, key=lambda row: row["name"].lower())

    @staticmethod
    def _icon_name(info) -> str:
        icon = info.get_icon()
        if icon is None:
            return ""
        names = getattr(icon, "get_names", None)
        if names:
            return (names() or [""])[0]
        file = getattr(icon, "get_file", None)
        return file().get_path() if file and file() else ""

    def _desktop_for(self, pid: int, name: str) -> dict[str, str] | None:
        exe, comm = _proc_exe(pid).lower(), _proc_name(pid).lower()
        lowered = name.lower()
        for row in self._installed():
            candidates = {row["executable"].lower(), row["desktop_id"].lower().rsplit(".", 1)[-1]}
            if exe in candidates or comm in candidates or row["name"].lower() == lowered:
                return row
        return None

    def _x_pids(self) -> dict[int, list[int]]:
        """Top-level X windows by the pid that owns them: _NET_WM_PID, or for the
        Xt and Motif apps that do not set it, the process named like the window's class."""
        out: dict[int, list[int]] = {}
        clients = _clients()
        listed = [str(c) for c in clients] if clients is not None else \
            _x("search", "--onlyvisible", "--name", "", check=False).split()
        for raw in listed:
            pid = _x("getwindowpid", raw, check=False)
            owner = int(pid) if pid.isdigit() else _pid_for_class(_window_class(raw))
            if owner:
                out.setdefault(owner, []).append(int(raw))
        return out

    def _running(self) -> list[App]:
        apps: dict[int, App] = {}
        for acc in self._desktop_apps():
            pid = self._safe(lambda acc=acc: int(acc.get_process_id()), 0)
            if not pid or pid == os.getpid():
                continue
            if not any(self._role(w) in _WINDOW_ROLES for w in self._children(acc)):
                continue
            name = self._name(acc) or _proc_name(pid)
            row = self._desktop_for(pid, name)
            apps[pid] = App(pid=pid, name=(row or {}).get("name") or name, desktop_id=(row or {}).get("desktop_id", ""),
                            accessible=acc)
        # Apps that do not talk to the accessibility bus still have X windows.
        for pid in self._x_pids():
            if pid in apps or pid == os.getpid():
                continue
            name = _proc_name(pid)
            row = self._desktop_for(pid, name)
            apps[pid] = App(pid=pid, name=(row or {}).get("name") or name, desktop_id=(row or {}).get("desktop_id", ""))
        return sorted(apps.values(), key=lambda a: a.name.lower())

    @staticmethod
    def _keys(app: App | dict) -> tuple[str, ...]:
        if isinstance(app, App):
            return (app.desktop_id, _proc_name(app.pid), app.name)
        return (str(app.get("desktop_id", "")), str(app.get("executable", "")))

    @classmethod
    def _refused(cls, app: App | dict) -> bool:
        return RefusedApps(OWN_APPS).hits(*cls._keys(app))

    def _running_match(self, query: str) -> App | None:
        wanted = str(query or "").strip().lower()
        if not wanted:
            return None
        running = self._running()
        for app in running:
            if wanted in (app.desktop_id.lower(), app.name.lower(), _proc_name(app.pid).lower(), str(app.pid),
                          app.desktop_id.lower().rsplit(".", 1)[-1]):
                return app
        for app in running:
            if wanted in app.name.lower():
                return app
        return None

    def resolve_app(self, query: str, *, launch: bool = True) -> App:
        app = self._running_match(query)
        from openprogram.host._platform import front

        if app is None and launch:
            # An application that starts may take the front; the guard is told which process is the new one.
            with front.starting():
                app = self._launch(query)
                if app is not None:
                    front.note(app.pid)
        if app is None:
            raise ComputerUseError(f"{query!r} is not running; list_apps shows the running and installed apps")
        RefusedApps(OWN_APPS).refuse(app.name, *self._keys(app))
        front.note(app.pid)
        return app

    def _installed_match(self, query: str) -> dict[str, str] | None:
        wanted = str(query or "").strip().lower()
        rows = self._installed()
        for row in rows:
            if wanted in (row["desktop_id"].lower(), row["name"].lower(), row["executable"].lower()):
                return row
        return next((row for row in rows if wanted and wanted in row["name"].lower()), None)

    def _launch(self, query: str) -> App | None:
        row = self._installed_match(query)
        if row is None:
            return None
        RefusedApps(OWN_APPS).refuse(row["name"], *self._keys(row))
        Gio = _gio()
        info = Gio.DesktopAppInfo.new(row["desktop_id"] + ".desktop")
        if info is None:
            return None
        before = {app.pid for app in self._running()}
        try:
            info.launch([], None)
        except Exception as exc:  # noqa: BLE001 - GLib.Error
            raise ComputerUseError(f"could not launch {row['name']}: {exc}") from exc
        deadline = time.monotonic() + LAUNCH_SECONDS
        while time.monotonic() < deadline:
            check_cancelled()
            time.sleep(0.3)
            for app in self._running():
                if app.pid not in before and (app.desktop_id == row["desktop_id"] or app.name == row["name"]
                                              or _proc_exe(app.pid) == row["executable"]):
                    return self._await_tree(app, deadline)
        raise ComputerUseError(f"{row['name']} was launched but no window of it appeared within "
                               f"{LAUNCH_SECONDS:g} seconds")

    def _await_tree(self, app: App, deadline: float) -> App:
        """A just-launched app joins the accessibility bus after its window maps; wait for it."""
        while app.accessible is None and time.monotonic() < deadline:
            check_cancelled()
            time.sleep(0.3)
            app = next((a for a in self._running() if a.pid == app.pid), app)
        return app

    def list_apps(self) -> str:
        running = self._running()
        lines = ["Running applications:"]
        for app in running:
            windows = self._x_windows(app.pid)
            state = [f"{len(windows)} window" + ("" if len(windows) == 1 else "s")]
            if app.accessible is None:
                state.append("no accessibility tree: screenshot and coordinates only")
            if self._refused(app):
                state.append("not controllable")
            lines.append(f"- {app.name} ({app.desktop_id or _proc_name(app.pid) or 'no desktop entry'}, "
                         f"pid {app.pid}; {', '.join(state)})")
        if len(lines) == 1:
            lines.append("- none with a window")
        ids = {app.desktop_id.lower() for app in running}
        installed = [row for row in self._installed() if row["desktop_id"].lower() not in ids and not self._refused(row)]
        if installed:
            lines += ["", "Installed, not running (get_app_state launches them):"]
            lines += [f"- {row['name']} ({row['desktop_id']})" for row in installed]
        return "\n".join(lines)

    def app_info(self, query: str) -> dict[str, Any]:
        """Display name, desktop id and icon of an app a rule or approval names. Launches nothing."""
        info: dict[str, Any] = {"query": query, "name": query, "bundle_id": "", "icon": "",
                                "installed": False, "running": False, "refused": False}
        if not isinstance(query, str) or not query.strip():
            return info
        app = self._running_match(query)
        row = self._installed_match(app.desktop_id if app and app.desktop_id else query)
        if app is None and row is None:
            return info
        info.update(installed=row is not None, running=app is not None,
                    name=(row or {}).get("name") or (app.name if app else query),
                    bundle_id=(row or {}).get("desktop_id") or (app.ident if app else ""),
                    icon=self._icon((row or {}).get("icon", "")))
        info["refused"] = self._refused(app) if app else self._refused(row)
        return info

    def running_apps(self) -> list[dict[str, Any]]:
        rows = []
        for app in self._running():
            if self._refused(app):
                continue
            row = self._installed_match(app.desktop_id) if app.desktop_id else None
            rows.append({"query": app.ident, "name": app.name, "bundle_id": app.ident,
                         "icon": self._icon((row or {}).get("icon", "")), "installed": row is not None,
                         "running": True, "refused": False})
        return rows

    def _icon(self, name: str) -> str:
        """A data URL for an icon theme name or file, or "" when none is found."""
        if not name:
            return ""
        if name in self._icons:
            return self._icons[name]
        path = Path(name) if name.startswith("/") else None
        if path is None:
            roots = [Path.home() / ".local/share/icons", Path("/usr/share/icons"), Path("/usr/share/pixmaps")]
            for root in roots:
                for size in ("128x128", "96x96", "64x64", "48x48", "scalable", "256x256"):
                    for ext in ("png", "svg"):
                        candidate = root / "hicolor" / size / "apps" / f"{name}.{ext}"
                        if candidate.is_file():
                            path = candidate
                            break
                    if path:
                        break
                if path is None and (root / f"{name}.png").is_file():
                    path = root / f"{name}.png"
                if path:
                    break
        url = ""
        if path is not None and path.is_file() and path.stat().st_size < 512_000:
            mime = "image/svg+xml" if path.suffix == ".svg" else "image/png"
            url = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()
        self._icons[name] = url
        return url

    # ------------------------------------------------------------------ windows
    def _x_windows(self, pid: int) -> list[XWindow]:
        """Top-level X windows of ``pid`` with a title, mapped ones first."""
        clients = _clients()
        visible = set(_x("search", "--onlyvisible", "--name", "", check=False).split())
        everything = [str(xid) for xid in self._x_pids().get(pid, [])]
        if clients is None:
            # Without a client list the visible-window walk misses minimized windows.
            everything += [raw for raw in _x("search", "--pid", pid, check=False).split() if raw not in everything]
        out = []
        for raw in everything:
            title = _x("getwindowname", raw, check=False)
            if not title:
                continue
            geometry = _x("getwindowgeometry", "--shell", raw, check=False)
            values = dict(line.split("=", 1) for line in geometry.splitlines() if "=" in line)
            try:
                x, y, w, h = (int(values[k]) for k in ("X", "Y", "WIDTH", "HEIGHT"))
            except (KeyError, ValueError):
                continue
            # Without a client list, skip GTK's and Qt's tiny client-leader windows.
            if w < 2 or h < 2 or (clients is None and w <= 10 and h <= 10):
                continue
            out.append(XWindow(int(raw), title, x, y, w, h, raw in visible))
        return sorted(out, key=lambda w: not w.visible)

    def _accessible_windows(self, app: App) -> list[Any]:
        if app.accessible is None:
            return []
        return [w for w in self._children(app.accessible) if self._role(w) in _WINDOW_ROLES
                and (self._has(w, "SHOWING") or self._has(w, "ICONIFIED") or self._name(w))]

    def _pair(self, app: App, which=None, window_id=None, prefer_id: int = 0) -> tuple[Any, XWindow | None]:
        """The (AT-SPI window, X window) pair a step names, or the one read last, or the active one."""
        xwins = self._x_windows(app.pid)
        accs = self._accessible_windows(app)

        def acc_for(xw: XWindow | None):
            if xw is None:
                return None
            titled = [a for a in accs if self._name(a) == xw.title]
            if len(titled) == 1:
                return titled[0]
            for acc in titled or accs:
                frame = self._extents(acc)
                if frame and abs(frame[2] - xw.width) < 4 and abs(frame[3] - xw.height) < 4:
                    return acc
            return titled[0] if titled else (accs[0] if len(accs) == 1 and len(xwins) == 1 else None)

        if window_id not in (None, "", 0):
            try:
                wanted = int(window_id)
            except (TypeError, ValueError) as exc:
                raise ComputerUseError("window_id must be an integer from list_windows") from exc
            for xw in xwins:
                if xw.xid == wanted:
                    return acc_for(xw), xw
            raise ComputerUseError(f"window_id {wanted} is not an open window of this app; call list_windows")
        if which not in (None, ""):
            if isinstance(which, int) or str(which).strip().isdigit():
                index = int(which)
                if 0 <= index < len(xwins):
                    return acc_for(xwins[index]), xwins[index]
                raise ComputerUseError(f"window {index} does not exist; the app has {len(xwins)} window(s)")
            needle = str(which).strip().lower()
            for xw in xwins:
                if needle in xw.title.lower():
                    return acc_for(xw), xw
            raise ComputerUseError(f"no window title contains {which!r}")
        if prefer_id:
            for xw in xwins:
                if xw.xid == prefer_id:
                    return acc_for(xw), xw
        active = next((a for a in accs if self._has(a, "ACTIVE")), None)
        if active is not None:
            title = self._name(active)
            for xw in xwins:
                if xw.title == title:
                    return active, xw
        if xwins:
            return acc_for(xwins[0]), xwins[0]
        return (accs[0] if accs else None), None

    def list_windows(self, query: str) -> str:
        app = self.resolve_app(query, launch=False)
        active = _x("getactivewindow", check=False)
        lines = [f"Windows of {app.name} (pid {app.pid}):"]
        xwins = self._x_windows(app.pid)
        for position, xw in enumerate(xwins):
            notes = []
            if str(xw.xid) == active:
                notes.append("focused")
            if not xw.visible:
                notes.append("minimized")
            lines.append(f'- {position}: "{xw.title}" window_id {xw.xid}, {xw.width}x{xw.height} at ({xw.x}, {xw.y})'
                         + (f" [{', '.join(notes)}]" if notes else ""))
        if not xwins:
            lines.append("- none open")
        return "\n".join(lines)

    # ------------------------------------------------------------------ screenshot
    def _capture(self, xw: XWindow, region=None) -> tuple[bytes | None, float]:
        """PNG of the window (or of ``region`` in window pixels) and its pixels per screen pixel."""
        if not xw.visible:
            return None, 1.0
        try:
            Gdk, GdkX11 = _gdk()
            display = Gdk.Display.get_default()
            window = GdkX11.X11Window.foreign_new_for_display(display, xw.xid)
            x, y, w, h = region or (0, 0, xw.width, xw.height)
            pixbuf = Gdk.pixbuf_get_from_window(window, int(x), int(y), int(w), int(h))
        except Exception:  # noqa: BLE001 - no picture is not an error; the tree still reads
            return None, 1.0
        if pixbuf is None:
            return None, 1.0
        scale = 1.0
        longest = max(pixbuf.get_width(), pixbuf.get_height())
        if region is None and longest > MAX_IMAGE_SIDE:
            from gi.repository import GdkPixbuf
            scale = MAX_IMAGE_SIDE / longest
            pixbuf = pixbuf.scale_simple(max(1, int(pixbuf.get_width() * scale)),
                                         max(1, int(pixbuf.get_height() * scale)), GdkPixbuf.InterpType.BILINEAR)
        ok, data = pixbuf.save_to_bufferv("png", [], [])
        return (bytes(data) if ok else None), scale

    # ------------------------------------------------------------------ tree
    def _node(self, acc, depth: int) -> Node:
        role = self._role(acc) or "unknown"
        label = self._label(acc, role)
        flags: list[str] = []
        value = ""
        secure = role == _SECURE
        if secure:
            flags.append("secure")
        elif role in _TOGGLE_ROLES:
            flags.append("checked" if self._has(acc, "CHECKED") or self._has(acc, "PRESSED") else "unchecked")
        else:
            number_value = self._value(acc)
            if number_value is not None:
                value = f"{number_value:g}"
            elif role not in ("label", "push button", "menu item", "page tab", "link") or not label:
                text = self._text(acc, 400)
                if text and text != label:
                    value = text
        if not self._has(acc, "ENABLED") and not self._has(acc, "SENSITIVE"):
            flags.append("disabled")
        if self._has(acc, "FOCUSED"):
            flags.append("focused")
        if self._has(acc, "SELECTED"):
            flags.append("selected")
        if self._has(acc, "EXPANDED"):
            flags.append("expanded")
        if not secure and ("EditableText" in self._ifaces(acc) and self._has(acc, "EDITABLE")
                           or "Value" in self._ifaces(acc) and role not in ("progress bar", "level bar")):
            flags.append("settable")
        placeholder = ""
        attrs = self._safe(lambda: acc.get_attributes(), {}) or {}
        if isinstance(attrs, dict):
            placeholder = str(attrs.get("placeholder-text", ""))
        actions = [a for a in self._actions(acc) if a and a.lower() not in _PRIMARY]
        return Node(role=role, depth=depth, label=label, value=value, placeholder=placeholder,
                    flags=flags, actions=actions)

    def _read_tree(self, window) -> tuple[list[Node], list[Any], bool]:
        nodes: list[Node] = []
        elements: list[Any] = []
        if window is None:
            return nodes, elements, True
        stack = [(window, 0)]
        seen: set = set()
        # GTK 3 marks what is on screen SHOWING; GTK 4 sets only VISIBLE below the window.
        shown = "SHOWING" if any(self._has(c, "SHOWING") for c in self._children(window)) else "VISIBLE"
        while stack:
            check_cancelled()
            if len(nodes) >= MAX_NODES:
                return nodes, elements, False
            acc, depth = stack.pop()
            key = self._safe(lambda acc=acc: hash(acc), id(acc))
            if key in seen:
                continue
            seen.add(key)
            if depth and (not self._has(acc, shown) or self._name(acc).endswith("TooltipWindow")):
                continue
            node = self._node(acc, depth)
            transparent = (node.role in _TRANSPARENT and not node.label and not node.value and not node.actions)
            child_depth = depth
            if not transparent or not depth:
                node.index = len(nodes)
                nodes.append(node)
                elements.append(acc)
                child_depth = depth + 1
            if child_depth > MAX_DEPTH:
                continue
            # A combo box lists its popup items while closed; they are read when it opens.
            if node.role == "combo box" and not self._has(acc, "EXPANDED"):
                children = [c for c in self._children(acc) if self._role(c) != "menu"]
            else:
                children = self._children(acc)
            stack.extend((child, child_depth) for child in reversed(children))
        return nodes, elements, True

    # ------------------------------------------------------------------ state
    def get_state(self, session: str, query: str, *, window=None, window_id=None, full=False,
                  as_diff=True, include: str = "all") -> Observation:
        include = str(include or "all").lower()
        if include not in INCLUDE:
            raise ComputerUseError("include must be all, tree or screenshot")
        with self._lock:
            app = self.resolve_app(query)
            key = (session, app.pid)
            previous = self._states.get(key)
            acc, xw = self._pair(app, window, window_id, previous.window_id if previous else 0)
            title = xw.title if xw else self._name(acc)
            wid = xw.xid if xw else 0
            same_window = bool(previous) and (previous.window_id == wid if wid else previous.window_title == title)
            if include == "screenshot" and not same_window:
                raise ComputerUseError("Read this window with include=all first; a screenshot-only read keeps the "
                                       "tree of the window the model already has.")
            image, scale = (None, 1.0)
            if include != "tree" and xw is not None:
                image, scale = self._capture(xw)
            lines = [f"App: {app.name} ({app.ident}, pid {app.pid})"]
            if xw is None and acc is None:
                lines.append("Window: none open.")
            else:
                note = " (minimized)" if xw is not None and not xw.visible else ""
                size = f", {xw.width}x{xw.height} pixels" if xw else ""
                lines.append(f'Window: "{title}"{note}' + (f", window_id {wid}" if wid else "") + size)
                if image is not None:
                    w, h = int(round(xw.width * scale)), int(round(xw.height * scale))
                    lines.append(f"Screenshot: {w}x{h} pixels; x,y coordinates in actions are pixels of this screenshot.")
                elif include != "tree":
                    lines.append("Screenshot: unavailable (the window is minimized; show_window brings it back). "
                                 "Use element indices.")
            if app.accessible is None:
                lines.append("This app does not expose an accessibility tree (for GTK and Qt apps, turn on "
                             "accessibility and restart the app); act with x,y on the screenshot.")
            others = [w for w in self._x_windows(app.pid) if w.xid != wid]
            if others:
                lines.append("Other windows: " + "; ".join(f'"{w.title}"' for w in others[:8])
                             + ". list_windows shows their ids; pass window=<title> or window_id to read one.")
            frame = (xw.x, xw.y, xw.width, xw.height) if xw else None
            if include == "screenshot":
                previous.origin = (frame[0], frame[1]) if frame else previous.origin
                previous.size = (frame[2], frame[3]) if frame else previous.size
                previous.scale, previous.has_screenshot = scale, image is not None
                previous.text_boxes = self._read_text(image) if previous.sparse else []
                lines += ["", "Tree not read (include=screenshot); element indices are those of the last tree."]
                lines.extend(_text_section(previous.text_boxes))
                return Observation(text="\n".join(lines), image=image, app=app.name)
            nodes, elements, complete = self._read_tree(acc)
            prior = previous.snapshot if same_window else None
            fresh = number(nodes, prior)
            body, is_diff = diff(prior if (as_diff and not full) else None, nodes)
            state = WindowState(
                pid=app.pid, window_title=title, window_id=wid,
                elements={node.index: element for node, element in zip(nodes, elements)},
                nodes=nodes, by_index={node.index: node for node in nodes}, snapshot=snapshot(nodes, fresh),
                origin=(frame[0], frame[1]) if frame else (0.0, 0.0),
                size=(frame[2], frame[3]) if frame else (0.0, 0.0), scale=scale, has_screenshot=image is not None)
            if include == "tree" and same_window and previous.has_screenshot:
                state.scale, state.has_screenshot = previous.scale, True
            state.sparse = window_node_count(nodes) < OCR_SPARSE_NODES
            if state.sparse:
                state.text_boxes = self._read_text(image)
            self._states[key] = state
            if not complete:
                lines.append(f"The tree was cut at {MAX_NODES} elements.")
            lines.append("")
            lines.append("Accessibility tree changes since the last state:" if is_diff else "Accessibility tree:")
            lines.append(body or "(empty)")
            lines.extend(_text_section(state.text_boxes))
            return Observation(text="\n".join(lines), image=image, app=app.name)

    @staticmethod
    def _read_text(image: bytes | None) -> list[ocr.TextBox]:
        if image is None:
            return []
        try:
            return ocr.recognize_tesseract(image)
        except Exception:  # noqa: BLE001 - OCR only adds to a read
            return []

    def _state_for(self, session: str, query: str) -> tuple[App, WindowState]:
        app = self.resolve_app(query, launch=False)
        state = self._states.get((session, app.pid))
        if state is None:
            raise ComputerUseError(f"call get_app_state for {app.name} first")
        return app, state

    def _element(self, state: WindowState, index):
        try:
            index = int(index)
        except (TypeError, ValueError) as exc:
            raise ComputerUseError("element_index must be an integer from the last state") from exc
        acc = state.elements.get(index)
        if acc is None:
            raise ComputerUseError(f"element {index} is not in the last state; call get_app_state")
        if not self._role(acc):
            acc = self._rebind(state, index)
            if acc is None:
                raise ComputerUseError(f"element {index} no longer exists; call get_app_state for a fresh tree")
        return acc

    def _rebind(self, state: WindowState, index: int):
        """The element now behind ``index`` when the app replaced the one read earlier (see :func:`tree.rebind`)."""
        try:
            app = next((app for app in self._running() if app.pid == state.pid), None)
            if app is None:
                return None
            if state.window_id:
                window, _ = self._pair(app, None, state.window_id)
            else:
                named = [w for w in self._accessible_windows(app) if self._name(w) == state.window_title]
                window = named[0] if len(named) == 1 else None
            if window is None:
                return None
            nodes, elements, _ = self._read_tree(window)
            return rebind(state, index, nodes, elements)
        except Exception:
            # The step then fails as it did before: the model reads the window again.
            return None

    def _node_at(self, state: WindowState, index) -> Node:
        return state.by_index[int(index)]

    def _center(self, acc) -> tuple[float, float]:
        frame = self._extents(acc)
        if frame is None:
            raise ComputerUseError("this element has no position on screen; act on it by its actions instead")
        return frame[0] + frame[2] / 2, frame[1] + frame[3] / 2

    def _point(self, state: WindowState, x, y) -> tuple[float, float]:
        if x is None or y is None:
            raise ComputerUseError("give element_index, or x and y in pixels of the last screenshot")
        if not state.has_screenshot:
            raise ComputerUseError("this window has no screenshot to take coordinates from; call get_app_state")
        try:
            px, py = float(x), float(y)
        except (TypeError, ValueError) as exc:
            raise ComputerUseError("x and y must be numbers") from exc
        w, h = state.size[0] * state.scale, state.size[1] * state.scale
        if not (0 <= px < w and 0 <= py < h):
            raise ComputerUseError(f"({px:g}, {py:g}) is outside the {int(w)}x{int(h)} screenshot")
        return state.origin[0] + px / state.scale, state.origin[1] + py / state.scale

    def _focused(self, state: WindowState | None):
        for acc in (state.elements.values() if state else ()):
            if self._has(acc, "FOCUSED"):
                return acc
        return None

    def _live_focused(self, app: App, state: WindowState | None):
        """The element holding keyboard focus in the app's window, read now."""
        acc, _ = self._pair(app, prefer_id=state.window_id if state else 0)
        if acc is None:
            return None
        stack = [acc]
        seen = 0
        while stack and seen < MAX_NODES:
            current = stack.pop()
            seen += 1
            if self._has(current, "FOCUSED"):
                return current
            if self._has(current, "SHOWING") or self._has(current, "VISIBLE") or current is acc:
                stack.extend(self._children(current))
        return None

    def _refuse_secure(self, acc) -> None:
        if acc is not None and self._role(acc) == _SECURE:
            refuse_password_field("typing into this password field")

    # ------------------------------------------------------------------ foreground input
    @contextmanager
    def _foreground(self, state: WindowState, *, keep_pointer: bool = False):
        """Bring the window forward for XTEST input, then give focus and the pointer back."""
        if not state.window_id:
            raise ComputerUseError("this window has no X window id; act by element_index")
        from openprogram.host._platform import front

        # The front guard would give the focus back in the middle of the input.
        with front.forward():
            previous = _x("getactivewindow", check=False)
            before = _stacking()
            pointer = dict(line.split("=", 1) for line in _x("getmouselocation", "--shell", check=False).splitlines()
                           if "=" in line)
            _x("windowactivate", "--sync", state.window_id, check=False, timeout=3)
            if _x("getactivewindow", check=False) != str(state.window_id):
                # Without an EWMH window manager, activation does nothing; focus the window directly.
                _x("windowraise", state.window_id, check=False)
                _x("windowfocus", "--sync", state.window_id, check=False, timeout=3)
            try:
                yield
            finally:
                if not keep_pointer and "X" in pointer and "Y" in pointer:
                    _x("mousemove", pointer["X"], pointer["Y"], check=False)
                if previous and previous != str(state.window_id):
                    # The windows that stood over it go back over it, then the user's window takes the focus.
                    _put_under(state.window_id, before)
                    _x("windowactivate", previous, check=False, timeout=3)

    @staticmethod
    def _chord(key: str) -> list[str]:
        """An xdotool chord for a key name, with macOS modifier names mapped (cmd -> super)."""
        try:
            keymap.parse_chord(key)
        except keymap.KeyNameError as exc:
            if "modifier" in str(exc) or "malformed" in str(exc) or "non-empty" in str(exc):
                raise ComputerUseError(str(exc)) from exc
        parts = key.strip().split("+")
        if key.strip().endswith("++") or key.strip() == "+":
            parts = key.strip()[:-1].split("+")[:-1] + ["plus"]
        mods = []
        for name in parts[:-1]:
            mapped = _MODIFIERS.get(name.strip().lower())
            if mapped is None:
                raise ComputerUseError(f"unknown modifier {name!r} in {key!r}")
            mods.append(mapped)
        last = parts[-1].strip()
        last = {"enter": "Return", "esc": "Escape", "backspace": "BackSpace", "delete": "Delete",
                "pageup": "Prior", "pagedown": "Next", "page_up": "Prior", "page_down": "Next", "space": "space",
                " ": "space"}.get(last.lower(), _MODIFIERS.get(last.lower(), last))
        return ["+".join([*mods, last])]

    # ------------------------------------------------------------------ actions
    def act(self, session: str, action: str, args: dict[str, Any]) -> str:
        with self._lock:
            handler = getattr(self, f"_do_{action}", None)
            if handler is None:
                raise ComputerUseError(f"unknown action {action!r}")
            if action in self.STATELESS or (action in self.AT_FOCUS and args.get("element_index") is None):
                app = self.resolve_app(args.get("app") or "", launch=False)
                return handler(app, self._states.get((session, app.pid)), args)
            app, state = self._state_for(session, args.get("app") or "")
            return handler(app, state, args)

    def _do_click(self, app, state, args) -> str:
        button, count = click_args(args)
        try:
            mods = [_MODIFIERS[m.strip().lower()] for m in str(args.get("key") or "").replace(",", "+").split("+")
                    if m.strip()]
        except KeyError as exc:
            raise ComputerUseError(f"unknown modifier {exc.args[0]!r}") from exc
        hold = seconds_arg(args.get("duration"), "duration")
        index = args.get("element_index")
        if index is not None:
            acc = self._element(state, index)
            node = self._node_at(state, index)
            if count == 1 and not mods and not hold and button == "left":
                for name in _PRIMARY:
                    if self._do_action(acc, name):
                        return f"pressed {node.body()}"
                if node.role in ("text", "entry", "terminal", "document text") and self._grab_focus(acc):
                    return f"focused {node.body()}"
            if count == 1 and not mods and not hold and button == "right":
                for name in ("menu", "popup", "showmenu", "show menu", "context menu"):
                    if self._do_action(acc, name):
                        return f"opened the context menu of {node.body()}"
            point, what = self._center(acc), node.body()
        else:
            point, what = self._point(state, args.get("x"), args.get("y")), f"at ({args.get('x')}, {args.get('y')})"
        self._xtest_click(state, point, button, count, mods, hold)
        return f"{click_words(button, count)} {what}" + (f" (held {hold:g}s)" if hold else "") + \
            " (the window came forward for the click, then focus went back)"

    def _xtest_click(self, state, point, button: str, count: int, mods: list[str], hold: float) -> None:
        with self._foreground(state):
            _x("mousemove", "--sync", int(round(point[0])), int(round(point[1])))
            for mod in mods:
                _x("keydown", mod)
            try:
                if hold:
                    _x("mousedown", _BUTTONS[button])
                    time.sleep(hold)
                    _x("mouseup", _BUTTONS[button])
                else:
                    _x("click", "--repeat", count, "--delay", 60, _BUTTONS[button])
            finally:
                for mod in reversed(mods):
                    _x("keyup", mod, check=False)
            time.sleep(0.05)

    def _grab_focus(self, acc) -> bool:
        """Move keyboard focus inside the app. GTK also presents the window when it does
        this, so the window the user had active gets focus back."""
        Atspi = _atspi()
        previous = _x("getactivewindow", check=False)
        done = bool(self._safe(lambda: Atspi.Component.grab_focus(acc), False))
        if previous:
            time.sleep(0.05)
            if _x("getactivewindow", check=False) != previous:
                _x("windowactivate", previous, check=False, timeout=3)
        return done

    def _do_hover(self, app, state, args) -> str:
        index = args.get("element_index")
        if index is not None:
            point, what = self._center(self._element(state, index)), self._node_at(state, index).body()
        else:
            point, what = self._point(state, args.get("x"), args.get("y")), f"({args.get('x')}, {args.get('y')})"
        rest = seconds_arg(args.get("duration"), "duration") or HOVER_SECONDS
        with self._foreground(state, keep_pointer=True):
            _x("mousemove", "--sync", int(round(point[0])), int(round(point[1])))
            time.sleep(rest)
        return f"rested the pointer over {what} for {rest:g}s (on Linux the real pointer moves there)"

    def _do_wait(self, app, state, args) -> str:
        rest = seconds_arg(args.get("duration"), "duration") or 1.0
        deadline = time.monotonic() + rest
        while time.monotonic() < deadline:
            check_cancelled()
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return f"waited {rest:g}s"

    def _do_drag(self, app, state, args) -> str:
        start = self._point(state, args.get("x"), args.get("y"))
        end = self._point(state, args.get("to_x"), args.get("to_y"))
        with self._foreground(state):
            _x("mousemove", "--sync", int(start[0]), int(start[1]))
            _x("mousedown", 1)
            try:
                for step in range(1, 13):
                    t = step / 12
                    _x("mousemove", int(start[0] + (end[0] - start[0]) * t), int(start[1] + (end[1] - start[1]) * t))
                    time.sleep(0.015)
            finally:
                _x("mouseup", 1)
        return f"dragged from ({args.get('x')}, {args.get('y')}) to ({args.get('to_x')}, {args.get('to_y')})"

    def _scroll_bar(self, acc, direction: str):
        """The scroll bar that moves ``acc``'s content: its own, or one of an enclosing scroll pane."""
        vertical = direction in ("up", "down")
        current = acc
        for _ in range(12):
            if current is None:
                return None
            for child in self._children(current):
                if self._role(child) == "scroll bar" and "Value" in self._ifaces(child):
                    horizontal = self._has(child, "HORIZONTAL")
                    if vertical != horizontal:
                        return child
            current = self._safe(lambda current=current: current.get_parent())
        return None

    def _do_scroll(self, app, state, args) -> str:
        Atspi = _atspi()
        direction, pages, pixels = scroll_args(args)
        amount = f"{pixels:g} pixels" if pixels else f"{pages:g} page(s)"
        index = args.get("element_index")
        if index is not None:
            acc = self._element(state, index)
            node = self._node_at(state, index)
            bar = self._scroll_bar(acc, direction)
            if bar is not None:
                low = self._safe(lambda: Atspi.Value.get_minimum_value(bar), 0.0)
                high = self._safe(lambda: Atspi.Value.get_maximum_value(bar), 0.0)
                current = self._value(bar) or 0.0
                frame = self._extents(bar)
                page = (frame[3] if direction in ("up", "down") else frame[2]) * 0.9 if frame else (high - low) / 4
                step = pixels if pixels else page * pages
                sign = -1 if direction in ("up", "left") else 1
                target = max(low, min(high, current + sign * step))
                if self._safe(lambda: Atspi.Value.set_current_value(bar, target), False):
                    return f"scrolled {direction} {amount} in {node.body()}"
            point, what = self._center(acc), f"over {node.body()}"
        else:
            point, what = self._point(state, args.get("x"), args.get("y")), f"at ({args.get('x')}, {args.get('y')})"
        notches = max(1, int(round(pixels / PIXELS_PER_NOTCH))) if pixels else max(1, int(round(pages * NOTCHES_PER_PAGE)))
        wheel = {"up": 4, "down": 5, "left": 6, "right": 7}[direction]
        with self._foreground(state):
            _x("mousemove", "--sync", int(point[0]), int(point[1]))
            _x("click", "--repeat", notches, "--delay", 20, wheel)
        return f"scrolled {direction} {amount} {what}"

    def _do_press_key(self, app, state, args) -> str:
        key = str(args.get("key") or "").strip()
        if not key:
            raise ComputerUseError("key is required")
        chord = self._chord(key)
        hold = seconds_arg(args.get("duration"), "duration")
        target = None
        if args.get("element_index") is not None:
            if state is None:
                raise ComputerUseError(f"call get_app_state for {app.name} first")
            target = self._element(state, args["element_index"])
        target = target or self._live_focused(app, state)
        self._refuse_secure(target)
        # Return on a focused field fires its default action without leaving the background.
        if chord == ["Return"] and not hold and target is not None and self._do_action(target, "activate"):
            return f"pressed {key}"
        state = state or self._ensure_window(app)
        if args.get("element_index") is not None:
            self._grab_focus(target)
        with self._foreground(state):
            if hold:
                _x("keydown", *chord)
                time.sleep(hold)
                _x("keyup", *chord)
            else:
                _x("key", "--clearmodifiers", *chord)
        return f"pressed {key} (the window came forward for the key, then focus went back)"

    def _ensure_window(self, app: App) -> WindowState:
        _, xw = self._pair(app)
        if xw is None:
            raise ComputerUseError(f"{app.name} has no open window")
        return WindowState(pid=app.pid, window_title=xw.title, window_id=xw.xid, origin=(xw.x, xw.y),
                           size=(xw.width, xw.height))

    def _insert(self, acc, text: str) -> bool:
        """Insert ``text`` at the caret of an editable element, in the background."""
        Atspi = _atspi()
        if "EditableText" not in self._ifaces(acc) or not self._has(acc, "EDITABLE"):
            return False
        caret = self._safe(lambda: Atspi.Text.get_caret_offset(acc), -1)
        if caret is None or caret < 0:
            caret = self._safe(lambda: Atspi.Text.get_character_count(acc), 0) or 0
        if self._safe(lambda: Atspi.Text.get_n_selections(acc), 0):
            selected = self._safe(lambda: Atspi.Text.get_selection(acc, 0))
            if selected is not None and selected.end_offset > selected.start_offset:
                self._safe(lambda: Atspi.EditableText.delete_text(acc, selected.start_offset, selected.end_offset))
                caret = selected.start_offset
        if not self._safe(lambda: Atspi.EditableText.insert_text(acc, caret, text, len(text.encode())), False):
            return False
        self._safe(lambda: Atspi.Text.set_caret_offset(acc, caret + len(text)))
        return True

    def _do_type_text(self, app, state, args) -> str:
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise ComputerUseError("text is required")
        if len(text) > MAX_TEXT_CHARS:
            raise ComputerUseError(f"text is longer than {MAX_TEXT_CHARS} characters; use paste")
        target = None
        if args.get("element_index") is not None:
            target = self._element(state, args["element_index"])
            node = self._node_at(state, args["element_index"])
            if node.role in _NOT_TEXT_ROLES:
                raise ComputerUseError(
                    f"element {args['element_index']} is a {role_name(node.role)}, not a text field: typed text "
                    "would land in whatever field has focus. Click it, use set_value, or pick the text field.")
        target = target or self._live_focused(app, state)
        self._refuse_secure(target)
        lines = text.split("\n")
        if target is not None and "\n" not in text and self._insert(target, text):
            return f"typed {len(text)} character(s)"
        if target is not None and "EditableText" in self._ifaces(target) and all(
                self._insert(target, line) and (i == len(lines) - 1 or self._do_action(target, "activate"))
                for i, line in enumerate(lines)):
            return f"typed {len(text)} character(s); each newline pressed Return"
        state = state or self._ensure_window(app)
        if args.get("element_index") is not None:
            self._grab_focus(target)
        with self._foreground(state):
            for i, line in enumerate(lines):
                if line:
                    _x("type", "--clearmodifiers", "--delay", 8, "--", line, timeout=60)
                if i < len(lines) - 1:
                    _x("key", "Return")
        return f"typed {len(text)} character(s) (the window came forward for the keys, then focus went back)"

    def _do_set_value(self, app, state, args) -> str:
        Atspi = _atspi()
        index = args.get("element_index")
        acc = self._element(state, index)
        node = self._node_at(state, index)
        self._refuse_secure(acc)
        value = args.get("value")
        if value is None:
            raise ComputerUseError("value is required")
        if node.role in _TOGGLE_ROLES:
            wanted = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "on", "yes", "checked")
            on = self._has(acc, "CHECKED") or self._has(acc, "PRESSED")
            if on != wanted and not any(self._do_action(acc, name) for name in ("toggle", "click", "press")):
                raise ComputerUseError(f"element {index} cannot be toggled")
            return f"set {node.body()} to {wanted}"
        if "Value" in self._ifaces(acc) and not isinstance(value, bool):
            try:
                number_value = float(value)
            except (TypeError, ValueError) as exc:
                raise ComputerUseError("this element takes a number") from exc
            if not self._safe(lambda: Atspi.Value.set_current_value(acc, number_value), False):
                raise ComputerUseError("the app refused the value")
            return f"set {node.body()} to {number_value:g}"
        if "EditableText" in self._ifaces(acc) and self._has(acc, "EDITABLE"):
            if not self._safe(lambda: Atspi.EditableText.set_text_contents(acc, str(value)), False):
                raise ComputerUseError("the app refused the text")
            return f"set {node.body()} to {str(value)!r}"
        raise ComputerUseError(f"element {index} has no settable value; use click or type_text")

    def _do_perform_secondary_action(self, app, state, args) -> str:
        index = args.get("element_index")
        acc = self._element(state, index)
        node = self._node_at(state, index)
        wanted = str(args.get("action_name") or "").strip()
        if not wanted:
            raise ComputerUseError("action_name is required")
        actions = self._actions(acc)
        if not self._do_action(acc, wanted):
            raise ComputerUseError(f"element {index} does not expose {wanted!r}; its actions are: "
                                   + (", ".join(actions) or "none"))
        return f"performed {wanted} on {node.body()}"

    def _do_select_text(self, app, state, args) -> str:
        Atspi = _atspi()
        index = args.get("element_index")
        acc = self._element(state, index)
        node = self._node_at(state, index)
        value = self._text(acc)
        if value is None:
            raise ComputerUseError(f"element {index} has no text to select in")
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise ComputerUseError("text is required")
        start = find_text(value, text, args.get("prefix") or "", args.get("suffix") or "")
        if start is None:
            raise ComputerUseError(f"{text!r} was not found in element {index} with that prefix and suffix")
        end = start + len(text)
        kind = str(args.get("selection_type") or "text")
        if kind not in ("text", "cursor_before", "cursor_after"):
            raise ComputerUseError("selection_type must be text, cursor_before or cursor_after")
        if kind == "text":
            done = (self._safe(lambda: Atspi.Text.set_selection(acc, 0, start, end), False)
                    or self._safe(lambda: Atspi.Text.add_selection(acc, start, end), False))
        else:
            done = self._safe(lambda: Atspi.Text.set_caret_offset(acc, start if kind == "cursor_before" else end), False)
        if not done:
            raise ComputerUseError("the app refused the selection")
        where = {"text": "selected", "cursor_before": "placed the cursor before", "cursor_after": "placed the cursor after"}[kind]
        return f"{where} {text!r} in {node.body()}"

    def _do_paste(self, app, state, args) -> str:
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise ComputerUseError("text is required")
        kind = str(args.get("format") or "text").lower()
        if kind not in ("text", "md", "markdown", "html"):
            raise ComputerUseError("format must be text, md or html")
        if kind != "text":
            raise ComputerUseError("On Linux paste inserts plain text through the element's editable text; "
                                   "use format=text.")
        target = None
        if args.get("element_index") is not None:
            target = self._element(state, args["element_index"])
        target = target or self._live_focused(app, state)
        self._refuse_secure(target)
        if target is None or not self._insert(target, text):
            raise ComputerUseError("no editable text field has focus; focus one (focus or click) first")
        return f"pasted {len(text)} character(s) as text (inserted directly; the clipboard was not used)"

    def _do_focus(self, app, state, args) -> str:
        index = args.get("element_index")
        acc = self._element(state, index)
        node = self._node_at(state, index)
        self._refuse_secure(acc)
        if not self._has(acc, "FOCUSABLE") or not self._grab_focus(acc):
            raise ComputerUseError(f"element {index} cannot take keyboard focus; click it instead")
        return f"focused {node.body()}"

    def _do_copy(self, app, state, args) -> str:
        """The selected text of the element (or the focused one), read without the clipboard."""
        Atspi = _atspi()
        target = None
        if args.get("element_index") is not None:
            target = self._element(state, args["element_index"])
        target = target or self._live_focused(app, state)
        self._refuse_secure(target)
        if target is None or "Text" not in self._ifaces(target):
            raise ComputerUseError("no text element has focus; pass element_index of the text to copy from")
        ranges = []
        for i in range(self._safe(lambda: Atspi.Text.get_n_selections(target), 0) or 0):
            selected = self._safe(lambda i=i: Atspi.Text.get_selection(target, i))
            if selected is not None and selected.end_offset > selected.start_offset:
                ranges.append(self._safe(lambda s=selected: Atspi.Text.get_text(target, s.start_offset, s.end_offset), ""))
        text = "\n".join(r for r in ranges if r)
        if not text:
            raise ComputerUseError("nothing is selected; select text first (select_text, or press_key ctrl+a)")
        clipped = text if len(text) <= MAX_COPY_CHARS else text[:MAX_COPY_CHARS] + "\n…(truncated)"
        return Report(f"copied {len(text)} character(s) (read from the selection; the clipboard was not touched). "
                      f"Copied text:\n<<<\n{clipped}\n>>>")

    # ------------------------------------------------------------------ menus
    def _menu_bar(self, app: App):
        for window in self._accessible_windows(app):
            stack = [(window, 0)]
            while stack:
                acc, depth = stack.pop()
                if self._role(acc) == "menu bar":
                    return acc
                if depth < 6:
                    stack.extend((c, depth + 1) for c in self._children(acc))
        return None

    def _menu_children(self, item) -> list[Any]:
        return [c for c in self._children(item) if self._role(c) in _MENU_ITEMS]

    def _menu_line(self, item) -> str:
        parts = [self._name(item)]
        attrs = self._safe(lambda: item.get_attributes(), {}) or {}
        shortcut = attrs.get("keybinding") if isinstance(attrs, dict) else ""
        if not shortcut:
            Atspi = _atspi()
            if "Action" in self._ifaces(item):
                shortcut = self._safe(lambda: Atspi.Action.get_key_binding(item, 0), "") or ""
                shortcut = shortcut.split(";")[-1] if shortcut else ""
        if shortcut:
            parts.append(f"({shortcut})")
        if self._role(item) in ("check menu item", "radio menu item") and self._has(item, "CHECKED"):
            parts.append("[checked]")
        if not self._has(item, "ENABLED") and not self._has(item, "SENSITIVE"):
            parts.append("[disabled]")
        if self._menu_children(item):
            parts.append("▸")
        return " ".join(parts)

    def _do_menu(self, app, state, args) -> str:
        path = menu_path(args)
        bar = self._menu_bar(app)
        if bar is None:
            raise ComputerUseError(f"{app.name} has no menu bar (apps with a header bar keep their menu in a button; "
                                   "click it and read the state)")
        level = self._menu_children(bar)
        if not path:
            return Report("menu bar: " + " | ".join(t for t in (self._name(i) for i in level) if t)
                          + ". Pass menu_path to list a menu or press an item.")
        item, walked = None, []
        for step in path:
            wanted = menu_key(step)
            item = next((i for i in level if menu_key(self._name(i).replace("_", "")) == wanted), None)
            if item is None:
                where = " > ".join(walked) or "the menu bar"
                raise ComputerUseError(f"{step!r} is not in {where}; it has: "
                                       + " | ".join(t for t in (self._name(i) for i in level) if t))
            walked.append(self._name(item))
            level = self._menu_children(item)
            # GTK lists a submenu as a "menu" child holding the items.
            if len(level) == 1 and self._role(level[0]) == "menu" and not self._name(level[0]):
                level = self._menu_children(level[0])
        if level:
            return Report(" > ".join(walked) + ":\n" + "\n".join(f"- {self._menu_line(i)}" for i in level
                                                                  if self._name(i)))
        if not self._has(item, "ENABLED") and not self._has(item, "SENSITIVE"):
            raise ComputerUseError(" > ".join(walked) + " is disabled right now")
        if not any(self._do_action(item, name) for name in ("click", "activate", "press")):
            raise ComputerUseError(f"pressing {' > '.join(walked)} failed")
        return "chose " + " > ".join(walked)

    def _do_show_window(self, app, state, args) -> str:
        _, xw = self._pair(app, args.get("window"), args.get("window_id"), state.window_id if state else 0)
        if xw is None:
            raise ComputerUseError(f"{app.name} has no open window")
        if xw.visible:
            return Report("the window is already showing")
        _x("windowmap", "--sync", xw.xid, check=False, timeout=3)
        time.sleep(0.3)
        return "restored the window"

    # ------------------------------------------------------------------ reading aids
    def zoom(self, session: str, query: str, x, y, width, height) -> Observation:
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
            xw = next((w for w in self._x_windows(app.pid) if w.xid == state.window_id), None)
            if xw is None:
                raise ComputerUseError("The window is gone; call get_app_state again.")
            region = (px / state.scale, py / state.scale, pw / state.scale, ph / state.scale)
            image, _ = self._capture(xw, region=region)
            if image is None:
                raise ComputerUseError("The window could not be captured; call get_app_state again.")
            text = (f"Zoomed region ({int(px)}, {int(py)}) {int(pw)}x{int(ph)} of the last screenshot, at full "
                    "pixel density. Coordinates for actions still refer to the last screenshot, not to this image.")
            return Observation(text=text, image=image, app=app.name)

    def find(self, session: str, query: str, text: str = "", role: str = "") -> str:
        with self._lock:
            _, state = self._state_for(session, query)
            needle = " ".join(str(text or "").split()).lower()
            wanted_role = str(role or "").strip().lower()
            if not needle and not wanted_role:
                raise ComputerUseError("find needs text, role, or both")
            hits = []
            for node in state.nodes:
                if wanted_role and wanted_role not in (node.role.lower(), role_name(node.role).lower()):
                    continue
                haystack = " ".join(f"{node.label} {node.value} {node.placeholder}".split()).lower()
                if needle and needle not in haystack:
                    continue
                hits.append(f"{node.index} {node.body()}")
            if not hits and needle and not wanted_role and state.text_boxes:
                seen = ocr.matching(state.text_boxes, needle)
                if seen:
                    return (f"no element matches, but the screenshot shows {len(seen)} line(s) with that text "
                            "(OCR; act with x,y at the centre):\n" + ocr.render(seen[:60]))
            if not hits:
                return "no element of the last tree matches; call get_app_state for a fresh tree, or scroll"
            more = f"\n…and {len(hits) - 60} more; narrow the search." if len(hits) > 60 else ""
            return f"{len(hits)} match(es):\n" + "\n".join(hits[:60]) + more

    # ------------------------------------------------------------------ audio
    @staticmethod
    def _streams(pids: set[int]) -> list[int]:
        """PulseAudio / PipeWire sink inputs that ``pids`` play."""
        try:
            listed = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return []
        out = []
        for block in re.split(r"\n(?=Sink Input #)", listed):
            head = re.match(r"Sink Input #(\d+)", block.strip())
            owner = re.search(r'application\.process\.id = "(\d+)"', block)
            if head and owner and int(owner.group(1)) in pids:
                out.append(int(head.group(1)))
        return out

    def start_audio(self, session: str, query: str, max_duration=None) -> Report:
        try:
            limit = audio.seconds(max_duration)
        except ValueError as exc:
            raise ComputerUseError(str(exc)) from exc
        if shutil.which("parec") is None or shutil.which("pactl") is None:
            raise ComputerUseError("Recording needs PulseAudio or PipeWire with pulseaudio-utils (parec, pactl).")
        with self._lock:
            running = self._recordings.get(session)
            if running is not None and not running.stopped:
                raise ComputerUseError(f"Already recording {running.app}; call stop_audio_recording first.")
            app = self.resolve_app(query)
            recording = _Recording(app=app.name, pid=app.pid, limit=limit)
            recording.thread = threading.Thread(target=self._record, args=(recording,), daemon=True,
                                                name=f"computer-use-audio-{app.pid}")
            recording.thread.start()
            self._recordings[session] = recording
            return Report(f"Recording the sound {app.name} plays, for up to {limit:g} seconds; the user still hears "
                          "it and nothing else is recorded. Act as usual, then call stop_audio_recording.")

    def _record(self, recording: _Recording) -> None:
        """Wait for the app to open a stream, then read it until stopped or the limit."""
        deadline = time.monotonic() + recording.limit
        stream = None
        while stream is None and not recording.stopped and time.monotonic() < deadline:
            # Sound often comes from a child started after recording began (a player, a renderer).
            found = self._streams(_descendants(recording.pid))
            stream = found[0] if found else None
            if stream is None:
                time.sleep(0.25)
        if stream is None:
            return
        try:
            process = subprocess.Popen(
                ["parec", f"--monitor-stream={stream}", "--format=float32le", f"--rate={audio.SAMPLE_RATE}",
                 "--channels=1", "--raw", "--latency-msec=50"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as exc:
            recording.error = str(exc)
            return
        recording.process = process
        # Read until parec ends (stop terminates it), so the samples it still buffers are kept.
        while recording.seconds < recording.limit:
            chunk = process.stdout.read1(6400)
            if not chunk:
                break
            recording.take(chunk)
        process.terminate()

    def stop_audio(self, session: str, query: str = "") -> AudioClip:
        with self._lock:
            recording = self._recordings.get(session)
            if recording is None:
                raise ComputerUseError("Nothing is being recorded; call start_audio_recording first.")
            if query:
                app = self._running_match(query)
                if app is not None and app.pid != recording.pid:
                    raise ComputerUseError(f"The recording is of {recording.app}, not {query}.")
            del self._recordings[session]
        recording.stopped = True
        if recording.process is not None:
            recording.process.terminate()
        if recording.thread is not None:
            recording.thread.join(timeout=3)
        if recording.error:
            raise ComputerUseError(f"Could not record {recording.app}: {recording.error}")
        with recording.lock:
            data, peak = audio.wav(b"".join(recording.chunks))
        return AudioClip(app=recording.app, path=save_recording(recording.app, data), data=data,
                         seconds=round(recording.seconds, 2), peak=peak)

    # ------------------------------------------------------------------ settling
    def settle(self, session: str, query: str) -> float:
        app = self.resolve_app(query, launch=False)
        previous = self._states.get((session, app.pid))
        start = time.monotonic()
        time.sleep(SETTLE_MIN)
        window, _ = self._pair(app, prefer_id=previous.window_id if previous else 0)
        last, busy = self._quick_signature(window)
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= (BUSY_MAX if busy else SETTLE_MAX):
                break
            check_cancelled()
            time.sleep(0.2)
            current, busy = self._quick_signature(window)
            if current == last and not busy:
                break
            last = current
        return time.monotonic() - start

    def _quick_signature(self, window) -> tuple[tuple, bool]:
        out, busy = [], False
        queue = [window] if window is not None else []
        while queue and len(out) < QUICK_NODES:
            acc = queue.pop(0)
            if not (self._has(acc, "SHOWING") or self._has(acc, "VISIBLE")) and acc is not window:
                continue
            role = self._role(acc)
            if role in _BUSY_ROLES and (role == "animation" or (self._value(acc) or 1.0) < 1.0):
                busy = True
            out.append((role, self._name(acc), self._text(acc, 80) or "", self._has(acc, "ENABLED")))
            queue.extend(self._children(acc))
        return tuple(out), busy


def _text_section(boxes: list[ocr.TextBox]) -> list[str]:
    if not boxes:
        return []
    return ["", "Text on the screenshot (OCR; this app exposes little to accessibility, so act on it with x,y at a "
                "line's centre):", ocr.render(boxes)]


_BACKEND: LinuxComputer | None = None
_BACKEND_LOCK = threading.Lock()


def backend() -> LinuxComputer:
    global _BACKEND
    with _BACKEND_LOCK:
        if _BACKEND is None:
            _BACKEND = LinuxComputer()
        return _BACKEND


__all__ = ["LinuxComputer", "OWN_APPS", "backend", "support"]

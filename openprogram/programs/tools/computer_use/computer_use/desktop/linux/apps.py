"""Applications: the running and installed ones, found by name, launched, and refused."""
from __future__ import annotations

import base64
import os
from pathlib import Path
import time
from typing import Any

from ..common.base import NotDispatched, ComputerUseError, RefusedApps, check_cancelled
from .x11 import (App, LAUNCH_SECONDS, OWN_APPS, _WINDOW_ROLES, _application_directories, _atspi, _gio,
    _pid_for_class, _window_class)
from . import x11


class Apps:
    """Finding, launching and describing applications."""

    def _desktop_apps(self) -> list[Any]:
        Atspi = _atspi()
        desktop = Atspi.get_desktop(0)
        return self._children(desktop)

    def _installed(self) -> list[dict[str, str]]:
        """Desktop entries a person would launch from the app grid. Installing or removing an app changes its
        folder's time, so they are read again only then: a permission decision looks them up on every call."""
        signature = []
        for directory in _application_directories():
            try:
                signature.append((directory, os.stat(directory).st_mtime_ns))
            except OSError:
                continue
        held, rows = self._installed_rows
        if held and held == tuple(signature):
            return rows
        rows = self._scan_installed()
        self._installed_rows = (tuple(signature), rows)
        return rows

    def _scan_installed(self) -> list[dict[str, str]]:
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
        exe, comm = x11._proc_exe(pid).lower(), x11._proc_name(pid).lower()
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
        clients = x11._clients()
        listed = [str(c) for c in clients] if clients is not None else \
            x11._x("search", "--onlyvisible", "--name", "", check=False).split()
        for raw in listed:
            pid = x11._x("getwindowpid", raw, check=False)
            owner = int(pid) if pid.isdigit() else _pid_for_class(_window_class(raw))
            if owner:
                out.setdefault(owner, []).append(int(raw))
        try:
            embedded = x11._embedded_windows(clients) if clients else []
        except Exception:  # noqa: BLE001 - embedded windows are extra; the client list stands
            embedded = []
        for xid, owner in embedded:
            out.setdefault(owner, []).append(xid)
        return out

    def _running(self) -> list[App]:
        apps: dict[int, App] = {}
        for acc in self._desktop_apps():
            pid = self._safe(lambda acc=acc: int(acc.get_process_id()), 0)
            if not pid or pid == os.getpid():
                continue
            if not any(self._role(w) in _WINDOW_ROLES for w in self._children(acc)):
                continue
            name = self._name(acc) or x11._proc_name(pid)
            row = self._desktop_for(pid, name)
            apps[pid] = App(pid=pid, name=(row or {}).get("name") or name, desktop_id=(row or {}).get("desktop_id", ""),
                            accessible=acc)
        # Apps that do not talk to the accessibility bus still have X windows.
        for pid in self._x_pids():
            if pid in apps or pid == os.getpid():
                continue
            name = x11._proc_name(pid)
            row = self._desktop_for(pid, name)
            apps[pid] = App(pid=pid, name=(row or {}).get("name") or name, desktop_id=(row or {}).get("desktop_id", ""))
        return sorted(apps.values(), key=lambda a: a.name.lower())

    @staticmethod
    def _keys(app: App | dict) -> tuple[str, ...]:
        if isinstance(app, App):
            return (app.desktop_id, x11._proc_name(app.pid), app.name)
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
            if wanted in (app.desktop_id.lower(), app.name.lower(), x11._proc_name(app.pid).lower(), str(app.pid),
                          app.desktop_id.lower().rsplit(".", 1)[-1]):
                return app
        for app in running:
            if wanted in app.name.lower():
                return app
        return None

    def app_key(self, query: str) -> str:
        """One key for an application however it is named; "" when it is not running."""
        app = self._running_match(query) if isinstance(query, str) and query.strip() else None
        return f"{app.ident or app.pid}".lower() if app is not None else ""

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
            raise NotDispatched(f"{query!r} is not running; list_apps shows the running and installed apps")
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
                                              or x11._proc_exe(app.pid) == row["executable"]):
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
            lines.append(f"- {app.name} ({app.desktop_id or x11._proc_name(app.pid) or 'no desktop entry'}, "
                         f"pid {app.pid}; {', '.join(state)})")
        if len(lines) == 1:
            lines.append("- none with a window")
        ids = {app.desktop_id.lower() for app in running}
        installed = [row for row in self._installed() if row["desktop_id"].lower() not in ids and not self._refused(row)]
        if installed:
            lines += ["", "Installed, not running (get_state launches them):"]
            lines += [f"- {row['name']} ({row['desktop_id']})" for row in installed]
        return "\n".join(lines)

    def app_name(self, query: str) -> str:
        """The display name of the desktop entry ``query`` names exactly (its desktop id, name or executable, or
        the pid of a process of it), as rules name it; "" when it names none. It reads only the desktop entries
        and /proc, never the accessibility bus or the X server, since it runs in every permission decision,
        and it matches exactly, so it never names another application than the one asked for."""
        wanted = str(query or "").strip().lower()
        if not wanted:
            return ""
        if wanted.isdigit():
            pid = int(wanted)
            comm = x11._proc_name(pid)
            if not comm:
                return ""
            row = self._desktop_for(pid, comm)
            return row["name"] if row else comm
        for row in self._installed():
            if wanted in (row["desktop_id"].lower(), row["name"].lower(), row["executable"].lower()):
                return row["name"]
        return ""

    def app_info(self, query: str) -> dict[str, Any]:
        """Display name, desktop id and icon of an app a rule or approval names. Launches nothing."""
        info: dict[str, Any] = {"query": query, "name": query, "bundle_id": "", "icon": "",
                                "installed": False, "running": False, "refused": False}
        if not isinstance(query, str) or not query.strip():
            return info
        app = self._running_match(query)
        # A running app takes its row by its own desktop id only: the query may match another installed app's
        # name in part, and the name shown must be that of the app the call drives.
        if app is not None:
            row = self._installed_match(app.desktop_id) if app.desktop_id else None
        else:
            row = self._installed_match(query)
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

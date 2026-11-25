"""An application's windows: its X windows paired with its accessible windows, listed and picked."""
from __future__ import annotations

import time
from typing import Any

from ..common.base import ComputerUseError, Report
from .x11 import App, WindowState, XWindow, _WINDOW_ROLES
from . import x11


class AppWindows:
    """Which window of an application a call means."""

    def _x_windows(self, pid: int) -> list[XWindow]:
        """Top-level X windows of ``pid`` with a title, mapped ones first."""
        clients = x11._clients()
        visible = set(x11._x("search", "--onlyvisible", "--name", "", check=False).split())
        everything = [str(xid) for xid in self._x_pids().get(pid, [])]
        if clients is None:
            # Without a client list the visible-window walk misses minimized windows.
            everything += [raw for raw in x11._x("search", "--pid", pid, check=False).split() if raw not in everything]
        out = []
        for raw in everything:
            title = x11._x("getwindowname", raw, check=False)
            if not title:
                continue
            geometry = x11._x("getwindowgeometry", "--shell", raw, check=False)
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
            matching = [xw for xw in xwins if needle in xw.title.lower()]
            if len(matching) > 1:
                named = ", ".join(f'"{xw.title}"' for xw in matching[:8])
                raise ComputerUseError(f"{len(matching)} windows have {which!r} in their title: {named}; "
                                       "name one by its window_id from list_windows as title")
            if matching:
                return acc_for(matching[0]), matching[0]
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
        active = x11._x("getactivewindow", check=False)
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

    def _ensure_window(self, app: App) -> WindowState:
        _, xw = self._pair(app)
        if xw is None:
            raise ComputerUseError(f"{app.name} has no open window")
        return WindowState(pid=app.pid, window_title=xw.title, window_id=xw.xid, origin=(xw.x, xw.y),
                           size=(xw.width, xw.height))

    def _do_show_window(self, app, state, args) -> str:
        _, xw = self._pair(app, args.get("window"), args.get("window_id"), state.window_id if state else 0)
        if xw is None:
            raise ComputerUseError(f"{app.name} has no open window")
        if xw.visible:
            return Report("the window is already showing")
        x11._x("windowmap", "--sync", xw.xid, check=False, timeout=3)
        time.sleep(0.3)
        return "restored the window"

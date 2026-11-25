"""Applications: the running and installed ones, found by name, launched, and refused."""
from __future__ import annotations

import os
from pathlib import PureWindowsPath
import subprocess
import time
from typing import Any

from ..common.base import NotDispatched, ComputerUseError, RefusedApps, check_cancelled
from .formats import parse_start_apps
from .win32 import App, LAUNCH_SECONDS, OWN_APPS
from . import win32


class Apps:
    """Finding, launching and describing applications."""

    def _apps_by_pid(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        tops = win32._top_windows()
        for hwnd in tops:
            out.setdefault(win32._window_pid(hwnd), []).append(hwnd)
        try:
            embedded = win32._embedded_windows(tops)
        except Exception:  # noqa: BLE001 - embedded windows are extra; the top-level list stands
            embedded = []
        for hwnd in embedded:
            out.setdefault(win32._window_pid(hwnd), []).append(hwnd)
        return out

    def _installed(self) -> list[dict[str, str]]:
        """Start menu apps, desktop and Store alike, from Get-StartApps; cached for a minute."""
        if self._start_apps and time.monotonic() - self._start_apps[0] < 60:
            return self._start_apps[1]
        try:
            done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                   "Get-StartApps | ConvertTo-Json -Compress"], capture_output=True, text=True,
                                  timeout=20, creationflags=0x08000000)  # CREATE_NO_WINDOW
            rows = parse_start_apps(done.stdout)
        except (OSError, subprocess.TimeoutExpired):
            rows = []
        self._start_apps = (time.monotonic(), rows)
        return rows

    def _running(self) -> list[App]:
        apps = []
        own = os.getpid()
        installed = self._installed()
        for pid in self._apps_by_pid():
            if pid == own:
                continue
            exe = win32._exe(pid)
            stem = PureWindowsPath(exe).stem
            row = next((r for r in installed if r["app_id"].lower().endswith(PureWindowsPath(exe).name.lower())
                        or r["name"].lower() == stem.lower()), None)
            apps.append(App(pid=pid, name=(row or {}).get("name") or stem or str(pid), exe=exe,
                            app_id=(row or {}).get("app_id", "")))
        return sorted(apps, key=lambda a: a.name.lower())

    @staticmethod
    def _keys(app: App | dict) -> tuple[str, ...]:
        if isinstance(app, App):
            return (PureWindowsPath(app.exe).stem, app.name, app.app_id)
        return (str(app.get("name", "")), str(app.get("app_id", "")))

    @classmethod
    def _refused(cls, app: App | dict) -> bool:
        return RefusedApps(OWN_APPS).hits(*cls._keys(app))

    def _running_match(self, query: str) -> App | None:
        wanted = str(query or "").strip().lower()
        if not wanted:
            return None
        running = self._running()
        for app in running:
            if wanted in (app.name.lower(), PureWindowsPath(app.exe).stem.lower(), PureWindowsPath(app.exe).name.lower(),
                          app.app_id.lower(), str(app.pid), app.exe.lower()):
                return app
        return next((app for app in running if wanted in app.name.lower()), None)

    def _installed_match(self, query: str) -> dict[str, str] | None:
        wanted = str(query or "").strip().lower()
        rows = self._installed()
        for row in rows:
            if wanted in (row["name"].lower(), row["app_id"].lower()):
                return row
        return next((row for row in rows if wanted and wanted in row["name"].lower()), None)

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

    def _launch(self, query: str) -> App | None:
        row = self._installed_match(query)
        if row is None:
            return None
        RefusedApps(OWN_APPS).refuse(row["name"], *self._keys(row))
        before = set(self._apps_by_pid())
        # Starting through the shell's AppsFolder works for Store and desktop apps alike.
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{row['app_id']}"])
        deadline = time.monotonic() + LAUNCH_SECONDS
        while time.monotonic() < deadline:
            check_cancelled()
            time.sleep(0.4)
            for app in self._running():
                if app.pid not in before and (app.name == row["name"] or app.app_id == row["app_id"]):
                    return app
        raise ComputerUseError(f"{row['name']} was launched but no window of it appeared within "
                               f"{LAUNCH_SECONDS:g} seconds")

    def list_apps(self) -> str:
        running = self._running()
        by_pid = self._apps_by_pid()
        lines = ["Running applications:"]
        for app in running:
            count = len(by_pid.get(app.pid, []))
            state = [f"{count} window" + ("" if count == 1 else "s")]
            if self._refused(app):
                state.append("not controllable")
            lines.append(f"- {app.name} ({app.ident}, pid {app.pid}; {', '.join(state)})")
        if len(lines) == 1:
            lines.append("- none with a window")
        names = {app.name.lower() for app in running}
        installed = [row for row in self._installed() if row["name"].lower() not in names and not self._refused(row)]
        if installed:
            lines += ["", "Installed, not running (get_state launches them):"]
            lines += [f"- {row['name']} ({row['app_id']})" for row in installed]
        return "\n".join(lines)

    def app_name(self, query: str) -> str:
        """The name rules give the application ``query`` names by its process: the program's file name for a
        pid or an executable path; "" for anything else, which rules take as written. Start menu names come
        from PowerShell, seconds away, and this runs in every permission decision, so they are not read."""
        wanted = str(query or "").strip()
        if not wanted:
            return ""
        if wanted.isdigit():
            return PureWindowsPath(win32._exe(int(wanted))).stem
        if wanted.lower().endswith(".exe") or "\\" in wanted or "/" in wanted:
            return PureWindowsPath(wanted).stem
        return ""

    def app_info(self, query: str) -> dict[str, Any]:
        info: dict[str, Any] = {"query": query, "name": query, "bundle_id": "", "icon": "",
                                "installed": False, "running": False, "refused": False}
        if not isinstance(query, str) or not query.strip():
            return info
        app = self._running_match(query)
        # A running app takes its row by its own app id only: the query, or its name, may match another
        # installed app's name in part, and the name shown must be that of the app the call drives.
        if app is not None:
            row = next((r for r in self._installed() if r["app_id"].lower() == app.app_id.lower()),
                       None) if app.app_id else None
        else:
            row = self._installed_match(query)
        if app is None and row is None:
            return info
        info.update(installed=row is not None, running=app is not None,
                    name=(row or {}).get("name") or (app.name if app else query),
                    bundle_id=(app.ident if app else row["app_id"]))
        info["refused"] = self._refused(app) if app else self._refused(row)
        return info

    def running_apps(self) -> list[dict[str, Any]]:
        return [{"query": app.ident, "name": app.name, "bundle_id": app.ident, "icon": "", "installed": bool(app.app_id),
                 "running": True, "refused": False} for app in self._running() if not self._refused(app)]

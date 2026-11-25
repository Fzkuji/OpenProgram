"""Applications: the running and installed ones, found by name, launched, and refused."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
from typing import Any

from ..common.base import (BadArgument, ComputerUseError, RefusedApps, NotDispatched,
    check_cancelled as _check_cancelled)
from .frameworks import OWN_BUNDLES, _BROWSER_NAMES, _MATCH_ATTEMPTS
from . import frameworks
from . import processes


class Apps:
    """Finding, launching and describing applications."""

    def _window_rows(self) -> list[dict]:
        _, _, cg, _ = frameworks._frameworks()
        return list(cg.CGWindowListCopyWindowInfo(
            cg.kCGWindowListOptionAll | cg.kCGWindowListExcludeDesktopElements, cg.kCGNullWindowID) or [])

    def _app_rows(self) -> list[dict[str, Any]]:
        appkit, _, _, _ = frameworks._frameworks()
        windows: dict[int, int] = {}
        for row in self._window_rows():
            if row.get("kCGWindowLayer") == 0 and row.get("kCGWindowAlpha", 1) > 0:
                pid = int(row.get("kCGWindowOwnerPID", -1))
                windows[pid] = windows.get(pid, 0) + 1
        running = []
        refused = RefusedApps(OWN_BUNDLES)
        for app in processes.running_applications(appkit):
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
        """The applications in ``APP_DIRECTORIES``. Adding, removing or renaming an app changes its folder's
        time, so the list is read again only then: a permission decision looks it up several times per call."""
        signature = []
        for directory in frameworks.APP_DIRECTORIES:
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
        appkit = frameworks._appkit()
        manager = appkit.NSFileManager.defaultManager()
        seen, rows = set(), []
        for directory in frameworks.APP_DIRECTORIES:
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
                # The name the running app goes by (Visual Studio Code runs as "Code"), so a name the model
                # read off a running app finds the same bundle once it has quit.
                bundle_name = str(bundle.objectForInfoDictionaryKey_("CFBundleDisplayName")
                                  or bundle.objectForInfoDictionaryKey_("CFBundleName") or "")
                rows.append({"name": name[:-4], "display_name": display, "bundle_id": bundle_id, "path": path,
                             "bundle_name": bundle_name})
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
            lines.append("Installed, not running (get_state launches them in the background):")
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
        appkit = frameworks._appkit()
        q = query.strip().lower()
        pid = int(q[4:]) if q.startswith("pid:") and q[4:].isdigit() else None
        for app in processes.running_applications(appkit):
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
        if q in _BROWSER_NAMES:
            return self._running_match_once(_BROWSER_NAMES[q])
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
            raise BadArgument("app is required: a name, bundle id or .app path from list_apps")
        app = self._running_match(query)
        if app is None:
            # A refused app says so whether or not it is running.
            target = self._installed_match(query)
            if target is not None:
                self._refuse(target["bundle_id"], target["display_name"])
            if not launch:
                raise NotDispatched(f"{query!r} is not running")
            if target is None:
                raise NotDispatched(f"No application matches {query!r}. Call list_apps to see what is available.")
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
            appkit = frameworks._appkit()
            bundle = appkit.NSBundle.bundleWithPath_(query)
            if bundle and bundle.bundleIdentifier():
                return {"bundle_id": str(bundle.bundleIdentifier()), "display_name": Path(query).stem, "path": query}
        rows = self._installed()
        for row in rows:
            if q in {row["bundle_id"].lower(), row["name"].lower(), row["display_name"].lower(), row["path"].lower()}:
                return row
        # Only after every file and display name, so an app's own name is never taken by another's bundle name.
        found = next((row for row in rows if q and q == row.get("bundle_name", "").lower()), None)
        if found is None and q in _BROWSER_NAMES:
            return self._installed_match(_BROWSER_NAMES[q])
        return found

    def app_name(self, query: str) -> str:
        """The display name of the application ``query`` names (a name, a bundle id, a path, pid:N), as rules
        name it; "" when it names none. The same bundle gives the same name running or not. It looks once,
        without the retries of ``_running_match`` and without an icon, since it runs in every permission
        decision; it never launches anything."""
        if not isinstance(query, str) or not query.strip():
            return ""
        running = self._running_match_once(query)
        if running is not None:
            path = str(running.bundleURL().path()) if running.bundleURL() else ""
            fallback = str(running.localizedName() or "")
        else:
            row = self._installed_match(query)
            if row is None:
                return ""
            path, fallback = row.get("path", ""), row.get("display_name", "")
        name = str(frameworks._appkit().NSFileManager.defaultManager().displayNameAtPath_(path) or "") if path else ""
        name = name[:-4] if name.endswith(".app") else name
        return name or fallback or (Path(path).stem if path else "")

    def app_info(self, query: str) -> dict[str, Any]:
        """What the interface shows for an app a rule or an approval names: its own
        display name, bundle id and icon. Never launches anything."""
        info: dict[str, Any] = {"query": query, "name": query, "bundle_id": "", "icon": "",
                                "installed": False, "running": False, "refused": False}
        if not isinstance(query, str) or not query.strip():
            return info
        appkit = frameworks._appkit()
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
        appkit = frameworks._appkit()
        refused = RefusedApps(OWN_BUNDLES)
        rows = []
        for app in processes.running_applications(appkit):
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
        appkit = frameworks._appkit()
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
        _, ax, _, _ = frameworks._frameworks()
        root = ax.AXUIElementCreateApplication(pid)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._windows(root):
                return
            time.sleep(0.15)

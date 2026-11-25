"""An application's windows: picking one, windows on other Spaces, and the system file dialogs."""
from __future__ import annotations

import os
from pathlib import Path
import threading
import time
from typing import Any

from ..common.base import ComputerUseError, Report, check_cancelled as _check_cancelled
from ..common.tree import role_name
from .frameworks import (OPEN_TIMEOUT, WindowState, _CANCEL_TITLES, _DIALOG_SUBROLES, _FILE_PANELS,
    _LAUNCHING_SUFFIXES, _PANEL_SERVICE, _SHELL_BUNDLES, _unlocked)
from .geometry import _same_frame, panel_hosts
from . import frameworks
from . import skylight


class AppWindows:
    """Which window of an application a call means, and the file dialogs that belong to it."""

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
            app = frameworks._appkit().NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
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
        _, ax, _, _ = frameworks._frameworks()
        error, pid = ax.AXUIElementGetPid(root, None)
        if error:
            return []
        panels = self._panels_for(pid)
        if not panels or not skylight.skylight().available:
            return []
        known = {self._ax_window_id(w) for w in windows}
        frames = [f for f in (self._frame(w) for w in windows) if f is not None]
        found = []
        for service, sid, hid, bounds in panels:
            if hid in known or any(_same_frame(f, bounds) for f in frames):
                continue
            for window in self._attr(ax.AXUIElementCreateApplication(service), "AXWindows") or []:
                if skylight.skylight().window_id(window) == sid:
                    found.append(window)
                    break
        return found

    def _other_space_windows(self, root, windows: list[Any]) -> list[Any]:
        """Windows of the app on other Spaces, which ``AXWindows`` leaves out.

        Their ids are the app's off-screen ordinary windows that no listed
        window has; their elements come from SkyLight's remote-token lookup,
        searched again only when that set of ids changes.
        """
        if not skylight.skylight().available:
            return []
        _, ax, cg, _ = frameworks._frameworks()
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
            cached = (key, skylight.skylight().remote_windows(pid, wanted))
            self._remote[pid] = cached
        return [element for wid, element in sorted(cached[1].items()) if wid in wanted]

    def _ax_window_id(self, window) -> int:
        """The window server's id of an Accessibility window; for a file dialog
        drawn by the panel service, the id of the app window that shows it."""
        wid = (skylight.skylight().window_id(window) or 0) if skylight.skylight().available else 0
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
            matching = [window for window in windows if needle in str(self._attr(window, "AXTitle") or "").lower()]
            if len(matching) == 1:
                return matching[0]
            if matching:
                named = ", ".join(f'{self._ax_window_id(w)} "{self._attr(w, "AXTitle") or ""}"' for w in matching[:8])
                raise ComputerUseError(f"{len(matching)} windows have {which!r} in their title: {named}; "
                                       "name one by its window_id as title")
            titles = [t for w in windows if (t := str(self._attr(w, "AXTitle") or ""))]
            detail = "; its windows are " + ", ".join(f'"{t}"' for t in titles[:8]) if titles else ""
            error, pid = frameworks._frameworks()[1].AXUIElementGetPid(root, None)
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
        _, ax, _, _ = frameworks._frameworks()
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
        _, ax, _, _ = frameworks._frameworks()
        root = ax.AXUIElementCreateApplication(pid)
        if (self._file_panel(self._attr(root, "AXFocusedWindow"))
                or any(self._panel_element(w) for w in self._windows(root))
                or self.__dict__.get("_live_panels", {}).get(pid)):
            raise ComputerUseError(
                f"{app.localizedName()} has the system file dialog open, and macOS does not deliver keys sent in "
                "the background to it, nor to the window under it. To open a file, call open_file(app, path) with "
                "its absolute path: it cancels the dialog first. Otherwise read the dialog (get_state with "
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
        appkit = frameworks._appkit()
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
                "Read it with get_state(window=<its title>) and click its Cancel button, then call open_file again.")
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
        _, ax, _, _ = frameworks._frameworks()
        root = ax.AXUIElementCreateApplication(pid)
        url = str(frameworks._appkit().NSURL.fileURLWithPath_(str(path)).absoluteString())
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for window in self._windows(root):
                title = str(self._attr(window, "AXTitle") or "")
                if str(self._attr(window, "AXDocument") or "") == url or path.name in title:
                    return title
            time.sleep(0.15)
        return ""

    def list_windows(self, query: str) -> str:
        _unlocked()
        _, ax, cg, _ = frameworks._frameworks()
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
        _, ax, _, _ = frameworks._frameworks()
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
        _, _, cg, _ = frameworks._frameworks()
        return {int(r.get("kCGWindowNumber", 0)) for r in cg.CGWindowListCopyWindowInfo(
            cg.kCGWindowListOptionOnScreenOnly, cg.kCGNullWindowID) or []
            if int(r.get("kCGWindowOwnerPID", -1)) == pid}

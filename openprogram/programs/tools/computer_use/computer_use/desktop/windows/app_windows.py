"""An application's windows: listing them, picking one, and bringing one forward."""
from __future__ import annotations

import ctypes
import time

from ..common.base import ComputerUseError, Report
from .win32 import App, Win, WindowState, _class, _rect, _text, _thread
from . import win32


class AppWindows:
    """Which window of an application a call means."""

    def _windows(self, pid: int) -> list[Win]:
        user32 = win32._user32()
        out = []
        for hwnd in self._apps_by_pid().get(pid, []):
            x, y, w, h = _rect(hwnd)
            out.append(Win(hwnd, _text(hwnd), x, y, w, h, bool(user32.IsIconic(hwnd)), _class(hwnd)))
        return out

    def _pick(self, app: App, which=None, window_id=None, prefer_id: int = 0) -> Win | None:
        windows = self._windows(app.pid)
        if window_id not in (None, "", 0):
            try:
                wanted = int(window_id)
            except (TypeError, ValueError) as exc:
                raise ComputerUseError("window_id must be an integer from list_windows") from exc
            for win in windows:
                if win.hwnd == wanted:
                    return win
            raise ComputerUseError(f"window_id {wanted} is not an open window of this app; call list_windows")
        if which not in (None, ""):
            if isinstance(which, int) or str(which).strip().isdigit():
                index = int(which)
                if 0 <= index < len(windows):
                    return windows[index]
                raise ComputerUseError(f"window {index} does not exist; the app has {len(windows)} window(s)")
            needle = str(which).strip().lower()
            matching = [win for win in windows if needle in win.title.lower()]
            if len(matching) > 1:
                named = ", ".join(f'"{win.title}"' for win in matching[:8])
                raise ComputerUseError(f"{len(matching)} windows have {which!r} in their title: {named}; "
                                       "name one by its window_id from list_windows as title")
            if matching:
                return matching[0]
            raise ComputerUseError(f"no window title contains {which!r}")
        for win in windows:
            if win.hwnd == prefer_id:
                return win
        return windows[0] if windows else None

    def list_windows(self, query: str) -> str:
        app = self.resolve_app(query, launch=False)
        front = int(win32._user32().GetForegroundWindow() or 0)
        lines = [f"Windows of {app.name} (pid {app.pid}):"]
        windows = self._windows(app.pid)
        for position, win in enumerate(windows):
            notes = [n for n, on in (("focused", win.hwnd == front), ("minimized", win.minimized)) if on]
            lines.append(f'- {position}: "{win.title}" window_id {win.hwnd}, {win.width}x{win.height} at '
                         f'({win.x}, {win.y})' + (f" [{', '.join(notes)}]" if notes else ""))
        if not windows:
            lines.append("- none open")
        return "\n".join(lines)

    def _ensure_window(self, app: App) -> WindowState:
        win = self._pick(app)
        if win is None:
            raise ComputerUseError(f"{app.name} has no open window")
        return WindowState(pid=app.pid, window_title=win.title, window_id=win.hwnd, origin=(win.x, win.y),
                           size=(win.width, win.height))

    def _focus_hwnd(self, state: WindowState) -> int:
        """The child window holding keyboard focus in the target's thread."""
        from ctypes import wintypes

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                        ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND),
                        ("hwndMenuOwner", wintypes.HWND), ("hwndMoveSize", wintypes.HWND),
                        ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]

        info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        if win32._user32().GetGUIThreadInfo(_thread(state.window_id), ctypes.byref(info)) and info.hwndFocus:
            return int(info.hwndFocus)
        return state.window_id

    def _do_show_window(self, app, state, args) -> str:
        win = self._pick(app, args.get("window"), args.get("window_id"), state.window_id if state else 0)
        if win is None:
            raise ComputerUseError(f"{app.name} has no open window")
        if not win.minimized:
            return Report("the window is already showing")
        win32._user32().ShowWindow(win.hwnd, 4)  # SW_SHOWNOACTIVATE
        time.sleep(0.3)
        return "restored the window without activating it"

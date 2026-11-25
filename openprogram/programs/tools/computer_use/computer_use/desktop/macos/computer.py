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

import threading
from typing import Any

from ..common import audio
from . import window_watch
from .frameworks import WindowState
from .accessibility import Accessibility
from .apps import Apps
from .app_windows import AppWindows
from .state import WindowReading, _report_control
from .pointer import Pointer
from .keyboard import Keyboard
from .clipboard import Clipboard
from .menus import Menus
from .actions import Actions


class MacComputer(Accessibility, Apps, AppWindows, WindowReading, Pointer, Keyboard, Clipboard, Menus, Actions):
    """One backend per worker; state is kept per (session, pid)."""

    def __init__(self) -> None:
        self._states: dict[tuple[str, int], WindowState] = {}
        self._lock = threading.RLock()
        self._icons: dict[str, str] = {}
        # The installed applications and the folder times they were read at: rebuilt only when a folder changes.
        self._installed_rows: tuple[tuple, list[dict[str, str]]] = ((), [])
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


_BACKEND: MacComputer | None = None
_BACKEND_LOCK = threading.Lock()


def backend() -> MacComputer:
    global _BACKEND
    with _BACKEND_LOCK:
        if _BACKEND is None:
            _BACKEND = MacComputer()
        return _BACKEND


__all__ = ["MacComputer", "backend"]

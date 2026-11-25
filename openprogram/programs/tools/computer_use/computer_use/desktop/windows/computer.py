"""Windows backend for the ``computer_use`` tool.

It reads one application window as a screenshot plus an indexed UI
Automation tree, and gives the model the same actions as the macOS and
Linux backends.

* Element actions go through UI Automation patterns (Invoke, Toggle,
  Value, RangeValue, SelectionItem, ExpandCollapse, Scroll, Text,
  LegacyIAccessible) and run in the background.
* Pointer input at a coordinate and plain keys are posted as window
  messages to the child window under the point or holding focus, so the
  window is not raised. Chords with modifiers, and windows that read
  raw input instead of window messages (UWP frames, Chromium), get
  ``SendInput`` with the window brought forward for that step; focus
  and the cursor go back afterwards.
* Screenshots use ``PrintWindow`` with full-content rendering, which
  reads covered windows too; OCR uses tesseract when it is installed.
* Recording uses WASAPI process loopback: only the app's own sound
  (its process tree), while the user keeps hearing it.

Coordinates the model gives are pixels of the last screenshot of that
window; the process is per-monitor DPI aware, so they are physical
screen pixels here.
"""
from __future__ import annotations

import threading
from typing import Any

from .clipboard import Clipboard
from .menus import Menus
from .win32 import WindowState
from .automation import Automation
from .apps import Apps
from .app_windows import AppWindows
from .state import WindowReading
from .pointer import Pointer
from .keyboard import Keyboard
from .actions import Actions


class WindowsComputer(Clipboard, Menus, Automation, Apps, AppWindows, WindowReading, Pointer, Keyboard, Actions):
    """One backend per worker; state is kept per (session, pid)."""

    STATELESS = frozenset({"menu", "show_window", "wait"})

    AT_FOCUS = frozenset({"copy", "press_key", "type_text", "paste"})

    def __init__(self) -> None:
        self._states: dict[tuple[str, int], WindowState] = {}
        self._lock = threading.RLock()
        self._recordings: dict[str, Any] = {}
        self._start_apps: tuple[float, list[dict[str, str]]] | None = None


_BACKEND: WindowsComputer | None = None
_BACKEND_LOCK = threading.Lock()


def backend() -> WindowsComputer:
    global _BACKEND
    with _BACKEND_LOCK:
        if _BACKEND is None:
            _BACKEND = WindowsComputer()
        return _BACKEND


__all__ = ["WindowsComputer", "backend"]

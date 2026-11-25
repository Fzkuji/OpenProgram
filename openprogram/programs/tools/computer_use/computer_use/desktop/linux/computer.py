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

import threading

from .x11 import WindowState, _Recording
from .accessibility import Accessibility
from .apps import Apps
from .app_windows import AppWindows
from .state import WindowReading
from .recording import Recording
from .input import ForegroundInput
from .actions import Actions


class LinuxComputer(Accessibility, Apps, AppWindows, WindowReading, Recording, ForegroundInput, Actions):
    """One backend per worker; state is kept per (session, pid)."""

    STATELESS = frozenset({"menu", "show_window", "wait"})

    AT_FOCUS = frozenset({"copy", "press_key", "type_text", "paste"})

    def __init__(self) -> None:
        self._states: dict[tuple[str, int], WindowState] = {}
        self._lock = threading.RLock()
        self._recordings: dict[str, _Recording] = {}
        self._icons: dict[str, str] = {}
        # The desktop entries and the folder times they were read at: rebuilt only when a folder changes.
        self._installed_rows: tuple[tuple, list[dict[str, str]]] = ((), [])


_BACKEND: LinuxComputer | None = None
_BACKEND_LOCK = threading.Lock()


def backend() -> LinuxComputer:
    global _BACKEND
    with _BACKEND_LOCK:
        if _BACKEND is None:
            _BACKEND = LinuxComputer()
        return _BACKEND


__all__ = ["LinuxComputer", "backend"]

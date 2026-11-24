"""macOS window events for a shown window: Accessibility observers and process exits.

Runs on the run-loop thread of :class:`..ax_events.AXEventLoop`. Each
followed window gets its own notifications (moved, resized, minimized,
restored, title changed, destroyed) and its application its (focused
window changed, hidden, shown, activated); a process that exits without
closing its windows is told by the loop's kqueue.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from ..ax_events import AXEventLoop
from .follow import Report

WINDOW_NOTIFICATIONS = ("AXMoved", "AXResized", "AXWindowMiniaturized", "AXWindowDeminiaturized",
                        "AXTitleChanged", "AXUIElementDestroyed")
APP_NOTIFICATIONS = ("AXFocusedWindowChanged", "AXApplicationHidden", "AXApplicationShown",
                     "AXApplicationActivated")


def windows_for(windows: dict[int, Any], element: Any, equal: Callable[[Any, Any], bool]) -> list[int]:
    """The followed windows a notification's element names: the one window
    it is, or every window of the application when it is the application
    (or an element that is no window of ours any more)."""
    for window_id, known in windows.items():
        if known is not None and element is not None and equal(known, element):
            return [window_id]
    return list(windows)


class MacWindowEvents:
    def __init__(self, report: Report, *, element_of: Callable[[int], Any]) -> None:
        import ApplicationServices

        self._ax = ApplicationServices
        self._report = report
        self._element_of = element_of
        self._lock = threading.Lock()
        self._apps: dict[int, dict[int, Any]] = {}   # pid -> {window id: element}
        self._loop = AXEventLoop(self._on_ax, self._on_exit, name="embedded-window-events")

    def _on_ax(self, pid: int, element: Any, _name: str) -> None:
        with self._lock:
            groups = [self._apps[pid]] if pid in self._apps else list(self._apps.values())
            ids = [wid for windows in groups for wid in windows_for(windows, element, self._loop.equal)]
        for window_id in ids:
            self._report(window_id)

    def _on_exit(self, pid: int) -> None:
        with self._lock:
            ids = list(self._apps.get(pid) or {})
        for window_id in ids:
            self._report(window_id)

    def add(self, window_id: int, pid: int) -> bool:
        element = self._element_of(window_id)
        if element is None or pid <= 0:
            return False
        with self._lock:
            first = pid not in self._apps
            if first and not self._loop.observe(pid, self._ax.AXUIElementCreateApplication(pid), APP_NOTIFICATIONS):
                return False
            if not self._loop.observe(pid, element, WINDOW_NOTIFICATIONS):
                if first:
                    self._loop.drop(pid)
                return False
            self._apps.setdefault(pid, {})[window_id] = element
        return True

    def remove(self, window_id: int) -> None:
        with self._lock:
            for pid, windows in list(self._apps.items()):
                element = windows.pop(window_id, None)
                if element is None:
                    continue
                self._loop.unobserve(pid, element, WINDOW_NOTIFICATIONS)
                if not windows:
                    del self._apps[pid]
                    self._loop.drop(pid)

    def close(self) -> None:
        self._loop.close()


__all__ = ["APP_NOTIFICATIONS", "MacWindowEvents", "WINDOW_NOTIFICATIONS", "windows_for"]

"""Follow a shown window by the operating system's events, not by looking again.

A view of another application's window needs to know when that window
moves, is resized, minimized or restored, renamed, or closed. Each platform
announces this:

* **macOS**: Accessibility observers on the window (moved, resized,
  minimized, restored, title changed, destroyed) and on its application
  (focused window changed, hidden, shown, activated), and a kqueue exit
  event for its process (``follow_macos``).
* **Windows**: ``SetWinEventHook`` on the window's process for location,
  name, show, hide, destroy, minimize and foreground events
  (``follow_windows``).
* **Linux (X11)**: ``StructureNotifyMask`` and ``PropertyChangeMask`` on the
  window: configure, map, unmap, reparent, destroy and its name
  (``follow_linux``). Wayland announces nothing to another client; there
  the embedder is not available at all.

A :class:`Follower` keeps the set of followed windows and runs one platform
*source* while at least one window is followed: its thread starts with the
first window and ends with the last, so nothing runs while no view is
open. Where the source cannot start (no Accessibility access, no X
display), :meth:`Follower.follow` answers ``False`` and the view looks
again on its own short timer, only while it is open.
"""
from __future__ import annotations

import contextlib
import threading
from typing import Callable, Protocol

Report = Callable[[int], None]


class Source(Protocol):
    """One platform's window events, on a thread of its own."""

    def add(self, window_id: int, pid: int) -> bool: ...
    def remove(self, window_id: int) -> None: ...
    def close(self) -> None: ...


MakeSource = Callable[[Report], Source]


class Follower:
    """The windows views are showing, and the one source that follows them.

    ``make_source(report)`` starts a platform source; it calls
    ``report(window_id)`` from its own thread when that window changed.
    ``notify(window_id)`` is told, for followed windows only.
    """

    def __init__(self, make_source: MakeSource, notify: Report) -> None:
        self._make_source = make_source
        self._notify = notify
        self._lock = threading.Lock()
        self._source: Source | None = None
        self._windows: set[int] = set()

    def follow(self, window_id: int, pid: int) -> bool:
        """Follow ``window_id`` (of process ``pid``); whether its events will come."""
        with self._lock:
            if window_id in self._windows:
                return True
            source = self._source
            if source is None:
                try:
                    source = self._make_source(self._changed)
                except Exception:
                    # No events here: the view looks again on its own timer.
                    return False
                self._source = source
            try:
                added = bool(source.add(window_id, pid))
            except Exception:
                added = False
            if added:
                self._windows.add(window_id)
            elif not self._windows:
                self._source = None
                _close(source)
            return added

    def unfollow(self, window_id: int) -> None:
        with self._lock:
            if window_id not in self._windows:
                return
            self._windows.discard(window_id)
            source = self._source
            if self._windows:
                with contextlib.suppress(Exception):
                    source.remove(window_id)
                return
            self._source = None
        _close(source)

    def following(self, window_id: int) -> bool:
        with self._lock:
            return window_id in self._windows

    def stop(self) -> None:
        with self._lock:
            source, self._source = self._source, None
            self._windows.clear()
        _close(source)

    def _changed(self, window_id: int) -> None:
        # No lock: a source may report while :meth:`follow` waits for it to
        # take a new window, and a set lookup is atomic.
        if window_id not in self._windows:
            return
        with contextlib.suppress(Exception):
            self._notify(window_id)


def _close(source: Source | None) -> None:
    if source is None:
        return
    with contextlib.suppress(Exception):
        source.close()


def event_source(report: Report) -> Source:
    """The window events of this platform's embedder; raises where there are none."""
    import platform

    from . import embedder

    system = platform.system()
    instance = embedder()
    if system == "Darwin":
        from .follow_macos import MacWindowEvents
        return MacWindowEvents(report, element_of=lambda window_id: (instance.attached.get(window_id) or {}).get("element"))
    if system == "Windows":
        from .follow_windows import WinWindowEvents
        return WinWindowEvents(report)
    if system == "Linux":
        from .follow_linux import X11WindowEvents
        return X11WindowEvents(report)
    raise RuntimeError(f"No window events on {system}.")


__all__ = ["Follower", "MakeSource", "Report", "Source", "event_source"]

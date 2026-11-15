"""Keep the window the agent operates under a live capture, as Codex does.

While a ScreenCaptureKit stream is open on a window, macOS marks it: a
purple badge over its title-bar buttons and the screen-sharing icon in
the menu bar. Codex Computer Use keeps such a stream on the window it
controls; this does the same so the system draws the marker. The
stream asks for the smallest frame at one frame a second and attaches
no output, so nothing is recorded or kept.

A window stays watched while steps keep arriving and is released after
``IDLE_SECONDS`` without one, or when the agent moves to another window.

While it is watched, the window's place on screen is reported to the App
every ``REPORT_SECONDS`` (when it changed): its frame, its title-bar
buttons and the windows in front of it. The App draws its control overlay
from that: a glowing edge, a control badge over the buttons and the agent
cursor, kept off the windows that cover it. A final report with
``active: False`` takes the overlay down.
"""
from __future__ import annotations

import contextlib
import threading
import time
from typing import Any, Callable

IDLE_SECONDS = 20.0
REPORT_SECONDS = 0.12
# The App's own overlay windows carry this title, so they never count as
# covering the window they are drawn over.
OVERLAY_TITLE = "OpenProgram agent overlay"
# How far outside the window the glow reaches; covering windows further
# away than this do not matter.
GLOW_MARGIN = 32


class _Watch:
    def __init__(self, window_id: int, info: dict[str, Any] | None = None) -> None:
        self.window_id = window_id
        self.stream: Any = None
        self.last = time.monotonic()
        # pid, app_name, buttons (relative to the window) and the run that owns it.
        self.info = dict(info or {})


Report = Callable[[dict[str, Any], dict[str, Any]], None]


class WindowWatch:
    """At most one watched window per session."""

    def __init__(self, report: Report | None = None) -> None:
        self._lock = threading.Lock()
        self._watches: dict[str, _Watch] = {}
        self._timer: threading.Timer | None = None
        self._report = report

    def touch(self, session: str, window_id: int, info: dict[str, Any] | None = None) -> None:
        """A step on ``window_id``: watch it, or keep watching it."""
        if not window_id:
            return
        with self._lock:
            watch = self._watches.get(session)
            if watch is not None and watch.window_id == window_id:
                watch.last = time.monotonic()
                if info:
                    watch.info.update(info)
                return
            old, watch = watch, _Watch(window_id, info)
            self._watches[session] = watch
            self._schedule()
        if old is not None:
            threading.Thread(target=_stop, args=(old,), daemon=True).start()
        threading.Thread(target=self._start, args=(session, watch), daemon=True).start()
        if self._report is not None:
            threading.Thread(target=self._follow, args=(session, watch), daemon=True).start()

    def _current(self, session: str, watch: _Watch) -> bool:
        with self._lock:
            return self._watches.get(session) is watch

    def _follow(self, session: str, watch: _Watch) -> None:
        """Report the watched window's place while it stays watched, then its end."""
        sent: dict[str, Any] | None = None
        while self._current(session, watch):
            try:
                place = window_place(watch.window_id)
            except Exception:
                place = None
            place = place or {"frame": None, "occluders": []}
            if place != sent:
                sent = place
                self._send(watch, {**place, "active": True})
            time.sleep(REPORT_SECONDS)
        self._send(watch, {"frame": None, "occluders": [], "active": False})

    def _send(self, watch: _Watch, place: dict[str, Any]) -> None:
        payload = {"window_id": watch.window_id, "app_name": str(watch.info.get("app_name") or ""),
                   "buttons": watch.info.get("buttons"), **place}
        with contextlib.suppress(Exception):
            # The overlay is a courtesy; reporting never disturbs a step.
            self._report(watch.info, payload)

    def release(self, session: str) -> None:
        with self._lock:
            watch = self._watches.pop(session, None)
        if watch is not None:
            _stop(watch)

    def _start(self, session: str, watch: _Watch) -> None:
        try:
            stream = _open(watch.window_id)
        except Exception:
            # The marker is a courtesy; a step never fails because of it.
            return
        with self._lock:
            current = self._watches.get(session) is watch
            watch.stream = stream
        if not current:
            _stop(watch)

    def _schedule(self) -> None:
        if self._timer is None:
            self._timer = threading.Timer(IDLE_SECONDS / 4, self._sweep)
            self._timer.daemon = True
            self._timer.start()

    def _sweep(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._timer = None
            idle = [s for s, w in self._watches.items() if now - w.last >= IDLE_SECONDS]
            stale = [self._watches.pop(s) for s in idle]
            if self._watches:
                self._schedule()
        for watch in stale:
            _stop(watch)


def _rect(row: dict) -> list[int]:
    b = row.get("kCGWindowBounds") or {}
    return [round(float(b.get(k, 0))) for k in ("X", "Y", "Width", "Height")]


def _meets(a: list[int], b: list[int], margin: int = 0) -> bool:
    return (a[0] - margin < b[0] + b[2] and b[0] < a[0] + a[2] + margin
            and a[1] - margin < b[1] + b[3] and b[1] < a[1] + a[3] + margin)


def window_place(window_id: int, rows: list[dict] | None = None) -> dict[str, Any] | None:
    """The window's frame in screen points and the windows in front of it.

    ``rows`` is the window server's on-screen list, front to back. A window
    that is not on screen (minimized, on another Space) has no place.
    """
    if rows is None:
        import Quartz
        rows = list(Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or [])
    ids = [int(r.get("kCGWindowNumber", 0)) for r in rows]
    if window_id not in ids:
        return None
    index = ids.index(window_id)
    frame = _rect(rows[index])
    occluders = []
    for row in rows[:index]:
        # Only ordinary app windows cover it: the menu bar, the Dock and
        # system layers above them span the screen without hiding it.
        if (row.get("kCGWindowLayer", 0) != 0 or row.get("kCGWindowName") == OVERLAY_TITLE
                or float(row.get("kCGWindowAlpha", 1)) <= 0):
            continue
        rect = _rect(row)
        if rect[2] > 0 and rect[3] > 0 and _meets(frame, rect, GLOW_MARGIN):
            occluders.append(rect)
    return {"frame": frame, "occluders": occluders[:32]}


def _wait(start, timeout: float = 8.0):
    done, box = threading.Event(), []

    def callback(*args):
        box.extend(args)
        done.set()

    start(callback)
    if not done.wait(timeout):
        raise RuntimeError("the window capture did not answer in time")
    value, error = (box[0], box[1]) if len(box) == 2 else (None, box[0] if box else None)
    if error is not None:
        raise RuntimeError(str(error))
    return value


def _open(window_id: int):
    import CoreMedia as cm
    import ScreenCaptureKit as sc

    content = _wait(lambda cb: sc.SCShareableContent
                    .getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(True, False, cb))
    window = next((w for w in content.windows() if int(w.windowID()) == window_id), None)
    if window is None:
        raise RuntimeError(f"window {window_id} is not capturable")
    content_filter = sc.SCContentFilter.alloc().initWithDesktopIndependentWindow_(window)
    config = sc.SCStreamConfiguration.alloc().init()
    config.setWidth_(2)
    config.setHeight_(2)
    config.setMinimumFrameInterval_(cm.CMTimeMake(1, 1))
    config.setShowsCursor_(False)
    stream = sc.SCStream.alloc().initWithFilter_configuration_delegate_(content_filter, config, None)
    _wait(lambda cb: stream.startCaptureWithCompletionHandler_(cb))
    return stream


def _stop(watch: _Watch) -> None:
    stream, watch.stream = watch.stream, None
    if stream is None:
        return
    with contextlib.suppress(Exception):
        _wait(lambda cb: stream.stopCaptureWithCompletionHandler_(cb))

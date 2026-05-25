"""Skill directory watcher.

Uses :mod:`watchdog` when installed; degrades to a 5-second polling
loop otherwise. Triggers a callback on any change under the five
source roots.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from .loader import _source_dirs


_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_callbacks: list[Callable[[], None]] = []


def _mtime_map() -> dict[str, float]:
    out: dict[str, float] = {}
    for _src, root in _source_dirs():
        if not root.exists():
            continue
        for p in root.rglob("SKILL.md"):
            try:
                out[str(p)] = p.stat().st_mtime
            except OSError:
                continue
    return out


def _emit() -> None:
    with _lock:
        cbs = list(_callbacks)
    for cb in cbs:
        try:
            cb()
        except Exception:
            pass


def _try_watchdog() -> bool:
    try:
        from watchdog.events import FileSystemEventHandler  # noqa: F401
        from watchdog.observers import Observer  # noqa: F401
        return True
    except Exception:
        return False


def _run_polling() -> None:
    prev = _mtime_map()
    while not _stop.wait(5.0):
        cur = _mtime_map()
        if cur != prev:
            prev = cur
            _emit()


def _run_watchdog() -> None:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event):  # noqa: D401, ANN001
            _emit()

    observer = Observer()
    handler = Handler()
    for _src, root in _source_dirs():
        try:
            root.mkdir(parents=True, exist_ok=True)
            observer.schedule(handler, str(root), recursive=True)
        except Exception:
            continue
    observer.start()
    try:
        while not _stop.wait(1.0):
            pass
    finally:
        observer.stop()
        observer.join(timeout=2.0)


def start_watcher(on_change: Callable[[], None] | None = None) -> None:
    """Start the watcher thread once. ``on_change`` is appended to the
    callback list; multiple subscribers are supported."""
    global _thread
    with _lock:
        if on_change is not None and on_change not in _callbacks:
            _callbacks.append(on_change)
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        target = _run_watchdog if _try_watchdog() else _run_polling
        _thread = threading.Thread(target=target, name="skills-watcher", daemon=True)
        _thread.start()


def stop_watcher() -> None:
    global _thread
    _stop.set()
    t = _thread
    _thread = None
    if t is not None:
        t.join(timeout=2.0)

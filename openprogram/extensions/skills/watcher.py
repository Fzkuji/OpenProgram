"""Skill directory watcher.

The operating system's file events under every skill source root wake
the watcher (``host/_platform/changes.watch``, topic :data:`TOPIC`); it
then compares the ``SKILL.md`` files with what it saw last and calls the
callbacks only when they differ, so a change to a skill's other files
costs a few ``stat`` calls. Without file events it looks every
:data:`POLL_SECONDS`, and in any case every :data:`RESYNC_SECONDS`. A
source root added later (a plugin's skills) is watched from the next
pass; registering one signals the topic.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

from openprogram.host._platform import changes

from .loader import _source_dirs, _unchanged

_log = logging.getLogger(__name__)

#: File events under the skill source roots, and a plugin's skills registered.
TOPIC = "skills.files"
#: Only where file events cannot be had.
POLL_SECONDS = 5.0
#: A look even without an event, in case one was lost.
RESYNC_SECONDS = 300.0


_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_callbacks: list[Callable[[], None]] = []


def _mtime_map() -> dict[str, float]:
    return _walk()[0]


def _walk() -> tuple[dict[str, float], dict[str, int], dict[str, tuple[int, int]], list[str]]:
    """The ``SKILL.md`` files of every source, and what would show that a
    second walk finds others: each directory's time, each file's time and
    size, and the sources that are not there yet."""
    out: dict[str, float] = {}
    directories: dict[str, int] = {}
    files: dict[str, tuple[int, int]] = {}
    absent: list[str] = []
    for _src, root in _source_dirs():
        if not root.exists():
            absent.append(str(root))
            continue
        for folder, _names, names in os.walk(root):
            try:
                directories[folder] = os.stat(folder).st_mtime_ns
            except OSError:
                continue
            if "SKILL.md" not in names:
                continue
            path = os.path.join(folder, "SKILL.md")
            try:
                seen = os.stat(path)
            except OSError:
                continue
            out[path] = seen.st_mtime
            files[path] = (seen.st_mtime_ns, seen.st_size)
    return out, directories, files, absent


def _emit() -> None:
    with _lock:
        cbs = list(_callbacks)
    for cb in cbs:
        try:
            cb()
        except Exception:
            # Callbacks are caller code; one failing must not starve the rest.
            _log.warning("skills change callback %r failed", cb, exc_info=True)


def _scan() -> tuple | None:
    """One reading of the skill folders, or ``None`` when it could not be
    taken (a folder vanished mid-walk, the state directory is unreadable):
    the watcher tries again at the next tick instead of ending for good."""
    try:
        return _walk()
    except Exception:
        _log.warning("skills folders could not be read; trying again", exc_info=True)
        return None


def _same(seen: tuple) -> bool:
    """Whether a walk now would read what ``seen`` did, from the times of
    its directories and files alone. A user's skills hold thousands of
    other files; walking them every five seconds was most of this thread."""
    try:
        if any(os.path.exists(root) for root in seen[3]):
            return False
        return _unchanged(seen[1], seen[2])
    except Exception:
        return False


def _watch_roots(watched: set[str]) -> set[str]:
    """Watch every source root, including one registered since the last pass."""
    try:
        roots = {str(root) for _src, root in _source_dirs()}
        for root in roots - watched:
            changes.watch(root, TOPIC, recursive=True)
        for root in watched - roots:
            changes.unwatch(root, TOPIC)
    except Exception:  # noqa: BLE001 - the roots watched so far stay
        _log.warning("skills folders not watched", exc_info=True)
        return watched
    return roots


def _wait(due: float | None) -> bool:
    """Wait for a change (or ``due``); True once the watcher is asked to stop."""
    changes.wait_for(_listener, resync=RESYNC_SECONDS, poll=POLL_SECONDS, due=due)
    return _stop.is_set()


def _run() -> None:
    watched = _watch_roots(set())
    prev = _scan()
    # A reading that failed is taken again soon, not at the next change.
    retry_at = time.time() + POLL_SECONDS if prev is None else None
    while not _wait(retry_at):
        retry_at = None
        watched = _watch_roots(watched)
        if prev is not None and _same(prev):
            continue
        cur = _scan()
        if cur is None:
            retry_at = time.time() + POLL_SECONDS
            continue
        changed = prev is None or cur[0] != prev[0]
        prev = cur
        if changed:
            _emit()


_listener = changes.listen(TOPIC)


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
        _thread = threading.Thread(target=_run, name="skills-watcher", daemon=True)
        _thread.start()


def stop_watcher() -> None:
    global _thread
    _stop.set()
    _listener.wake()
    t = _thread
    _thread = None
    if t is not None:
        t.join(timeout=2.0)

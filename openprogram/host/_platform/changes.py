"""How a Worker learns that something changed, instead of looking again.

Every background loop and every open window used to look again every few
seconds: an idle Worker spent seconds of processor time each minute finding
nothing new. Here a change is announced once and whoever waits on it wakes;
nothing runs while nothing changes. OpenCode works the same way: one bus,
file events from the operating system, and windows that are told.

Four sources announce a change:

* :func:`signal` from the code that made it, in this process.
* :func:`watch` for files another process writes (the CLI, a program's own
  process, the user editing a skill): one thread takes the operating
  system's file events (FSEvents, inotify, ReadDirectoryChangesW, through
  ``watchfiles``) and signals the topic a path was registered under.
* ``process_exits.watch_process`` for a process another one depends on (a
  command's supervisor, the update controller): the operating system says
  when it ends, and its topic is signalled.
* :func:`publish` forwards a topic to the open windows as a ``changed``
  frame, so a window reads again when told instead of on a timer.

It sits at the bottom of the layers, beside the other operating-system
services, so the stores of every layer can signal; the server hands it the
way to reach windows.

Nothing here starts a thread until the Worker calls :func:`start_watching`
(file events and process exits) and :func:`set_publisher` (frames); in the CLI and in tests a
signal only wakes listeners, and a waiter looks on its own ``poll``.

A waiter (:func:`listen`) still wakes at a long ``resync`` interval, in case
an announcement was lost, and at the short ``poll`` interval its caller
gives when file events cannot be had on this machine.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

_log = logging.getLogger(__name__)

#: A file change reaches its waiters within about this long.
_STEP_MS = 500
_DEBOUNCE_MS = 1_000
#: Changes to a published topic within this long go to the windows as one frame.
_PUBLISH_COALESCE_SECONDS = 0.2

_lock = threading.Lock()
_listeners: dict[str, set["Listener"]] = {}


class Listener:
    """Wakes when one of its topics is signalled.

    What was signalled since the last :meth:`wait` is kept, so a change made
    while the waiter was busy is not lost.
    """

    def __init__(self, topics: Iterable[str]) -> None:
        self.topics = tuple(dict.fromkeys(topics))
        self._condition = threading.Condition()
        self._pending: set[str] = set()
        self._closed = False

    def _add(self, key: str) -> None:
        with self._condition:
            self._pending.add(key)
            self._condition.notify_all()

    def wait(self, timeout: float | None) -> set[str]:
        """What changed, or an empty set once ``timeout`` passed.

        A key is ``topic`` or ``topic:key`` as it was signalled.
        """
        with self._condition:
            if not self._pending and not self._closed:
                self._condition.wait(timeout)
            pending, self._pending = self._pending, set()
            return pending

    def wake(self) -> None:
        """Wake the waiter without a change (a shutdown, a new deadline)."""
        self._add("")

    def close(self) -> None:
        with _lock:
            for topic in self.topics:
                listeners = _listeners.get(topic)
                if listeners is not None:
                    listeners.discard(self)
                    if not listeners:
                        _listeners.pop(topic, None)
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def __enter__(self) -> "Listener":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def listen(*topics: str) -> Listener:
    """A listener for ``topics``; close it when it is no longer wanted."""
    listener = Listener(topics)
    with _lock:
        for topic in listener.topics:
            _listeners.setdefault(topic, set()).add(listener)
    return listener


_callbacks: dict[str, list[Callable[[tuple[str, ...]], None]]] = {}


def subscribe(topic: str, callback: Callable[[tuple[str, ...]], None]) -> Callable[[], None]:
    """Call ``callback(keys)`` on the signalling thread each time ``topic``
    is signalled; for a loop that already waits on its own event. It must
    be quick (set an event) and must not raise. Returns the unsubscribe."""
    with _lock:
        _callbacks.setdefault(topic, []).append(callback)

    def unsubscribe() -> None:
        with _lock:
            known = _callbacks.get(topic, [])
            if callback in known:
                known.remove(callback)

    return unsubscribe


def signal(topic: str, *keys: str) -> None:
    """Announce that ``topic`` changed, for ``keys`` when given (a
    conversation, a job). Cheap when nobody listens."""
    with _lock:
        listeners = tuple(_listeners.get(topic, ()))
        callbacks = tuple(_callbacks.get(topic, ()))
        published = topic in _published
    if listeners:
        marks = [f"{topic}:{key}" for key in keys if key] or [topic]
        for listener in listeners:
            for mark in marks:
                listener._add(mark)
    for callback in callbacks:
        try:
            callback(keys)
        except Exception:  # noqa: BLE001 - a subscriber never breaks the writer
            _log.warning("change callback for %s failed", topic, exc_info=True)
    if published:
        _queue_publish(topic, keys)


def wait_for(listener: Listener, *, resync: float, poll: float | None = None,
             due: float | None = None) -> set[str]:
    """Wait on ``listener`` until a change, the next ``due`` time
    (``time.time()``), or ``resync`` seconds.

    ``poll`` is how often to look when file events cannot be had here; a
    loop that relies on :func:`watch` passes it.
    """
    timeout = resync
    if poll is not None and not file_events_available():
        timeout = min(timeout, poll)
    if due is not None:
        timeout = min(timeout, max(0.0, due - time.time()))
    return listener.wait(timeout)


# -- Windows ---------------------------------------------------------------

_published: set[str] = set()
_publish_pending: dict[str, set[str]] = {}
_publish_scheduled = False
#: ``schedule(delay, callback)`` on the server's event loop and ``emit(frame)``
#: to the windows, installed by the server when it starts; without them
#: there are no windows to tell.
_schedule: Callable[[float, Callable[[], None]], None] | None = None
_emit: Callable[[dict], None] | None = None


def publish(*topics: str) -> None:
    """Forward ``topics`` to the open windows as ``changed`` frames."""
    with _lock:
        _published.update(topics)


def set_publisher(schedule: Callable[[float, Callable[[], None]], None] | None,
                  emit: Callable[[dict], None] | None = None) -> None:
    """Install how frames are sent later (the server's event loop) and
    how a frame reaches the windows, or none."""
    global _schedule, _emit, _publish_scheduled
    with _lock:
        _schedule = schedule if emit is not None else None
        _emit = emit
        _publish_scheduled = False
        _publish_pending.clear()


def _queue_publish(topic: str, keys: tuple[str, ...]) -> None:
    global _publish_scheduled
    with _lock:
        schedule = _schedule
        if schedule is None:
            return
        known = _publish_pending.setdefault(topic, set())
        known.update(key for key in keys if key)
        if not keys:
            known.add("")
        if _publish_scheduled:
            return
        _publish_scheduled = True
    try:
        schedule(_PUBLISH_COALESCE_SECONDS, _flush_publish)
    except Exception:  # noqa: BLE001 - the loop is closing
        with _lock:
            _publish_scheduled = False


def _flush_publish() -> None:
    global _publish_scheduled
    with _lock:
        pending = dict(_publish_pending)
        _publish_pending.clear()
        _publish_scheduled = False
    emit = _emit
    if not pending or emit is None:
        return
    for topic, keys in pending.items():
        everything = "" in keys
        try:
            emit({"type": "changed", "topic": topic,
                           "keys": None if everything else sorted(keys)})
        except Exception:  # noqa: BLE001 - a window reads again when it reconnects
            _log.debug("changed frame for %s was not sent", topic, exc_info=True)


# -- Files -----------------------------------------------------------------

@dataclass(eq=False)
class _Watched:
    path: Path
    topic: str
    recursive: bool
    names: frozenset[str] | None
    key: Callable[[Path], str | None] | None
    anchor: Path = field(default_factory=Path)


_watched: list[_Watched] = []
_watch_threads: dict[bool, tuple[threading.Thread, threading.Event]] = {}
_unavailable = False
_started = False
_stopping = threading.Event()


def file_events_available() -> bool:
    """Whether file changes reach :func:`watch`'s topics: the Worker has
    started watching and the operating system's events work here."""
    return _started and not _unavailable


def start_watching() -> bool:
    """Begin taking file events for every path :func:`watch` registered,
    before or after, and process exits for every process
    ``process_exits.watch_process`` registered. The Worker calls this once;
    elsewhere (the CLI, tests) registrations are only kept and waiters look
    on their ``poll``."""
    global _started, _unavailable
    from . import process_exits
    process_exits.start()
    try:
        import watchfiles  # noqa: F401
    except Exception:  # noqa: BLE001
        _unavailable = True
        _log.warning("watchfiles is not installed; loops look on their own")
        return False
    _stopping.clear()
    _started = True
    _restart_watchers()
    return True


def watch(path: str | os.PathLike, topic: str, *, recursive: bool = False,
          names: Iterable[str] | None = None,
          key: Callable[[Path], str | None] | None = None) -> None:
    """Signal ``topic`` when something under ``path`` changes.

    ``path`` is a directory: its entries are watched, and its whole tree
    when ``recursive``. ``names`` limits it to entries with those names (a
    file is watched by its directory and its name, so a file replaced by a
    rename is still seen). A path that does not exist yet is
    watched from its nearest existing parent, and from itself once made.
    ``key`` turns a changed path into the key signalled with the topic.
    """
    # Events name real paths (/private/var on macOS, not /var).
    entry = _Watched(_real(Path(path)), topic, recursive,
                     frozenset(names) if names is not None else None, key)
    with _lock:
        for known in _watched:
            if (known.path, known.topic, known.recursive, known.names) == (
                    entry.path, entry.topic, entry.recursive, entry.names):
                return
        _watched.append(entry)
    _restart_watchers()


def unwatch(path: str | os.PathLike, topic: str) -> None:
    with _lock:
        _watched[:] = [w for w in _watched if not (w.path == _real(Path(path)) and w.topic == topic)]
    _restart_watchers()


def stop_watching() -> None:
    """End the file and process watcher threads (the Worker is stopping)."""
    global _started
    from . import process_exits
    process_exits.stop()
    _started = False
    _stopping.set()
    with _lock:
        threads = list(_watch_threads.values())
        _watch_threads.clear()
    for thread, stop in threads:
        stop.set()
    for thread, _stop in threads:
        thread.join(timeout=2.0)


def _real(path: Path) -> Path:
    """``path`` with its existing part resolved through links."""
    found = _anchor(path)
    return found.resolve() / path.relative_to(found)


def _anchor(path: Path) -> Path:
    found = path
    while not found.exists() and found.parent != found:
        found = found.parent
    return found


def _restart_watchers() -> None:
    """One watcher thread per kind (recursive or not), started again with
    the current set of paths whenever it changes.

    Threads are made, started and the old ones stopped under the lock: two
    registrations from different threads at once otherwise each started
    the threads the other had just made, and the second start raised."""
    if _stopping.is_set() or not file_events_available():
        return
    with _lock:
        for entry in _watched:
            entry.anchor = _anchor(entry.path)
        wanted: dict[bool, set[str]] = {}
        for entry in _watched:
            recursive = entry.recursive and entry.anchor == entry.path
            wanted.setdefault(recursive, set()).add(str(entry.anchor))
        old = dict(_watch_threads)
        _watch_threads.clear()
        for recursive, paths in wanted.items():
            stop = threading.Event()
            thread = threading.Thread(
                target=_run_watcher, args=(sorted(paths), recursive, stop),
                name="file-changes" + ("-tree" if recursive else ""), daemon=True)
            _watch_threads[recursive] = (thread, stop)
            thread.start()
        for _thread, stop in old.values():
            stop.set()


def _run_watcher(paths: list[str], recursive: bool, stop: threading.Event) -> None:
    global _unavailable
    try:
        import watchfiles
        for changes in watchfiles.watch(
            *paths, watch_filter=None, debounce=_DEBOUNCE_MS, step=_STEP_MS,
            stop_event=stop, rust_timeout=0, recursive=recursive,
            raise_interrupt=False, ignore_permission_denied=True,
        ):
            _dispatch(Path(changed) for _kind, changed in changes)
    except Exception:  # noqa: BLE001 - waiters fall back to looking
        if not stop.is_set():
            _unavailable = True
            _log.warning("file events are not available; loops look on their own", exc_info=True)
            # Every waiter is woken once so it picks up its short poll.
            with _lock:
                listeners = {l for group in _listeners.values() for l in group}
            for listener in listeners:
                listener.wake()


def _dispatch(paths: Iterable[Path]) -> None:
    moved = False
    hits: dict[str, set[str]] = {}
    with _lock:
        entries = list(_watched)
    for changed in paths:
        for entry in entries:
            if entry.anchor != entry.path:
                # Waiting for the path itself to appear.
                if changed == entry.path or entry.path.is_relative_to(changed) or changed.is_relative_to(entry.path):
                    moved = moved or _anchor(entry.path) != entry.anchor
                    if entry.path.exists():
                        hits.setdefault(entry.topic, set()).add("")
                continue
            if changed == entry.path:
                # A watched folder that is still there changed only its own
                # metadata (a chmod on every write lock, for one): nothing in
                # it changed, and a loop that locks it would wake itself.
                if entry.path.is_dir():
                    continue
                inside = True
            elif entry.recursive:
                inside = changed.is_relative_to(entry.path)
            else:
                inside = changed.parent == entry.path
            if not inside:
                continue
            if entry.names is not None and changed.name not in entry.names:
                continue
            if changed == entry.path and not entry.path.exists():
                moved = True
            key = entry.key(changed) if entry.key is not None else None
            hits.setdefault(entry.topic, set()).add(key or "")
    for topic, keys in hits.items():
        if "" in keys:
            signal(topic)
        else:
            signal(topic, *sorted(keys))
    if moved:
        _restart_watchers()


__all__ = [
    "Listener", "file_events_available", "listen", "publish", "set_publisher",
    "signal", "start_watching", "stop_watching", "subscribe", "unwatch", "wait_for",
    "watch",
]

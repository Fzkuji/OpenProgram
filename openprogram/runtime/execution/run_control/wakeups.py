"""Wake whatever waits on a stop signal the moment it is set.

A run's stop signal is a plain Event (a ``threading.Event`` for the turn,
an ``asyncio.Event`` inside its loop). It stays plain because request
options holding it are deep-copied, and an Event with a waiter attached
cannot be. What must happen when it is set (wake the turn's event loop,
cancel the task reading the provider stream) is registered here instead,
in a table keyed by the signal that is never copied, and :func:`fire`
runs it right after the signal is set (Codex: ``or_cancel(&token)`` on the
stream read, no polling).

A callback registered with a ``loop`` runs on that loop
(``call_soon_threadsafe``), so a stop from any thread reaches the loop in
one hop; a loop that has closed is skipped. Without a loop it runs in the
thread that fires.
"""
from __future__ import annotations

import itertools
import logging
import threading
import weakref
from dataclasses import dataclass
from typing import Any, Callable

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Entry:
    handle: int
    signal: "weakref.ReferenceType[Any]"
    callback: Callable[[], None]
    loop: Any = None


_lock = threading.Lock()
_entries: dict[int, list[_Entry]] = {}
_handles = itertools.count(1)


def register(signal: Any, callback: Callable[[], None], *, loop: Any = None) -> int:
    """Run ``callback`` when ``signal`` is fired; returns the handle to
    :func:`unregister` with. ``loop``: run it on that event loop."""
    handle = next(_handles)
    entry = _Entry(handle, weakref.ref(signal), callback, loop)
    with _lock:
        _entries.setdefault(id(signal), []).append(entry)
    return handle


def unregister(handle: int) -> None:
    with _lock:
        for key, entries in list(_entries.items()):
            kept = [entry for entry in entries if entry.handle != handle]
            if len(kept) != len(entries):
                if kept:
                    _entries[key] = kept
                else:
                    del _entries[key]
                return


def fire(signal: Any) -> None:
    """Run what is registered for ``signal`` (it has just been set)."""
    with _lock:
        entries = [entry for entry in _entries.get(id(signal), ()) if entry.signal() is signal]
    for entry in entries:
        try:
            if entry.loop is not None:
                if entry.loop.is_closed():
                    continue
                entry.loop.call_soon_threadsafe(entry.callback)
            else:
                entry.callback()
        except RuntimeError:
            # The loop closed between the check and the call: its turn ended.
            continue
        except Exception:
            _log.warning("stop wakeup failed", exc_info=True)


def set_and_fire(signal: Any) -> None:
    """Set ``signal`` and wake what waits on it."""
    signal.set()
    fire(signal)


def registered(signal: Any) -> int:
    """How many callbacks wait on ``signal`` (for tests)."""
    with _lock:
        return sum(1 for entry in _entries.get(id(signal), ()) if entry.signal() is signal)


__all__ = ["fire", "register", "registered", "set_and_fire", "unregister"]

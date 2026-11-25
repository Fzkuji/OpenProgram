"""The engine turns running in this process, by the agent session they write into.

An engine (``runtime.harnesses``) counts a turn in while it writes into an
agent's own session, and out when it has kept what it wrote; the app sync
leaves a session alone while any turn is counted in it, and writes what it
read only when no turn started since (``started``, ``unchanged``).
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

#: How many turns run in each (provider, agent session id).
running: dict[tuple[str, str], int] = {}
#: How many turns have started in each, ever: a reading taken before one started is stale.
_started: dict[tuple[str, str], int] = {}
_lock = threading.Lock()


class Busy(Exception):
    """An engine turn started in the agent session after the sync read it."""


def enter_running(key: tuple[str, str]) -> None:
    with _lock:
        running[key] = running.get(key, 0) + 1
        _started[key] = _started.get(key, 0) + 1


def started(key: tuple[str, str]) -> int:
    """How many engine turns have started in ``key``: taken before the sync reads it."""
    with _lock:
        return _started.get(key, 0)


@contextmanager
def unchanged(key: tuple[str, str], since: int) -> Iterator[bool]:
    """Whether no engine turn ran in ``key`` since ``started`` said ``since``; while the
    block runs, no turn starts there, so what the sync read is what it writes."""
    with _lock:
        yield not running.get(key) and _started.get(key, 0) == since


def leave_running(key: tuple[str, str]) -> None:
    with _lock:
        left = running.get(key, 0) - 1
        if left > 0:
            running[key] = left
        else:
            running.pop(key, None)


def is_running(key: tuple[str, str]) -> bool:
    return running.get(key, 0) > 0

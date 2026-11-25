"""The engine turns running in this process, by the agent session they write into.

An engine (``runtime.harnesses``) counts a turn in while it writes into an
agent's own session, and out when it has kept what it wrote; the app sync
leaves a session alone while any turn is counted in it.
"""
from __future__ import annotations

import threading

#: How many turns run in each (provider, agent session id).
running: dict[tuple[str, str], int] = {}
_lock = threading.Lock()


def enter_running(key: tuple[str, str]) -> None:
    with _lock:
        running[key] = running.get(key, 0) + 1


def leave_running(key: tuple[str, str]) -> None:
    with _lock:
        left = running.get(key, 0) - 1
        if left > 0:
            running[key] = left
        else:
            running.pop(key, None)


def is_running(key: tuple[str, str]) -> bool:
    return running.get(key, 0) > 0

"""What this worker started, as reported to ``/api/diagnostics``.

A switch compares this before and after, so a change that stops the scheduler
is caught even though the process is healthy. A channel's network state is
not recorded here, only whether it started.
"""
from __future__ import annotations

import threading

RUNNING = "running"
DEFERRED = "deferred"
FAILED = "failed"

_lock = threading.Lock()
_state: dict[str, dict[str, str]] = {}


def record(name: str, status: str, error: str = "") -> None:
    entry = {"status": status}
    if error:
        entry["error"] = error[:500]
    with _lock:
        _state[name] = entry


def snapshot() -> dict[str, dict[str, str]]:
    with _lock:
        return {name: dict(entry) for name, entry in _state.items()}


def clear() -> None:
    with _lock:
        _state.clear()


__all__ = ["DEFERRED", "FAILED", "RUNNING", "clear", "record", "snapshot"]

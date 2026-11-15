"""Channel adapter heartbeat registry.

Each channel adapter runs in its own daemon thread inside the worker
process. A separate watcher thread (started from ``runner.py``)
periodically inspects ``thread.is_alive()`` for each adapter and
stamps a wall-clock timestamp here. The webui ``/api/channels/.../status``
endpoint then reads that timestamp to answer "is this adapter still
running?" — without touching adapter code at all.

Why not have each adapter heartbeat itself? Adapters block in
third-party event loops (``discord.Client.run``, telethon's
``run_until_disconnected``, slack-sdk's socket-mode handler) where
there is no convenient hook to insert a periodic timer. Watching from
the outside via ``thread.is_alive()`` is coarse but reliable, and good
enough for the UI signal ("green = adapter thread running, red = it
died / never started").
"""
from __future__ import annotations

import threading
import time
from typing import Optional


_LOCK = threading.Lock()
_HEARTBEATS: dict[tuple[str, str], float] = {}
_STOPS: dict[tuple[str, str], str] = {}
_RETRIES: dict[tuple[str, str], float] = {}

#: The adapter returned on its own: its login no longer works.
STOP_SIGNED_OUT = "signed_out"
#: The adapter could not be built, or its thread died on an error.
STOP_FAILED = "failed"


def heartbeat(channel: str, account_id: str, ts: Optional[float] = None) -> None:
    """Record that ``(channel, account_id)``'s adapter was alive at ``ts``."""
    with _LOCK:
        _HEARTBEATS[(channel, account_id)] = ts if ts is not None else time.time()


def get_last_seen(channel: str, account_id: str) -> Optional[float]:
    """Return the last heartbeat timestamp, or ``None`` if never recorded."""
    with _LOCK:
        return _HEARTBEATS.get((channel, account_id))


def clear(channel: str, account_id: str) -> None:
    """Drop the heartbeat entry (called when the adapter thread exits)."""
    with _LOCK:
        _HEARTBEATS.pop((channel, account_id), None)


def record_stop(channel: str, account_id: str, reason: str) -> None:
    """Remember why an adapter stopped for good, until the next start."""
    with _LOCK:
        _STOPS[(channel, account_id)] = reason


def get_stop(channel: str, account_id: str) -> Optional[str]:
    """Return why the adapter stopped, or ``None`` if it has not."""
    with _LOCK:
        return _STOPS.get((channel, account_id))


def clear_stop(channel: str, account_id: str) -> None:
    """Forget stops and retries (called just before a new adapter thread)."""
    with _LOCK:
        _STOPS.pop((channel, account_id), None)
        _RETRIES.pop((channel, account_id), None)


def record_retry(channel: str, account_id: str, settles_in: float) -> None:
    """The adapter crashed and will reconnect; its thread is still alive.

    A blocking ``run`` gives no signal that it connected, so the mark
    expires ``settles_in`` seconds from now unless another crash renews it.
    """
    with _LOCK:
        _RETRIES[(channel, account_id)] = time.time() + settles_in


def is_retrying(channel: str, account_id: str) -> bool:
    """Return whether the adapter crashed recently and is reconnecting."""
    with _LOCK:
        until = _RETRIES.get((channel, account_id))
    return until is not None and time.time() < until

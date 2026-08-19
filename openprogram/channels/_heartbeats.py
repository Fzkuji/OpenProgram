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

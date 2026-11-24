"""Channel adapter running marks.

Each channel adapter runs in its own daemon thread inside the worker
process. The channel supervisor (``operations/worker/runner.py``) stamps a
wall-clock time here when an adapter thread starts and clears it when the
thread ends, which the thread announces as it returns. The webui
``/api/channels/.../status`` endpoint reads the mark to answer "is this
adapter running?" without touching adapter code at all.

Why not have each adapter report itself? Adapters block in third-party
event loops (``discord.Client.run``, telethon's
``run_until_disconnected``, slack-sdk's socket-mode handler) where there
is no convenient hook to insert anything. Watching the thread from the
outside is coarse but reliable, and good enough for the UI signal ("green
= adapter thread running, red = it died / never started").

Every change that makes the status endpoint answer differently signals
``channels`` keyed ``<channel>:<account>`` (a mark when it appears, not on
a repeat stamp), so an open window reads the status when it changes
instead of on a timer. The one answer that changes with time alone is a
retry mark running out, which the endpoint reports as ``recheck_in``.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from openprogram.host._platform.changes import publish, signal


_LOCK = threading.Lock()
_HEARTBEATS: dict[tuple[str, str], float] = {}
_STOPS: dict[tuple[str, str], str] = {}
_RETRIES: dict[tuple[str, str], float] = {}

#: A stamp this much newer than the last one is announced again.
STALE_AFTER_SECONDS = 30.0

publish("channels")


def _changed(channel: str, account_id: str) -> None:
    signal("channels", f"{channel}:{account_id}")

#: The adapter returned on its own: its login no longer works.
STOP_SIGNED_OUT = "signed_out"
#: The adapter could not be built, or its thread died on an error.
STOP_FAILED = "failed"


def heartbeat(channel: str, account_id: str, ts: Optional[float] = None) -> None:
    """Record that ``(channel, account_id)``'s adapter was alive at ``ts``."""
    now = ts if ts is not None else time.time()
    with _LOCK:
        previous = _HEARTBEATS.get((channel, account_id))
        _HEARTBEATS[(channel, account_id)] = now
    if previous is None or now - previous >= STALE_AFTER_SECONDS:
        _changed(channel, account_id)


def get_last_seen(channel: str, account_id: str) -> Optional[float]:
    """Return the last heartbeat timestamp, or ``None`` if never recorded."""
    with _LOCK:
        return _HEARTBEATS.get((channel, account_id))


def clear(channel: str, account_id: str) -> None:
    """Drop the heartbeat entry (called when the adapter thread exits)."""
    with _LOCK:
        removed = _HEARTBEATS.pop((channel, account_id), None)
    if removed is not None:
        _changed(channel, account_id)


def record_stop(channel: str, account_id: str, reason: str) -> None:
    """Remember why an adapter stopped for good, until the next start."""
    with _LOCK:
        _STOPS[(channel, account_id)] = reason
    _changed(channel, account_id)


def get_stop(channel: str, account_id: str) -> Optional[str]:
    """Return why the adapter stopped, or ``None`` if it has not."""
    with _LOCK:
        return _STOPS.get((channel, account_id))


def clear_stop(channel: str, account_id: str) -> None:
    """Forget stops and retries (called just before a new adapter thread)."""
    with _LOCK:
        stopped = _STOPS.pop((channel, account_id), None)
        retrying = _RETRIES.pop((channel, account_id), None)
    if stopped is not None or retrying is not None:
        _changed(channel, account_id)


def record_retry(channel: str, account_id: str, settles_in: float) -> None:
    """The adapter crashed and will reconnect; its thread is still alive.

    A blocking ``run`` gives no signal that it connected, so the mark
    expires ``settles_in`` seconds from now unless another crash renews it.
    """
    with _LOCK:
        _RETRIES[(channel, account_id)] = time.time() + settles_in
    _changed(channel, account_id)


def is_retrying(channel: str, account_id: str) -> bool:
    """Return whether the adapter crashed recently and is reconnecting."""
    with _LOCK:
        until = _RETRIES.get((channel, account_id))
    return until is not None and time.time() < until


def retry_remaining(channel: str, account_id: str) -> Optional[float]:
    """Seconds until the reconnecting mark runs out, or ``None`` if none."""
    with _LOCK:
        until = _RETRIES.get((channel, account_id))
    if until is None:
        return None
    left = until - time.time()
    return left if left > 0 else None

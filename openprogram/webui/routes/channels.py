"""Channel adapter health endpoints.

``GET /api/channels/{platform}/{account_id}/status`` — used by the
frontend status badge to decide whether the dot should be green
(adapter thread alive), yellow (configured but never heartbeated),
or red (heartbeat stale / adapter never started).

The actual heartbeat is stamped from ``worker.runner``'s watcher
thread, which polls ``thread.is_alive()`` on each adapter every 5s.
See ``openprogram/channels/_heartbeats.py`` for the registry.
"""
from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse


# A heartbeat older than this many seconds counts as "stale" (red).
# 30s gives the 5s watcher tick plenty of slack while still catching
# real adapter crashes / hangs reasonably quickly in the UI.
_STALE_AFTER_SEC = 30.0


def register(app: FastAPI) -> None:
    @app.get("/api/channels/{platform}/{account_id}/status")
    def channel_status(platform: str, account_id: str):
        from openprogram.channels._heartbeats import get_last_seen

        last = get_last_seen(platform, account_id)
        now = time.time()
        if last is None:
            # Never seen — either the adapter isn't enabled/configured,
            # or the worker hasn't gotten around to spawning its
            # thread yet. UI shows yellow ("connecting") for this.
            return JSONResponse(
                content={
                    "alive": False,
                    "state": "unknown",
                    "last_seen_at": None,
                    "age_seconds": None,
                }
            )
        age = now - last
        alive = age < _STALE_AFTER_SEC
        return JSONResponse(
            content={
                "alive": alive,
                "state": "alive" if alive else "stale",
                "last_seen_at": last,
                "age_seconds": age,
            }
        )

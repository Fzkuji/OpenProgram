"""Sleep scheduling — installs a daily background task in the worker.

The worker calls ``start_in_worker(...)`` at boot. We spawn a daemon
thread that sleeps until the next 03:00 local time, runs the sweep,
and loops. Lightweight; doesn't depend on a cron daemon.

The sweep reorganises topic files: splitting one that has grown to cover
several subjects, merging paragraphs that say the same thing, repairing
links. Writing only ever makes files longer, so without this a workspace
ends up as one enormous file per subject with its timeline cut to pieces
— the shape that makes ordering and counting questions unanswerable.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta


logger = logging.getLogger(__name__)


def get_provider():
    from . import get_provider as _resolve

    return _resolve()


def _seconds_until_next_3am() -> float:
    now = datetime.now()
    target = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def start_in_worker(
    *,
    model: str | None = None,             # defaults to the user's own
    daily_at: int = 3,                    # hour-of-day local
    initial_delay: float | None = None,   # override for tests
) -> threading.Thread | None:
    """Spawn the sleep scheduler thread. Returns the thread or None if disabled.

    ``model`` overrides what the reorganising pass runs on; left unset it
    uses the login and default model the user's own CLI already has, so
    background maintenance needs no separate credential.
    """
    if os.environ.get("OPENPROGRAM_NO_SLEEP", "").strip() in ("1", "true", "yes"):
        logger.info("memory sleep scheduler disabled by OPENPROGRAM_NO_SLEEP")
        return None

    def _loop() -> None:
        if initial_delay is not None:
            time.sleep(initial_delay)
        else:
            wait = _seconds_until_next_3am() if daily_at == 3 else _seconds_until(daily_at)
            time.sleep(wait)
        while True:
            try:
                report = get_provider().maintain(model=model)
                logger.info("memory sleep sweep done: %s", report)
            except Exception as e:  # noqa: BLE001
                logger.warning("memory sleep sweep failed: %s", e)
            time.sleep(_seconds_until(daily_at))

    t = threading.Thread(target=_loop, name="memory-sleep", daemon=True)
    t.start()
    return t


def _seconds_until(hour: int) -> float:
    now = datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()

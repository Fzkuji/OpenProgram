"""Sleep scheduling — installs a daily background task in the worker.

The worker calls ``start_in_worker(...)`` at boot. We spawn a daemon
thread that sleeps until the next 03:00 local time, runs the sweep,
and loops. Lightweight; doesn't depend on a cron daemon.

The LLM callable is supplied by the caller (worker) so this module
stays independent of any provider SDK. If no LLM is wired in, the
sweep still runs but ``deep`` becomes a no-op (which is OK — light
will keep collecting candidates and recovery happens once the LLM is
configured).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Callable

from .sleep import run_sweep

logger = logging.getLogger(__name__)


def _seconds_until_next_3am() -> float:
    now = datetime.now()
    target = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def start_in_worker(
    *,
    llm: Callable[[str, str], str] | None = None,
    daily_at: int = 3,                    # hour-of-day local
    initial_delay: float | None = None,   # override for tests
) -> threading.Thread | None:
    """Spawn the sleep scheduler thread. Returns the thread or None if disabled.

    ``llm`` is a callable ``(system_prompt, user_text) -> str`` that the
    worker constructs once (default agent's provider). ``None`` is OK —
    the sweep still cleans up but skips LLM phases.
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
                report = run_sweep(llm=llm)
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

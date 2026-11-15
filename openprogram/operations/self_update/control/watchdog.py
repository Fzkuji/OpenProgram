"""The worker's periodic look at self-update, on its own thread.

Notices a controller that stopped writing its heartbeat and starts it again,
so a dead controller is seen while the worker is up. Also runs held turns once
admission reopens, and starts a diagnosis the controller asked for.
"""
from __future__ import annotations

import logging
import threading

from ..store import SelfUpdateStore
from ..types import is_terminal
from ..versions import layout as paths
from . import switch
from .held_turns import drain

TICK_SECONDS = 10.0
_log = logging.getLogger(__name__)


def tick(store: SelfUpdateStore | None = None) -> None:
    target = store or SelfUpdateStore()
    if not target.root.exists():
        return
    switch.clear_finished_admission(target, paths.for_state(target.root.parent))
    record = target.load_active()
    if record is not None and switch.owns(record) and not is_terminal(record.state.phase):
        if switch.controller_stale(target, record):
            switch.launch(record.request.update_id, store=target)
    drain(store=target)
    from ..repair.diagnosis import dispatch_pending
    dispatch_pending()


def start() -> threading.Event:
    """Run :func:`tick` until the returned event is set."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(TICK_SECONDS):
            try:
                tick()
            except Exception:  # noqa: BLE001 - a bad pass must not end the watchdog.
                _log.warning("self-update watchdog pass failed", exc_info=True)

    threading.Thread(target=loop, daemon=True, name="self-update-watchdog").start()
    return stop


__all__ = ["start", "tick"]

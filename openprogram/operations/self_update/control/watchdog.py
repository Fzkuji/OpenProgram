"""The worker's look at self-update, on its own thread.

Notices a controller that ended before its update did and starts it again,
the moment it ends: the controller says which process it is
(``switch.CONTROLLER``) and the operating system announces that process's
exit (``process_exits``). Also runs held turns once
admission reopens, and starts a diagnosis the controller asked for.
"""
from __future__ import annotations

import logging
import threading
import time

from ..store import SelfUpdateStore
from ..types import is_held, is_terminal
from openprogram.host._platform import runtime_layout as paths
from . import switch
from .held_turns import drain

#: The first pass comes this long after the Worker starts.
FIRST_PASS_SECONDS = 10.0
#: Without file events, the watchdog looks this often.
POLL_SECONDS = 10.0
_log = logging.getLogger(__name__)


#: Every input of a pass is a self-update file, the controller's exit or a
#: launched controller's start deadline, so the watchdog waits for one of
#: them and looks on its own only this often.
RESYNC_SECONDS = 300.0
#: Topic signalled when a file under ``<state>/self-updates`` changes, and,
#: keyed by update, when the process controlling a live update ends.
SELF_UPDATE_TOPIC = "self_update"

#: When this Worker last launched each update's controller.
_launched: dict[str, float] = {}


def tick(store: SelfUpdateStore | None = None) -> float | None:
    """One pass; while an update this worker controls runs without a
    controller, when (``time.time()``) to launch one again."""
    from openprogram.host._platform.process_exits import watch_process

    target = store or SelfUpdateStore()
    if not target.root.exists():
        return None
    switch.clear_finished_admission(target, paths.for_state(target.root.parent))
    record = target.load_active()
    # A held update waits for its owner's restart with no controller.
    running = (record is not None and switch.owns(record) and not is_terminal(record.state.phase)
               and not is_held(record))
    due = None
    if running:
        update_id = record.request.update_id
        controller = switch.controller_process(target, record)
        if controller is not None:
            # Its exit, or the file it removes on letting go, wakes the next pass.
            watch_process(*controller, SELF_UPDATE_TOPIC, update_id)
        else:
            now = time.time()
            launched = _launched.get(update_id)
            if launched is not None and now < launched + switch.CONTROLLER_START_SECONDS:
                # Launched a moment ago: it says who it is when it starts.
                due = launched + switch.CONTROLLER_START_SECONDS
            else:
                switch.launch(update_id, store=target)
                _launched[update_id] = now
                due = now + switch.CONTROLLER_START_SECONDS
    drain(store=target)
    from ..repair.diagnosis import dispatch_pending
    dispatch_pending()
    return due


def watch_store(store: SelfUpdateStore | None = None) -> None:
    """Signal ``SELF_UPDATE_TOPIC`` when a self-update file changes. Every
    watcher of the store registers through here, so they share one
    registration."""
    from openprogram.host._platform import changes

    changes.watch((store or SelfUpdateStore()).root, SELF_UPDATE_TOPIC, recursive=True)


class _Stop(threading.Event):
    """Ends the watchdog, waking it if it waits for a change."""

    def __init__(self, listener) -> None:
        super().__init__()
        self._listener = listener

    def set(self) -> None:
        super().set()
        self._listener.wake()


def start() -> threading.Event:
    """Run :func:`tick` until the returned event is set."""
    from openprogram.host._platform import changes

    watch_store()
    listener = changes.listen(SELF_UPDATE_TOPIC)
    stop = _Stop(listener)

    def loop() -> None:
        due: float | None = time.time() + FIRST_PASS_SECONDS
        try:
            while not stop.is_set():
                changes.wait_for(listener, resync=RESYNC_SECONDS, poll=POLL_SECONDS, due=due)
                if stop.is_set():
                    break
                try:
                    due = tick()
                except Exception:  # noqa: BLE001 - a bad pass must not end the watchdog.
                    due = time.time() + POLL_SECONDS
                    _log.warning("self-update watchdog pass failed", exc_info=True)
        finally:
            listener.close()

    threading.Thread(target=loop, daemon=True, name="self-update-watchdog").start()
    return stop


__all__ = ["RESYNC_SECONDS", "SELF_UPDATE_TOPIC", "start", "tick", "watch_store"]

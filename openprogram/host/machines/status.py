"""Connection state of each machine: what the project picker, the sidebar and the composer show beside its icon.

A state is one of:

* ``connected`` — this computer, or the last check or command reached the machine;
* ``connecting`` — no answer yet since the machine was added, edited or switched on;
* ``disconnected`` — the machine is switched off;
* ``error`` — the last check or command could not reach it, or its kind is not loaded.

Asking for the states starts a check (``check.check_machine``) behind the
answer for every machine whose state is older than :data:`STALE_AFTER`, one
at a time per machine. Every file operation on a remote machine reports
what it saw too, so a machine that drops mid-conversation reads as an
error without waiting for the next check. Listeners hear every change.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable, Optional

from openprogram.host.machines.kinds import CONNECTIONS

_log = logging.getLogger(__name__)

CONNECTED = "connected"
CONNECTING = "connecting"
DISCONNECTED = "disconnected"
ERROR = "error"

#: Seconds a check's answer is trusted before a listing checks again.
STALE_AFTER = 60.0

_lock = threading.Lock()
# machine id -> (connection fingerprint, state, message, monotonic time)
_known: dict[str, tuple[str, str, str, float]] = {}
_checking: set[str] = set()
listeners: list[Callable[[], None]] = []


def _fingerprint(machine) -> str:
    """The machine's connections as configured: an edited target is another machine to check."""
    return json.dumps({name: (machine.connection(name).raw() if machine.connection(name) else None)
                       for name in CONNECTIONS}, sort_keys=True, default=str)


def _notify() -> None:
    for listener in list(listeners):
        try:
            listener()
        except Exception:
            _log.debug("machine status listener failed", exc_info=True)


def note(machine, ok: bool, message: str = "") -> None:
    """Record what a check or a command saw on ``machine``; listeners hear a changed state."""
    if machine is None or machine.is_local:
        return
    state = CONNECTED if ok else ERROR
    with _lock:
        before = _known.get(machine.id)
        _known[machine.id] = (_fingerprint(machine), state, message, time.monotonic())
    if before is None or before[1] != state:
        _notify()


def _check(machine) -> None:
    from openprogram.host.machines.check import check_machine
    try:
        ok, message = check_machine(machine)
    except Exception as exc:  # a check must never take the worker down
        ok, message = False, str(exc)
    finally:
        with _lock:
            _checking.discard(machine.id)
    note(machine, ok, message)


def state_of(machine, *, refresh: bool = True) -> dict:
    """``{"state", "message"}`` for one machine; a stale or unknown one is checked behind the answer."""
    if machine.is_local:
        return {"state": CONNECTED, "message": ""}
    if not machine.enabled:
        return {"state": DISCONNECTED, "message": ""}
    fingerprint = _fingerprint(machine)
    with _lock:
        known = _known.get(machine.id)
        if known is not None and known[0] != fingerprint:
            known = None
        stale = known is None or time.monotonic() - known[3] > STALE_AFTER
        start = refresh and stale and machine.id not in _checking
        if start:
            _checking.add(machine.id)
    if start:
        threading.Thread(target=_check, args=(machine,), name=f"machine-check-{machine.id}",
                         daemon=True).start()
    if known is None:
        return {"state": CONNECTING, "message": ""}
    return {"state": known[1], "message": known[2]}


def online(machine) -> bool:
    """The machine answered last time (this computer always does)."""
    return state_of(machine, refresh=False)["state"] == CONNECTED


def forget(machine_id: Optional[str] = None) -> None:
    """Drop what is known (tests; a removed machine)."""
    with _lock:
        if machine_id is None:
            _known.clear()
            _checking.clear()
        else:
            _known.pop(machine_id, None)


__all__ = ["CONNECTED", "CONNECTING", "DISCONNECTED", "ERROR", "STALE_AFTER",
           "forget", "listeners", "note", "online", "state_of"]

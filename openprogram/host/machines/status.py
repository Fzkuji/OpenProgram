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

What the model is told is steadier than what the icon shows. A check can
fail once on a busy network, so :func:`unreachable` names a machine only
after its checks have failed for :data:`OFFLINE_AFTER` seconds with no
success in between (two failed checks at least, that far apart); the first
success clears it. It reads only what checks already found and never waits
on the network.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable, Optional

_log = logging.getLogger(__name__)

CONNECTED = "connected"
CONNECTING = "connecting"
DISCONNECTED = "disconnected"
ERROR = "error"

#: Seconds a check's answer is trusted before a listing checks again.
STALE_AFTER = 60.0
#: Seconds checks must keep failing, with no success between, before a machine reads as unreachable.
OFFLINE_AFTER = 45.0

_lock = threading.Lock()
# machine id -> (connection fingerprint, state, message, monotonic time)
_known: dict[str, tuple[str, str, str, float]] = {}
# machine id -> monotonic time of the first failure since the last success
_failing_since: dict[str, float] = {}
_checking: set[str] = set()
listeners: list[Callable[[], None]] = []


def _fingerprint(machine) -> str:
    """The machine's connection as configured: an edited one is another machine to check."""
    return json.dumps(machine.connection.raw() if machine.connection else None, sort_keys=True, default=str)


def _notify() -> None:
    for listener in list(listeners):
        try:
            listener()
        except Exception:
            _log.debug("machine status listener failed", exc_info=True)


def _now() -> float:
    return time.monotonic()


def _offline(machine_id: str, known: tuple[str, str, str, float] | None) -> bool:
    """Under ``_lock``: the checks of ``machine_id`` have failed for ``OFFLINE_AFTER`` with no success."""
    if known is None or known[1] != ERROR:
        return False
    since = _failing_since.get(machine_id)
    return since is not None and known[3] - since >= OFFLINE_AFTER


def note(machine, ok: bool, message: str = "") -> None:
    """Record what a check or a command saw on ``machine``; listeners hear a changed state."""
    if machine is None or machine.is_local:
        return
    state = CONNECTED if ok else ERROR
    fingerprint = _fingerprint(machine)
    now = _now()
    with _lock:
        before = _known.get(machine.id)
        was_offline = _offline(machine.id, before)
        if ok or before is None or before[0] != fingerprint or before[1] != ERROR:
            _failing_since.pop(machine.id, None)
        if not ok:
            _failing_since.setdefault(machine.id, now)
        _known[machine.id] = (fingerprint, state, message, now)
        is_offline = _offline(machine.id, _known[machine.id])
    if before is None or before[1] != state or was_offline != is_offline:
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


def state_of(machine, *, refresh: bool = True, max_age: float = STALE_AFTER) -> dict:
    """``{"state", "message"}`` for one machine; one whose answer is older than ``max_age`` (or unknown) is
    checked behind the answer when ``refresh``."""
    if machine.is_local:
        return {"state": CONNECTED, "message": ""}
    if not machine.enabled:
        return {"state": DISCONNECTED, "message": ""}
    fingerprint = _fingerprint(machine)
    with _lock:
        known = _known.get(machine.id)
        if known is not None and known[0] != fingerprint:
            known = None
        stale = known is None or _now() - known[3] > max_age
        start = refresh and stale and machine.id not in _checking
        if start:
            _checking.add(machine.id)
    if start:
        threading.Thread(target=_check, args=(machine,), name=f"machine-check-{machine.id}",
                         daemon=True).start()
    if known is None:
        return {"state": CONNECTING, "message": ""}
    return {"state": known[1], "message": known[2]}


def unreachable(machine, *, refresh: bool = False) -> Optional[str]:
    """Why ``machine`` cannot be reached, once its checks have failed for ``OFFLINE_AFTER`` with no success
    in between; ``None`` while it answers, is unknown or has only just started failing.

    Never waits on the network. With ``refresh``, a machine that is failing and whose last check is stale is
    checked again behind the answer, so its return is noticed without anyone opening Settings.
    """
    if machine is None or machine.is_local or not machine.enabled:
        return None
    fingerprint = _fingerprint(machine)
    with _lock:
        known = _known.get(machine.id)
        if known is None or known[0] != fingerprint:
            return None
        failing = known[1] == ERROR
        offline = _offline(machine.id, known)
    if failing and refresh:
        state_of(machine)
    return (known[2] or "no answer") if offline else None


def online(machine) -> bool:
    """The machine answered last time (this computer always does)."""
    return state_of(machine, refresh=False)["state"] == CONNECTED


def forget(machine_id: Optional[str] = None) -> None:
    """Drop what is known (tests; a removed machine)."""
    with _lock:
        if machine_id is None:
            _known.clear()
            _checking.clear()
            _failing_since.clear()
        else:
            _known.pop(machine_id, None)
            _failing_since.pop(machine_id, None)


__all__ = ["CONNECTED", "CONNECTING", "DISCONNECTED", "ERROR", "OFFLINE_AFTER", "STALE_AFTER",
           "forget", "listeners", "note", "online", "state_of", "unreachable"]

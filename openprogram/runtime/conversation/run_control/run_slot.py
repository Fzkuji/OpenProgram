"""The conversation run slot, as a port the hosting server fills.

The selected branch of a conversation runs one turn at a time. (A turn on
a recorded branch that is not selected takes that branch's own slot in the
server and runs beside it; nothing the runtime starts on its own does.)
The web server owns the selected branch's slot
(its run reservation, the running task it shows and the guard every chat
entry point takes), so a turn the runtime starts on its own, such as a
reminder wake or a handed-over message, must take the same slot or it runs
on top of the owner's turn. (An always-on check-in runs beside the
conversation on a side branch and takes no slot.) The runtime cannot import the server; the
server registers itself here instead. Without a host the slot falls back
to the in-process turn registry.
"""
from __future__ import annotations

from typing import Any, Protocol


class RunSlotPort(Protocol):
    def reserve(self, session_id: str, owner: str, *,
                lock_wait: float | None = None) -> bool: ...

    def activate(self, session_id: str, owner: str, *, execution_id: str,
                 status_version: int, runner: Any) -> bool: ...

    def finish(self, session_id: str, owner: str, *, execution_id: str) -> None: ...

    def finishing(self, session_id: str, owner: str) -> None: ...

    def release(self, session_id: str, owner: str) -> None: ...


class RunSlotBusy(RuntimeError):
    """The conversation is already running a turn."""


_port: RunSlotPort | None = None


def register_run_slot(port: RunSlotPort | None) -> None:
    """Make ``port`` the run slot of this process (``None`` drops it)."""
    global _port
    _port = port


def reserve(session_id: str, owner: str, *, lock_wait: float | None = None) -> bool:
    """Take the conversation's run slot for ``owner``; ``False`` when a turn holds it.

    Taking it reads the conversation under its lock. ``lock_wait`` bounds
    that wait in seconds (``0``: do not wait at all; ``None``: the store's
    own wait). A lock not had in time raises ``SessionLockTimeout``: the
    slot may be free, so the caller retries or says the message was not
    delivered, never that a turn is running."""
    if _port is None:
        from .turns import is_turn_running
        return not is_turn_running(session_id)
    if lock_wait is None or not _takes_lock_wait(_port):
        return _port.reserve(session_id, owner)
    return _port.reserve(session_id, owner, lock_wait=lock_wait)


def _takes_lock_wait(port: Any) -> bool:
    import inspect
    try:
        return "lock_wait" in inspect.signature(port.reserve).parameters
    except (TypeError, ValueError):
        return False


def activate(session_id: str, owner: str, *, execution_id: str,
             status_version: int, runner: Any) -> bool:
    if _port is None:
        return True
    return _port.activate(session_id, owner, execution_id=execution_id,
                          status_version=status_version, runner=runner)


def finish(session_id: str, owner: str, *, execution_id: str) -> None:
    if _port is not None:
        _port.finish(session_id, owner, execution_id=execution_id)


def release(session_id: str, owner: str) -> None:
    if _port is not None:
        _port.release(session_id, owner)


def finishing(session_id: str, owner: str) -> None:
    """The turn ``owner`` is about to send its terminal frame.

    Called just before that frame goes out, so nobody can see "the turn is
    over" while the slot still answers "busy" without the slot knowing it
    is only finishing up: the next message on the branch waits the short
    finish-up out instead of being refused. Never raises."""
    mark = getattr(_port, "finishing", None)
    if mark is None:
        return
    try:
        mark(session_id, owner)
    except Exception:
        # A missed mark only brings back the refusal it prevents.
        import logging
        logging.getLogger(__name__).debug("finishing mark failed", exc_info=True)


__all__ = [
    "RunSlotBusy", "RunSlotPort", "activate", "finish", "finishing", "register_run_slot",
    "release", "reserve",
]

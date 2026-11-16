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

from typing import Any, Callable, Protocol


class RunSlotPort(Protocol):
    def reserve(self, session_id: str, owner: str, *,
                lock_wait: float | None = None, branch_id: str | None = None) -> bool: ...

    def adopt(self, session_id: str, owner: str, *, execution_id: str,
              status_version: int, branch_id: str | None, runner: Any) -> bool: ...

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


def reserve(session_id: str, owner: str, *, lock_wait: float | None = None,
            branch_id: str | None = None) -> bool:
    """Take the conversation's run slot for ``owner``; ``False`` when a turn holds it.

    ``branch_id`` names the branch the turn will run on: the slot taken is
    that branch's own when it is a recorded branch other than the selected
    one, the selected branch's otherwise.

    Taking it reads the conversation under its lock. ``lock_wait`` bounds
    that wait in seconds (``0``: do not wait at all; ``None``: the store's
    own wait). A lock not had in time raises ``SessionLockTimeout``: the
    slot may be free, so the caller retries or says the message was not
    delivered, never that a turn is running."""
    if _port is None:
        from .turns import is_turn_running
        return not is_turn_running(session_id)
    options = {"lock_wait": lock_wait, "branch_id": branch_id}
    taken = _parameters(_port.reserve)
    return _port.reserve(session_id, owner, **{
        name: value for name, value in options.items() if value is not None and name in taken})


def _parameters(method: Any) -> frozenset[str]:
    import inspect
    try:
        return frozenset(inspect.signature(method).parameters)
    except (TypeError, ValueError):
        return frozenset()


def hold_admitted(session_id: str, owner: str, *, execution_id: str, status_version: int,
                  branch_id: str | None, runner: Any, still_wanted: Callable[[], bool],
                  poll_s: float = 0.5) -> bool:
    """Take the run slot for a turn that is already admitted, waiting for it.

    A Goal's next turn is admitted from inside the turn before it, which
    still holds the branch's slot, and another turn may have taken the
    slot in the moment between the two. (A background-command notice does
    not wait for that moment: the Goal's next turn carries it, see
    ``process_notices.claim_for_turn``.)
    The admitted turn waits
    here, on its own thread, until its branch's slot is free, then holds it
    like a typed message does: every client is told it runs, a Stop names
    it, and no other turn starts on the branch beside it. The caller gives
    the slot back with :func:`finish`.

    ``False`` when ``still_wanted`` says the turn is no longer to run (it
    was stopped or superseded while it waited, or the Worker is stopping);
    nothing is held then. Without a host the turn runs as before.
    """
    adopt = getattr(_port, "adopt", None)
    if adopt is None:
        return still_wanted()
    import time
    from openprogram.state.store.session.files.session_lock import SessionLockTimeout
    while still_wanted():
        try:
            if adopt(session_id, owner, execution_id=execution_id, status_version=status_version,
                     branch_id=branch_id, runner=runner):
                return True
        except SessionLockTimeout:
            # The conversation's files are being written; the slot may be free.
            pass
        time.sleep(poll_s)
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
    "RunSlotBusy", "RunSlotPort", "activate", "finish", "finishing", "hold_admitted",
    "register_run_slot", "release", "reserve",
]

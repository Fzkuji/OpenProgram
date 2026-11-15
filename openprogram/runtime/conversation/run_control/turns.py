"""Turn lifecycle: open and retire a turn's token, cancel-event registration and turn queries."""
from __future__ import annotations

import threading

from openprogram.runtime.execution.run_control.tokens import (
    CancelToken,
    CancellationToken,
    _cancel_flags_lock,
    _current_tokens,
)
from openprogram.runtime.execution.run_control.binding import (
    _current_execution_id,
    _current_session_id,
    _current_token,
)
from openprogram.runtime.execution.run_control.owners import (
    register_execution_owner,
    retire_execution_owner,
)


# session_id -> exact Event whose owner is performing session-keyed cleanup.
# Registration fails closed while a lease exists; cleanup callbacks run outside
# this module's lock and release the lease in a finally block. Cleanup is
# session-keyed, so it gates the foreground slot only.
_cancel_cleanup_leases: dict[str, threading.Event] = {}


def begin_turn(
    session_id: str, turn_id: str | None = None,
) -> CancellationToken:
    """Open a fresh cancellation token for a turn and register it as current.

    Any token left registered for this session belongs to a turn that has
    already ended; it is retired here so a stop racing the handover cannot
    land on a dead turn. When ``turn_id`` is set it is the execution id
    and is indexed as such; the session foreground slot stays occupied
    so one-at-a-time chat admission still works.
    """
    token = CancelToken(session_id, turn_id)
    with _cancel_flags_lock:
        if session_id in _cancel_cleanup_leases:
            raise RuntimeError("session cancellation cleanup in progress")
        key = (session_id, None)
        stale = _current_tokens.get(key)
        _current_tokens[key] = token
        if turn_id:
            _current_tokens[(session_id, turn_id)] = token
    if stale is not None and stale is not token:
        stale.retire()
        if stale.execution_id:
            retire_execution_owner(stale.execution_id)
    if turn_id:
        register_execution_owner(turn_id, session_id, token=token)
    return token


def end_turn(session_id: str, token: CancelToken | None = None) -> None:
    """Retire the turn's token and deregister it.

    Passing the token makes this safe against a turn that already handed
    the session over to a successor: only the matching registration is
    removed, never a newer turn's.
    """
    with _cancel_flags_lock:
        key = (session_id, None)
        current = _current_tokens.get(key)
        doomed = token if token is not None else current
        if token is None or current is token:
            _current_tokens.pop(key, None)
        if doomed is not None:
            for stored_key, stored in list(_current_tokens.items()):
                if stored is doomed:
                    _current_tokens.pop(stored_key, None)
    if doomed is not None:
        doomed.retire()
        if doomed.execution_id:
            retire_execution_owner(doomed.execution_id)


def register_cancel_event(
    session_id: str, ev: threading.Event, *, execution_id: str | None = None,
) -> None:
    """Adopt a caller-owned Event as the session's current turn token.

    Kept for call sites (chat turns, job runner) that create their own
    Event and hand it to the dispatcher. The Event becomes the token's
    Event, so tripping either one is visible through both.
    """
    token = CancellationToken(session_id, execution_id)
    token._event = ev
    with _cancel_flags_lock:
        if execution_id is None and session_id in _cancel_cleanup_leases:
            raise RuntimeError("session cancellation cleanup in progress")
        key = (session_id, execution_id)
        stale = _current_tokens.get(key)
        _current_tokens[key] = token
    if stale is not None and stale._event is not ev:
        stale.retire()
        if stale.execution_id:
            retire_execution_owner(stale.execution_id)
    if execution_id:
        register_execution_owner(execution_id, session_id, token=token)


def claim_cancel_event(
    session_id: str,
    ev: threading.Event,
    *,
    execution_id: str | None = None,
    foreground: bool | None = None,
) -> bool:
    """Register ``ev`` only when this slot has no owner or cleanup lease.

    The foreground slot (``execution_id`` None, or ``foreground=True``)
    additionally fails closed while a session-keyed cleanup lease is held.
    Jobs pass an execution id and leave the foreground slot alone.
    """
    occupy_fg = (execution_id is None) if foreground is None else foreground
    token = CancellationToken(session_id, execution_id)
    token._event = ev
    with _cancel_flags_lock:
        if occupy_fg and session_id in _cancel_cleanup_leases:
            return False
        key = (session_id, execution_id)
        if key in _current_tokens:
            return False
        if occupy_fg and execution_id is not None:
            fg = (session_id, None)
            if fg in _current_tokens:
                return False
            _current_tokens[fg] = token
        _current_tokens[key] = token
    if execution_id:
        register_execution_owner(execution_id, session_id, token=token)
    return True


def acquire_cancel_cleanup(session_id: str, ev: threading.Event) -> bool:
    """Atomically lease session-keyed cleanup to the exact current event.

    A successful lease prevents ``begin_turn`` and ``register_cancel_event``
    from handing the session to a successor until ``release_cancel_cleanup``.
    The caller must release in ``finally`` after all blocking cleanup work.
    """
    with _cancel_flags_lock:
        current = _current_tokens.get((session_id, None))
        if (
            current is None
            or current._event is not ev
            or session_id in _cancel_cleanup_leases
        ):
            return False
        _cancel_cleanup_leases[session_id] = ev
        return True


def release_cancel_cleanup(session_id: str, ev: threading.Event) -> None:
    """Release the cleanup lease only when ``ev`` still owns it."""
    with _cancel_flags_lock:
        if _cancel_cleanup_leases.get(session_id) is ev:
            _cancel_cleanup_leases.pop(session_id, None)


def unregister_cancel_event(
    session_id: str,
    ev: threading.Event | None = None,
    *,
    execution_id: str | None = None,
) -> None:
    """Retire the registration made with ``ev`` (see register_cancel_event).

    Callers that registered an Event MUST pass it back here: without it
    this pops whatever token is CURRENT, including a newer turn's — the
    concrete failure was ``/task --async`` finishing after the user had
    already started a chat turn, popping the chat turn's token and
    leaving its Stop button dead. With ``ev`` only the matching
    registration is removed; a mismatch means a newer turn already
    replaced (and retired) ours via register_cancel_event, so there is
    nothing left to do. ``ev=None`` keeps the unconditional force-clear
    for internal cleanup callers that explicitly want to tear down whatever
    is current.
    """
    if ev is None:
        end_turn(session_id)
        return
    doomed: CancellationToken | None = None
    with _cancel_flags_lock:
        key = (session_id, execution_id)
        current = _current_tokens.get(key)
        if current is None or current._event is not ev:
            # Fall back to matching the Event anywhere in this session so
            # a foreground alias of the same token is retired with it.
            for stored_key, stored in list(_current_tokens.items()):
                if stored_key[0] == session_id and stored._event is ev:
                    current = stored
                    break
            else:
                return
        doomed = current
        for stored_key, stored in list(_current_tokens.items()):
            if stored is doomed:
                _current_tokens.pop(stored_key, None)
    doomed.retire()
    if doomed.execution_id:
        retire_execution_owner(doomed.execution_id)


def is_turn_running(session_id: str) -> bool:
    """True while a turn is in flight on this session.

    The authoritative in-process busy check: every turn entry point that
    can run concurrently (webui chat, job runner workers) registers its
    cancel token in ``_current_tokens`` and unregisters it in a finally
    block, so presence here means a turn is executing right now.
    send_message uses this to decide direct delivery vs. inbox queueing.
    """
    with _cancel_flags_lock:
        return any(key[0] == session_id for key in _current_tokens)


def is_cancelled(
    session_id: str, *, execution_id: str | None = None,
) -> bool:
    """True while the current turn is cancelled. False once it has ended.

    A background task checking its own session resolves to its own slot,
    so a stop aimed at the foreground turn never reads as cancelled here.
    """
    if execution_id is None and _current_session_id.get(None) == session_id:
        execution_id = _current_execution_id.get(None)
    with _cancel_flags_lock:
        token = _current_tokens.get((session_id, execution_id))
    if token is not None:
        return token.is_cancelled()
    bound = _current_token.get(None)
    if bound is not None and bound.session_id == session_id:
        if execution_id is None or bound.execution_id == execution_id:
            return bound.is_cancelled()
    return False


def cancelled_execution_ids(session_id: str) -> set[str]:
    """The executions of this session a Stop has reached and that have not ended."""
    with _cancel_flags_lock:
        return {
            execution_id for (owner, execution_id), token in _current_tokens.items()
            if owner == session_id and execution_id and token.is_cancelled()
        }


def clear_cancel(session_id: str) -> None:
    """Retire the session's token — the turn is over, cancelled or not."""
    end_turn(session_id)

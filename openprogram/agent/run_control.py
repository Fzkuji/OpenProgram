"""
Run control for turn execution: pause / cancel / session binding /
active-runtime registry.

This is turn-execution state, not a UI concern — the web UI, the task
runner, channels, process runners and long-running tools all steer the
same machine. Importing this module claims the core's host-integration
seams (``set_cancellation_check`` / ``set_session_id_provider``), which
is what makes the exec loop cancellable; ``agentic_programming`` itself
never imports this layer and keeps its headless defaults when nobody
does.
"""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from typing import Any

from openprogram.agentic_programming.function import (
    CancelledError,
    add_pre_invocation_hook,
    set_cancellation_check,
    set_session_id_provider,
)


# ---------------------------------------------------------------------------
# Pause/resume — cooperative: only blocks at `node_created` event hooks.
# ---------------------------------------------------------------------------

_pause_event = threading.Event()
_pause_event.set()  # starts un-paused


def pause_execution() -> None:
    """Block agentic functions from proceeding (cooperative)."""
    _pause_event.clear()


def resume_execution() -> None:
    """Resume blocked agentic functions."""
    _pause_event.set()


def wait_if_paused() -> None:
    """Called by the event hook; blocks until resumed."""
    _pause_event.wait()


# ---------------------------------------------------------------------------
# Turn cancellation tokens — one per turn, never per session.
#
# A turn opens a fresh CancelToken; the LLM call, tool execution and every
# sub-task check that one object. Stopping trips the token of the turn that
# is running now. When the turn ends the token is retired, so a stop that
# arrives late cannot reach into the next turn. Nothing has to be reset on
# cleanup — the next turn simply gets a different object.
#
# The public names below (mark_cancelled / is_cancelled / clear_cancel)
# keep their meaning for callers and the WS protocol; they now resolve to
# the session's current token instead of a sticky boolean.
# ---------------------------------------------------------------------------


class CancelToken:
    """Cancellation signal scoped to exactly one turn.

    Wraps a ``threading.Event`` so blocked worker threads can wait on it,
    and carries a ``retired`` flag: once the turn ends, ``cancel()`` is a
    no-op, which is what keeps a late stop from leaking into the next turn.
    """

    __slots__ = ("_event", "_retired", "_lock", "session_id", "turn_id")

    def __init__(self, session_id: str, turn_id: str | None = None) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self._event = threading.Event()
        self._retired = False
        self._lock = threading.Lock()

    @property
    def event(self) -> threading.Event:
        """The underlying Event, for code that must block until cancelled."""
        return self._event

    def cancel(self) -> bool:
        """Trip this token. Returns False if the turn already ended."""
        with self._lock:
            if self._retired:
                return False
            self._event.set()
        return True

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def retire(self) -> None:
        """End this token's life. Later ``cancel()`` calls do nothing."""
        with self._lock:
            self._retired = True

    @property
    def retired(self) -> bool:
        with self._lock:
            return self._retired


_cancel_flags_lock = threading.Lock()

# (session_id, execution_id) → the token owned by that execution. A None
# execution_id is the foreground slot shared by Web, MCP and ACP turns, which
# admit one at a time; background tasks sharing a session bind their task id
# so a stop aimed at one never reaches a sibling.
# Absent when no turn is in flight, which is why a stop between turns is a
# no-op rather than a flag that poisons whatever runs next.
_current_tokens: dict[tuple[str, str | None], CancelToken] = {}

# session_id -> exact Event whose owner is performing session-keyed cleanup.
# Registration fails closed while a lease exists; cleanup callbacks run outside
# this module's lock and release the lease in a finally block. Cleanup is
# session-keyed, so it gates the foreground slot only.
_cancel_cleanup_leases: dict[str, threading.Event] = {}

# Per-thread session_id so the cancel hook knows whose token to check.
# Set by `_execute_in_context` at entry. ContextVars do not propagate across
# threading.Thread starts, so the value is always set from inside the worker.
_current_session_id: ContextVar = ContextVar("_current_session_id", default=None)

# Background tasks sharing a session bind their task id here. Foreground
# turns leave it as None and retain the historical single-turn semantics.
_current_execution_id: ContextVar = ContextVar(
    "_current_execution_id", default=None,
)

# The active token for the current worker context. Set alongside the
# session id so nested agentic frames check the same object even when a
# turn for another session is running elsewhere in the process.
_current_token: ContextVar = ContextVar("_current_token", default=None)


def begin_turn(session_id: str, turn_id: str | None = None) -> CancelToken:
    """Open a fresh cancellation token for a turn and register it as current.

    Any token left registered for this session belongs to a turn that has
    already ended; it is retired here so a stop racing the handover cannot
    land on a dead turn.
    """
    token = CancelToken(session_id, turn_id)
    with _cancel_flags_lock:
        if session_id in _cancel_cleanup_leases:
            raise RuntimeError("session cancellation cleanup in progress")
        key = (session_id, None)
        stale = _current_tokens.get(key)
        _current_tokens[key] = token
    if stale is not None:
        stale.retire()
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
        if token is None or current is token:
            _current_tokens.pop(key, None)
    doomed = token if token is not None else current
    if doomed is not None:
        doomed.retire()


def current_token(
    session_id: str, *, execution_id: str | None = None,
) -> CancelToken | None:
    """The token of the turn running on this session, or None between turns."""
    with _cancel_flags_lock:
        return _current_tokens.get((session_id, execution_id))


def register_cancel_event(
    session_id: str, ev: threading.Event, *, execution_id: str | None = None,
) -> None:
    """Adopt a caller-owned Event as the session's current turn token.

    Kept for call sites (chat turns, task runner) that create their own
    Event and hand it to the dispatcher. The Event becomes the token's
    Event, so tripping either one is visible through both.
    """
    token = CancelToken(session_id)
    token._event = ev
    with _cancel_flags_lock:
        if execution_id is None and session_id in _cancel_cleanup_leases:
            raise RuntimeError("session cancellation cleanup in progress")
        key = (session_id, execution_id)
        stale = _current_tokens.get(key)
        _current_tokens[key] = token
    if stale is not None and stale._event is not ev:
        stale.retire()


def claim_cancel_event(
    session_id: str, ev: threading.Event, *, execution_id: str | None = None,
) -> bool:
    """Register ``ev`` only when this slot has no owner or cleanup lease.

    The foreground slot (``execution_id`` None) additionally fails closed
    while a session-keyed cleanup lease is held.
    """
    token = CancelToken(session_id)
    token._event = ev
    with _cancel_flags_lock:
        if execution_id is None and session_id in _cancel_cleanup_leases:
            return False
        key = (session_id, execution_id)
        if key in _current_tokens:
            return False
        _current_tokens[key] = token
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
    for callers that explicitly want to tear down whatever is current
    (the /api/stop handler).
    """
    if ev is None:
        end_turn(session_id)
        return
    key = (session_id, execution_id)
    with _cancel_flags_lock:
        current = _current_tokens.get(key)
        if current is None or current._event is not ev:
            return
        _current_tokens.pop(key, None)
    current.retire()


def is_turn_running(session_id: str) -> bool:
    """True while a turn is in flight on this session.

    The authoritative in-process busy check: every turn entry point that
    can run concurrently (webui chat, task runner workers) registers its
    cancel token in ``_current_tokens`` and unregisters it in a finally
    block, so presence here means a turn is executing right now.
    send_message uses this to decide direct delivery vs. inbox queueing.
    """
    # ponytail: channel-worker turns don't register a token, so they are
    # invisible here; register one there if channel sessions ever need
    # busy-queueing.
    with _cancel_flags_lock:
        return any(key[0] == session_id for key in _current_tokens)


def mark_cancelled(session_id: str, *, execution_id: str | None = None) -> None:
    """Stop the turn running on this session. No-op between turns."""
    with _cancel_flags_lock:
        token = _current_tokens.get((session_id, execution_id))
    if token is not None:
        token.cancel()


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
    return token.is_cancelled() if token is not None else False


def clear_cancel(session_id: str) -> None:
    """Retire the session's token — the turn is over, cancelled or not."""
    end_turn(session_id)


def set_current_execution_id(execution_id: str | None):
    """Bind task-keyed cancellation/runtime ownership to this context."""
    return _current_execution_id.set(execution_id)


def reset_current_execution_id(token) -> None:
    try:
        _current_execution_id.reset(token)
    except Exception:
        pass


def get_current_execution_id() -> str | None:
    return _current_execution_id.get(None)


def set_current_session_id(session_id: str):
    """Bind session_id to the current worker context. Call at the top of
    _execute_in_context. Returns the token for later reset()."""
    return _current_session_id.set(session_id)


def get_current_session_id() -> str | None:
    """The webui session bound to the current worker context, or None when
    not inside a dispatcher-driven turn (CLI / tests / headless)."""
    return _current_session_id.get()


def reset_current_session_id(token) -> None:
    """Reset the session_id ContextVar using a token from set_current_session_id."""
    try:
        _current_session_id.reset(token)
    except Exception:
        pass


def _active_token() -> "CancelToken | None":
    """The token this frame must check.

    The context-bound token wins: it is the one the enclosing turn opened,
    so a nested frame keeps checking its own turn even if the session has
    since moved on. Falling back to the session registry covers workers
    that bind only the session id.
    """
    token = _current_token.get(None)
    if token is not None:
        return token
    cid = _current_session_id.get(None)
    eid = _current_execution_id.get(None)
    return current_token(cid, execution_id=eid) if cid else None


def _cancel_hook() -> None:
    """Pre-invocation hook: raise CancelledError if this turn was stopped.

    Registered with agentic_function's hook list, so every @agentic_function
    entry (and every Runtime.exec call) aborts once the turn is cancelled.
    """
    token = _active_token()
    if token is not None and token.is_cancelled():
        raise CancelledError(f"Execution stopped by user (conv={token.session_id})")


def check_cancelled() -> None:
    """Public cancel checkpoint usable from inside long-running tool code.

    Same semantics as ``_cancel_hook`` but exported so non-@agentic_function
    code paths (e.g. GUI-Agent observe / OCR / detector pipelines) can yield
    to the stop signal between heavy synchronous stages without waiting for
    the next @agentic_function boundary. Safe no-op when no turn is bound
    (e.g. CLI / unit test contexts).
    """
    _cancel_hook()


# Register the cancel hook once at import time.
add_pre_invocation_hook(_cancel_hook)

# Claim the core's host-integration seams for the webui. Importing this module
# is what makes the exec loop cancellable and gives Runtime.ask a session to
# route to; without it the core keeps its headless defaults.
set_cancellation_check(_cancel_hook)
set_session_id_provider(get_current_session_id)


# ---------------------------------------------------------------------------
# Active exec runtimes — keep track so /api/stop can kill the CLI subprocess.
# ---------------------------------------------------------------------------

_active_exec_runtimes: dict[tuple[str, str | None], Any] = {}
_active_exec_runtimes_lock = threading.Lock()


def register_active_runtime(
    session_id: str, rt: Any, *, execution_id: str | None = None,
) -> None:
    with _active_exec_runtimes_lock:
        _active_exec_runtimes[(session_id, execution_id)] = rt


def unregister_active_runtime(
    session_id: str, *, execution_id: str | None = None,
) -> None:
    with _active_exec_runtimes_lock:
        _active_exec_runtimes.pop((session_id, execution_id), None)


def has_active_runtime(session_id: str) -> bool:
    """True iff a foreground runtime is registered for this session.

    Used as a zombie check against ``_running_tasks``: an entry there
    without a paired live runtime (process died, cleanup missed) is
    stale and should be treated as no-op.
    """
    with _active_exec_runtimes_lock:
        return (session_id, None) in _active_exec_runtimes


def kill_active_runtime(
    session_id: str, *, execution_id: str | None = None,
) -> None:
    """Terminate the subprocess of the active exec runtime, if any."""
    with _active_exec_runtimes_lock:
        rt = _active_exec_runtimes.get((session_id, execution_id))
    if rt is None:
        return
    proc = getattr(rt, "_proc", None)
    if proc is None:
        return
    try:
        if proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    except Exception:
        pass

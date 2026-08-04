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

# session_id → the token of the turn currently running on that session.
# Absent when no turn is in flight, which is why a stop between turns is a
# no-op rather than a flag that poisons whatever runs next.
_current_tokens: dict[str, CancelToken] = {}

# Per-thread session_id so the cancel hook knows whose token to check.
# Set by `_execute_in_context` at entry. ContextVars do not propagate across
# threading.Thread starts, so the value is always set from inside the worker.
_current_session_id: ContextVar = ContextVar("_current_session_id", default=None)

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
        stale = _current_tokens.get(session_id)
        _current_tokens[session_id] = token
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
        current = _current_tokens.get(session_id)
        if token is None or current is token:
            _current_tokens.pop(session_id, None)
    doomed = token if token is not None else current
    if doomed is not None:
        doomed.retire()


def current_token(session_id: str) -> CancelToken | None:
    """The token of the turn running on this session, or None between turns."""
    with _cancel_flags_lock:
        return _current_tokens.get(session_id)


def register_cancel_event(session_id: str, ev: threading.Event) -> None:
    """Adopt a caller-owned Event as the session's current turn token.

    Kept for call sites (chat turns, task runner) that create their own
    Event and hand it to the dispatcher. The Event becomes the token's
    Event, so tripping either one is visible through both.
    """
    token = CancelToken(session_id)
    token._event = ev
    with _cancel_flags_lock:
        stale = _current_tokens.get(session_id)
        _current_tokens[session_id] = token
    if stale is not None and stale._event is not ev:
        stale.retire()


def unregister_cancel_event(session_id: str) -> None:
    end_turn(session_id)


def mark_cancelled(session_id: str) -> None:
    """Stop the turn running on this session. No-op between turns."""
    with _cancel_flags_lock:
        token = _current_tokens.get(session_id)
    if token is not None:
        token.cancel()


def is_cancelled(session_id: str) -> bool:
    """True while the current turn is cancelled. False once it has ended."""
    with _cancel_flags_lock:
        token = _current_tokens.get(session_id)
    return token.is_cancelled() if token is not None else False


def clear_cancel(session_id: str) -> None:
    """Retire the session's token — the turn is over, cancelled or not."""
    end_turn(session_id)


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
    return current_token(cid) if cid else None


def _cancel_hook() -> None:
    """Pre-invocation hook: raise CancelledError if this turn was stopped.

    Registered with agentic_function's hook list, so every @agentic_function
    entry (and every Runtime.exec call) aborts once the turn is cancelled.
    """
    token = _active_token()
    if token is not None and token.is_cancelled():
        raise CancelledError(
            f"Execution stopped by user (conv={token.session_id})")


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

_active_exec_runtimes: dict[str, Any] = {}
_active_exec_runtimes_lock = threading.Lock()


def register_active_runtime(session_id: str, rt: Any) -> None:
    with _active_exec_runtimes_lock:
        _active_exec_runtimes[session_id] = rt


def unregister_active_runtime(session_id: str) -> None:
    with _active_exec_runtimes_lock:
        _active_exec_runtimes.pop(session_id, None)


def has_active_runtime(session_id: str) -> bool:
    """True iff a runtime is currently registered for this session.

    Used as a zombie check against ``_running_tasks``: an entry there
    without a paired live runtime (process died, cleanup missed) is
    stale and should be treated as no-op.
    """
    with _active_exec_runtimes_lock:
        return session_id in _active_exec_runtimes


def kill_active_runtime(session_id: str) -> None:
    """Terminate the subprocess of the active exec runtime, if any."""
    with _active_exec_runtimes_lock:
        rt = _active_exec_runtimes.get(session_id)
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



"""
Pause / resume / cancel / kill-runtime primitives used by the web UI.

These were originally inline in server.py but live here so server.py stays
focused on FastAPI routes and the execution dispatcher.
"""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from typing import Any

from openprogram.agentic_programming.function import (
    CancelledError,
    add_pre_invocation_hook,
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
# Cancel flags — per-conversation. Set by /api/stop, checked by the exception
# path in _execute_in_context and by the pre-invocation hook below.
# ---------------------------------------------------------------------------

_cancel_flags: dict[str, bool] = {}
_cancel_flags_lock = threading.Lock()

# Per-thread session_id so the cancel hook knows whose flag to check.
# Set by `_execute_in_context` at entry. ContextVars do not propagate across
# threading.Thread starts, so the value is always set from inside the worker.
_current_session_id: ContextVar = ContextVar("_current_session_id", default=None)


_cancel_events: dict[str, threading.Event] = {}


def register_cancel_event(session_id: str, ev: threading.Event) -> None:
    with _cancel_flags_lock:
        _cancel_events[session_id] = ev


def unregister_cancel_event(session_id: str) -> None:
    with _cancel_flags_lock:
        _cancel_events.pop(session_id, None)


def mark_cancelled(session_id: str) -> None:
    with _cancel_flags_lock:
        _cancel_flags[session_id] = True
        ev = _cancel_events.get(session_id)
    if ev is not None:
        try:
            ev.set()
        except Exception:
            pass


def is_cancelled(session_id: str) -> bool:
    with _cancel_flags_lock:
        return _cancel_flags.get(session_id, False)


def clear_cancel(session_id: str) -> None:
    with _cancel_flags_lock:
        _cancel_flags.pop(session_id, None)


def set_current_session_id(session_id: str):
    """Bind session_id to the current worker context. Call at the top of
    _execute_in_context. Returns the token for later reset()."""
    return _current_session_id.set(session_id)


def reset_current_session_id(token) -> None:
    """Reset the session_id ContextVar using a token from set_current_session_id."""
    try:
        _current_session_id.reset(token)
    except Exception:
        pass


def _cancel_hook() -> None:
    """Pre-invocation hook: raise CancelledError if the current conv is stopped.

    Registered with agentic_function's hook list, so every @agentic_function
    entry (and every Runtime.exec call) aborts once /api/stop fires.
    """
    cid = _current_session_id.get(None)
    if cid and is_cancelled(cid):
        raise CancelledError(f"Execution stopped by user (conv={cid})")


def check_cancelled() -> None:
    """Public cancel checkpoint usable from inside long-running tool code.

    Same semantics as ``_cancel_hook`` but exported so non-@agentic_function
    code paths (e.g. GUI-Agent observe / OCR / detector pipelines) can yield
    to the stop flag between heavy synchronous stages without waiting for
    the next @agentic_function boundary. Safe no-op when no session is bound
    (e.g. CLI / unit test contexts).
    """
    cid = _current_session_id.get(None)
    if cid and is_cancelled(cid):
        raise CancelledError(f"Execution stopped by user (conv={cid})")


# Register the cancel hook once at import time.
add_pre_invocation_hook(_cancel_hook)


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



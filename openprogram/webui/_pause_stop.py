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


def mark_cancelled(session_id: str) -> None:
    with _cancel_flags_lock:
        _cancel_flags[session_id] = True


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


# ---------------------------------------------------------------------------
# Mark a cancelled tree's running nodes as errored (used on cancellation).
# ---------------------------------------------------------------------------

def mark_context_cancelled(ctx) -> None:
    """Recursively mark every running node in the tree as cancelled."""
    if ctx is None:
        return
    try:
        if getattr(ctx, "status", "") == "running":
            ctx.status = "error"
            if not getattr(ctx, "error", ""):
                ctx.error = "Cancelled by user"
            if getattr(ctx, "end_time", 0) == 0:
                ctx.end_time = time.time()
        for child in getattr(ctx, "children", []) or []:
            mark_context_cancelled(child)
    except Exception:
        pass

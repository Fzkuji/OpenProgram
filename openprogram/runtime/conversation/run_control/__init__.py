"""
Run control for turn execution: cancel / session binding /
active-runtime registry.

Execution binding and cancellation (tokens, the current session and
execution, live owners, exact cancel, child admission) live in
``openprogram.runtime.execution.run_control`` and are imported from there.
This package holds the turn side: turn tokens and cancel events
(``turns``), active exec runtimes (``active_runtimes``), the pre-approved
wait binding (``session_binding``) and the worker-stop and cancel hooks.

This is turn-execution state, not a UI concern — the web UI, the job
runner, channels, process runners and long-running tools all steer the
same machine. Importing this module claims the core's host-integration
seams (``set_cancellation_check`` / ``set_session_id_provider``), which
is what makes the exec loop cancellable; the call core itself
never imports this layer and keeps its headless defaults when nobody
does.
"""
from __future__ import annotations

import threading

from openprogram.runtime.agent import call_state as _call_state
from openprogram.host import cancellation as _cancellation
from openprogram.runtime.execution.run_control import binding as _binding
from openprogram.runtime.execution import run_control as _execution_run_control

from openprogram.runtime.conversation.run_control.session_binding import (
    get_preapproved_wait_id,
    reset_preapproved_wait_id,
    set_preapproved_wait_id,
    clear_turn_context,
)
from openprogram.runtime.conversation.run_control.active_runtimes import (
    register_active_runtime,
    unregister_active_runtime,
    has_active_runtime,
    kill_active_runtime,
)
from openprogram.runtime.conversation.run_control.turns import (
    begin_turn,
    end_turn,
    register_cancel_event,
    claim_cancel_event,
    acquire_cancel_cleanup,
    release_cancel_cleanup,
    unregister_cancel_event,
    is_turn_running,
    is_cancelled,
    clear_cancel,
)


_worker_stopping = threading.Event()


def begin_worker_shutdown() -> None:
    """Stop subsequent invocations without recording a user cancellation."""
    _worker_stopping.set()


def is_worker_stopping() -> bool:
    return _worker_stopping.is_set()


def raise_if_worker_stopping() -> None:
    if is_worker_stopping():
        from openprogram.models.providers.utils.errors import ExecInterrupt
        raise ExecInterrupt("worker_stopping")


def _active_token() -> "_execution_run_control.CancellationToken | None":
    """The token this frame must check.

    The context-bound token wins: it is the one the enclosing turn opened,
    so a nested frame keeps checking its own turn even if the session has
    since moved on. Falling back to the session registry covers workers
    that bind only the session id.
    """
    token = _binding._current_token.get(None)
    if token is not None:
        return token
    cid = _binding._current_session_id.get(None)
    eid = _binding._current_execution_id.get(None)
    return _execution_run_control.current_token(cid, execution_id=eid) if cid else None


def _cancel_hook() -> None:
    """Pre-invocation hook: raise CancelledError if this turn was stopped.

    Registered with Agent method execution hook list, so every Agent method
    entry (and every Runtime.exec call) aborts once the turn is cancelled.
    """
    if _worker_stopping.is_set():
        from openprogram.models.providers.utils.errors import ExecInterrupt
        raise ExecInterrupt("worker_stopping")
    token = _active_token()
    if token is not None and token.is_cancelled():
        # The reason, not the conversation id: this text can reach the model.
        from openprogram.runtime.execution.run_control.stop_reasons import current_stop_reason
        raise _cancellation.CancelledError(f"Execution stopped ({current_stop_reason()})")


def check_cancelled() -> None:
    """Public cancel checkpoint usable from inside long-running tool code.

    Same semantics as ``_cancel_hook`` but exported so ordinary function
    code paths (e.g. GUI-Agent observe / OCR / detector pipelines) can yield
    to the stop signal between heavy synchronous stages without waiting for
    the next Agent method boundary. Safe no-op when no turn is bound
    (e.g. CLI / unit test contexts).
    """
    _cancel_hook()


# Register the cancel hook once at import time.
_call_state.add_pre_invocation_hook(_cancel_hook)

# Claim the core's host-integration seams for the webui. Importing this module
# is what makes the exec loop cancellable and gives Runtime.ask a session to
# route to; without it the core keeps its headless defaults.
_cancellation.set_cancellation_check(_cancel_hook)
_call_state.set_session_id_provider(_execution_run_control.get_current_session_id)


__all__ = [
    "acquire_cancel_cleanup",
    "begin_turn",
    "begin_worker_shutdown",
    "check_cancelled",
    "claim_cancel_event",
    "clear_cancel",
    "clear_turn_context",
    "end_turn",
    "get_preapproved_wait_id",
    "has_active_runtime",
    "is_cancelled",
    "is_turn_running",
    "is_worker_stopping",
    "kill_active_runtime",
    "raise_if_worker_stopping",
    "register_active_runtime",
    "register_cancel_event",
    "release_cancel_cleanup",
    "reset_preapproved_wait_id",
    "set_preapproved_wait_id",
    "unregister_active_runtime",
    "unregister_cancel_event",
]

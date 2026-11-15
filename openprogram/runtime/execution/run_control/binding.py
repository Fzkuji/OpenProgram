"""The session, execution and cancellation token bound to the current context."""
from __future__ import annotations

from contextvars import ContextVar
import logging

_log = logging.getLogger(__name__)


# Per-thread session_id so the cancel hook knows whose token to check.
# Set by the production driver (`production_driver/execution.py`) and the
# process runner at turn entry. ContextVars do not propagate across
# threading.Thread starts, so the value is always set from inside the worker.
_current_session_id: ContextVar = ContextVar("_current_session_id", default=None)

# Background jobs sharing a session bind their job id here. Foreground
# turns leave it as None and retain the historical single-turn semantics.
_current_execution_id: ContextVar = ContextVar(
    "_current_execution_id", default=None,
)

# The active token for the current worker context. Set alongside the
# session id so nested agentic frames check the same object even when a
# turn for another session is running elsewhere in the process.
_current_token: ContextVar = ContextVar("_current_token", default=None)


def set_current_execution_id(execution_id: str | None):
    """Bind task-keyed cancellation/runtime ownership to this context."""
    return _current_execution_id.set(execution_id)


def reset_current_execution_id(token) -> None:
    try:
        _current_execution_id.reset(token)
    except (ValueError, RuntimeError):
        # ContextVar.reset raises ValueError for a token from another
        # context and RuntimeError for one already spent.
        _log.debug("execution id token already spent or foreign", exc_info=True)


def get_current_execution_id() -> str | None:
    return _current_execution_id.get(None)


def set_current_session_id(session_id: str):
    """Bind session_id to the current worker context. Call at the top of
    a turn's execution. Returns the token for later reset()."""
    return _current_session_id.set(session_id)


def get_current_session_id() -> str | None:
    """The webui session bound to the current worker context, or None when
    not inside a dispatcher-driven turn (CLI / tests / headless)."""
    return _current_session_id.get()


def reset_current_session_id(token) -> None:
    """Reset the session_id ContextVar using a token from set_current_session_id."""
    try:
        _current_session_id.reset(token)
    except (ValueError, RuntimeError):
        # ContextVar.reset raises ValueError for a token from another
        # context and RuntimeError for one already spent.
        _log.debug("session id token already spent or foreign", exc_info=True)

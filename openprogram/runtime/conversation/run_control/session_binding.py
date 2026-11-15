"""Turn-scoped context: the pre-approved wait of a resumed tool call.

The session, execution and token bindings themselves live in
``openprogram.runtime.execution.run_control.binding``.
"""
from __future__ import annotations

from contextvars import ContextVar

from openprogram.runtime.execution.run_control.binding import (
    _current_execution_id,
    _current_session_id,
    _current_token,
)


# A resumed tool call receives the durable approval identity selected by the
# Agent safe-point transaction.  It is scoped to one tool invocation and
# contains no answer or lifecycle state; those remain in execution_waits.
_preapproved_wait_id: ContextVar[str | None] = ContextVar(
    "_preapproved_wait_id", default=None,
)


def set_preapproved_wait_id(wait_id: str):
    return _preapproved_wait_id.set(wait_id)


def reset_preapproved_wait_id(token) -> None:
    _preapproved_wait_id.reset(token)


def get_preapproved_wait_id() -> str | None:
    return _preapproved_wait_id.get()


def clear_turn_context() -> None:
    """Drop context-bound session/execution/token (test teardown)."""
    _current_session_id.set(None)
    _current_execution_id.set(None)
    _current_token.set(None)

"""Why a run was stopped, and what a stopped tool call tells the model.

One table maps a stop's ``reason_code`` onto what stopped it (the person,
the task that started it, the whole conversation, a time limit, a service
restart). The tool result a stopped call leaves follows Codex
(``core/src/tools/parallel.rs``): every call gets a result, a call that ran
says how long it ran, a call that never started says so; the output a call
had already produced stays in front of that line (OpenCode
``shell/result.ts``). The reply's own interruption note is
:func:`openprogram.runtime.context.turn_context.interruption_note`.
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

USER = "cancel.user"
PARENT = "cancel.parent"
SESSION = "cancel.session"
WORKER = "worker_stopping"

#: Reasons that mean a time limit ended the run.
TIME_LIMIT_REASONS = frozenset({"time_limit", "cancel.timeout", "budget.runtime_exhausted"})
#: Reasons that mean the service went away under the run, not a person.
RESTART_REASONS = frozenset({WORKER, "error.worker_lost", "interrupted"})

#: The result of a call the model issued, whole, that never started.
NOT_RUN_TEXT = "Not run: the turn was stopped before this call started."


def current_stop_reason() -> str:
    """The reason the run bound to this context was stopped.

    The Worker stopping wins; otherwise the canonical execution's own
    ``reason_code`` (written when its stop was accepted); a run with no
    readable record was stopped by the person.
    """
    try:
        from openprogram.host.runtime_facts import worker_stopping

        if worker_stopping():
            return WORKER
    except Exception:
        _log.debug("worker state not read", exc_info=True)
    try:
        from openprogram.runtime.execution import default_store
        from openprogram.runtime.execution.run_control.binding import get_current_execution_id

        execution_id = get_current_execution_id()
        record = default_store().get_execution(execution_id) if execution_id else None
        reason = getattr(record, "reason_code", None)
        if isinstance(reason, str) and reason:
            return reason
    except Exception:
        _log.debug("stop reason not read", exc_info=True)
    return USER


def stopped_by(reason: str | None) -> str:
    """The words after "Stopped" that name what stopped a call."""
    if reason == PARENT:
        return "because the task that started it was stopped"
    if reason == SESSION:
        return "when the whole conversation was stopped"
    if reason in TIME_LIMIT_REASONS:
        return "by its time limit"
    return "by the user"


def stopped_tool_text(reason: str | None, seconds: float, partial: str = "") -> str:
    """The result of a call that was running when its turn was stopped."""
    if reason in RESTART_REASONS:
        line = f"Interrupted after {seconds:.1f}s because the service restarted."
    else:
        line = f"Stopped {stopped_by(reason)} after {seconds:.1f}s."
    partial = (partial or "").rstrip()
    return f"{partial}\n\n{line}" if partial else line


__all__ = [
    "NOT_RUN_TEXT", "PARENT", "RESTART_REASONS", "SESSION", "TIME_LIMIT_REASONS",
    "USER", "WORKER", "current_stop_reason", "stopped_by", "stopped_tool_text",
]

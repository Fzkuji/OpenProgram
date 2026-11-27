"""Why a run was stopped, and what a stopped tool call tells the model.

One table maps a stop's ``reason_code`` onto what stopped it (the person,
the task that started it, the whole conversation, a time limit, a service
restart). The tool result a stopped call leaves follows Codex
(``core/src/tools/parallel.rs``): every call gets a result, a call that ran
says how long it ran, a call that never started says so; the output a call
had already produced stays in front of that line (OpenCode
``shell/result.ts``). The turn after a stopped reply is told so beside its
message (:func:`interruption_note`).
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from openprogram.runtime.execution.model import ExecutionStatus

_log = logging.getLogger(__name__)

USER = "cancel.user"
PARENT = "cancel.parent"
SESSION = "cancel.session"
WORKER = "worker_stopping"
#: An unattended run (a check-in, a scheduled run) that went on past its limit.
TIME_LIMIT = "time_limit"

#: Reasons that mean a time limit ended the run.
TIME_LIMIT_REASONS = frozenset({TIME_LIMIT, "cancel.timeout", "budget.runtime_exhausted"})
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


def stopped_by_limit(execution: Any) -> bool:
    """Whether this execution ended because it ran out of time."""
    return (getattr(execution, "status", None) is ExecutionStatus.CANCELLED
            and getattr(execution, "reason_code", None) == TIME_LIMIT)


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


_INTERRUPTION_NOTES = {
    "cancel.user": "The user stopped your previous reply before it finished; its last part is incomplete.",
    "cancel.parent": ("Your previous reply was stopped because the task that started it was stopped; "
                      "its last part is incomplete."),
    "cancel.session": ("Your previous reply was stopped when the whole conversation was stopped; "
                       "its last part is incomplete."),
}
_TIME_LIMIT_NOTE = "Your previous reply was stopped by its time limit; its last part is incomplete."
_RESTART_NOTE = ("Your previous reply was cut off by a service restart, not by the user; "
                 "continue from where it left off if the next message asks for it.")
_OTHER_NOTE = "Your previous reply was stopped before it finished."
_CUT_TOOLS_NOTE = " A tool call that was stopped may have partly run."


def interruption_note(interruption: Any) -> str:
    """What the turn after a stopped reply is told, beside its message.

    A fact and, when a tool call was stopped or never ran, that it may have
    partly run (Codex ``<turn_aborted>``), put in front of the next user
    message (Grok Build) rather than in the assistant's place, where a
    model repeats it (Hermes). Written once, on that message's node.
    """
    if not isinstance(interruption, Mapping):
        return ""
    reason = interruption.get("reason_code")
    if reason in RESTART_REASONS:
        note = _RESTART_NOTE
    elif reason in TIME_LIMIT_REASONS:
        note = _TIME_LIMIT_NOTE
    else:
        note = _INTERRUPTION_NOTES.get(str(reason), _OTHER_NOTE)
    if interruption.get("cut_tools"):
        note += _CUT_TOOLS_NOTE
    return note


__all__ = [
    "NOT_RUN_TEXT", "PARENT", "RESTART_REASONS", "SESSION", "TIME_LIMIT", "TIME_LIMIT_REASONS",
    "USER", "WORKER", "current_stop_reason", "interruption_note", "stopped_by", "stopped_by_limit",
    "stopped_tool_text",
]

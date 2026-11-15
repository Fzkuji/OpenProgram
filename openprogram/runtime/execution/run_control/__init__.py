"""Execution binding and cancellation inside one process.

* ``binding`` — the session, execution and token bound to the current context.
* ``tokens`` — one cancellation token per execution.
* ``owners`` — the live owner of an execution, its grace period and the
  finalizer that writes ``cancelled`` once the owner is gone.
* ``cancellation`` — exact execution cancel (canonical command or legacy
  record), session cancel and child admission.
* ``records`` — the legacy DAG node and Job records a cancel reads.

The turn side (turn tokens, cancel events, active exec runtimes) lives in
``openprogram.runtime.conversation.run_control``. Subprocess signals and Job
records come from the engines through ``openprogram.runtime.execution.hooks``.
"""
from openprogram.runtime.execution.run_control.binding import (
    get_current_execution_id,
    get_current_session_id,
    reset_current_execution_id,
    reset_current_session_id,
    set_current_execution_id,
    set_current_session_id,
)
from openprogram.runtime.execution.run_control.cancellation import (
    admit_child_execution,
    cancel_execution,
    cancel_execution_for_reason,
    cancel_session_executions,
    child_execution_admission,
    mark_cancelled,
)
from openprogram.runtime.execution.run_control.errors import (
    ExecutionNotCancellable,
    ExecutionNotFound,
    ExecutionSpawnRefused,
)
from openprogram.runtime.execution.run_control.owners import (
    mark_execution_terminal,
    owner_is_alive,
    register_execution_owner,
    resume_cancel,
    retire_execution_owner,
)
from openprogram.runtime.execution.run_control.tokens import CancellationToken, current_token

__all__ = [
    "CancellationToken",
    "ExecutionNotCancellable",
    "ExecutionNotFound",
    "ExecutionSpawnRefused",
    "admit_child_execution",
    "cancel_execution",
    "cancel_execution_for_reason",
    "cancel_session_executions",
    "child_execution_admission",
    "current_token",
    "get_current_execution_id",
    "get_current_session_id",
    "mark_cancelled",
    "mark_execution_terminal",
    "owner_is_alive",
    "register_execution_owner",
    "reset_current_execution_id",
    "reset_current_session_id",
    "resume_cancel",
    "retire_execution_owner",
    "set_current_execution_id",
    "set_current_session_id",
]

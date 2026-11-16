"""Durable-intent-first coordination for execution control commands."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Any, Callable, Mapping

from openprogram.state.paths import get_active_profile

from ..attempts import AttemptRecord, AttemptStore
from ..recovery.checkpoints import (
    CheckpointManifest,
)
from ..driver import (
    ActivationInput,
    DriverAck,
    DriverRegistry,
)
from ..recovery.effects import EffectRecord
from ..model import (
    CommandKind,
    CommandStatus,
    ControlCommand,
    ExecutionRecord,
    RevisionRecord,
    TERMINAL_EXECUTION_STATUSES,
)
from ..store import (
    CommandConflict,
    ExecutionConflict,
    default_store,
)
from ..state_machine import InvalidCommand

if TYPE_CHECKING:
    from .service import RuntimeControlService


Activator = Callable[[AttemptRecord, ActivationInput], Any]
_log = logging.getLogger(__name__)


async def off_loop(execution_id: str, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Run ``fn`` (blocking work on an execution's records) in a worker thread.

    An execution's records, its conversation's nodes among them, are
    written under that conversation's lock; a control command awaited on
    the Worker's event loop must not wait for that lock there. The work
    runs in the execution's lane (``conversation_work``): many commands
    for one execution take at most that lane's threads, in arrival order,
    so one held conversation never takes the threads of the others. The
    awaits (an owner's signal, an activation) stay on the caller's loop.
    """
    from openprogram.state.store.session.conversation_work import run_for_conversation

    return await run_for_conversation(f"execution:{execution_id}", fn, *args, **kwargs)


class ProjectionRecoveryRequired(RuntimeError):
    """Canonical terminal state exists but its legacy projection needs repair."""

    code = "projection_recovery_required"


_default_control_services: dict[str, RuntimeControlService] = {}
_default_control_services_lock = RLock()


def default_control_service() -> RuntimeControlService:
    """Return the canonical control service for the active profile."""
    from .service import RuntimeControlService
    profile = get_active_profile() or "default"
    # Test and embedded workers may switch the state directory while keeping
    # the profile name. Key the service by the actual execution database so
    # admission and activation cannot read different stores.
    from openprogram.state.paths import get_run_state_path
    service_key = f"{profile}:{get_run_state_path()}"
    with _default_control_services_lock:
        service = _default_control_services.get(service_key)
        if service is None:
            executions = default_store()
            service = RuntimeControlService(
                executions,
                AttemptStore(executions),
                DriverRegistry(),
            )
            async def _activate_agent(attempt, activation, *, service=service):
                # Keep activation at the canonical service boundary. The
                # Agent engine registers the activator (execution.hooks);
                # the driver it builds is retained by the DriverRegistry
                # binding, not by a WebSocket-local temporary object.
                from .. import hooks
                activate = hooks.agent_activator(executions, service)
                return await activate(attempt, activation)
            service.activator = _activate_agent
            _default_control_services[service_key] = service
        return service


_CANCEL_SUPERSEDES = (
    CommandKind.PAUSE,
    CommandKind.CONTINUE,
    CommandKind.STEP,
    CommandKind.STEER,
    CommandKind.FORK,
    CommandKind.RETRY,
)
_PAUSE_SUPERSEDES = (
    CommandKind.CONTINUE,
    CommandKind.STEP,
)


@dataclass(frozen=True)
class ControlDispatch:
    # ``None`` only for a stop of an execution that had already ended
    # (``issue_code == "already_terminal"``): no command was written.
    command: ControlCommand | None
    execution: ExecutionRecord
    delivered: bool
    ack: DriverAck | None = None
    issue_code: str | None = None


@dataclass(frozen=True)
class ObservedCancelSubmission:
    """Outcome of one protocol cancel intent.

    Protocol transports keep their original command identity. The store
    takes the stop at the execution's current version, so an observation
    the execution has since moved past needs no second attempt.
    """

    command: ControlCommand | None
    execution: ExecutionRecord | None
    accepted: bool


async def submit_observed_cancel(
    service: "RuntimeControlService",
    *,
    command_id: str,
    execution_id: str,
    expected_version: int,
    actor: Mapping[str, Any],
    reason_code: str,
) -> ObservedCancelSubmission:
    """Submit one exact cancel from a protocol transport (MCP, ACP).

    ``accepted`` says this command is the stop in force. A stop of an
    execution another stop already holds, or that has ended, is not this
    protocol's to act on locally, so it is not accepted.
    """

    existing = service.executions.get_command(command_id)
    if existing is not None:
        return _existing_observed_cancel(
            service,
            command=existing,
            execution_id=execution_id,
            actor=actor,
            reason_code=reason_code,
        )
    # The same stop as the page's: what the turn waits on stops with it.
    from openprogram.runtime.execution.run_control.cancellation import (
        stop_with_descendants,
    )

    try:
        dispatch = await stop_with_descendants(
            execution_id,
            lambda: service.request_cancel(
                command_id=command_id,
                execution_id=execution_id,
                expected_version=expected_version,
                actor=actor,
                reason_code=reason_code,
            ),
            waited_on_only=True,
        )
    except CommandConflict:
        existing = service.executions.get_command(command_id)
        if existing is None:
            raise
        return _existing_observed_cancel(
            service,
            command=existing,
            execution_id=execution_id,
            actor=actor,
            reason_code=reason_code,
        )
    except (ExecutionConflict, ProjectionRecoveryRequired, InvalidCommand):
        return ObservedCancelSubmission(
            command=service.executions.get_command(command_id),
            execution=service.executions.get_execution(execution_id),
            accepted=False,
        )
    command = service.executions.get_command(command_id)
    latest = service.executions.get_execution(execution_id) or dispatch.execution
    # The stop in force under another command id, or none (the execution
    # had ended), is not a local cancellation.
    return ObservedCancelSubmission(
        command=command,
        execution=latest,
        accepted=command is not None and command.status in {
            CommandStatus.APPLYING,
            CommandStatus.APPLIED,
        },
    )


def _existing_observed_cancel(
    service: "RuntimeControlService",
    *,
    command: ControlCommand,
    execution_id: str,
    actor: Mapping[str, Any],
    reason_code: str,
) -> ObservedCancelSubmission:
    """Return a prior protocol intent without weakening command collisions."""

    if (
        command.execution_id != execution_id
        or command.kind is not CommandKind.CANCEL
        or dict(command.payload) != {"reason_code": reason_code}
        or dict(command.actor) != dict(actor)
    ):
        raise CommandConflict(
            "idempotency_collision",
            f"command_id was already used for a different request: "
            f"{command.command_id}",
        )
    return ObservedCancelSubmission(
        command=command,
        execution=service.executions.get_execution(execution_id),
        accepted=command.status in {
            CommandStatus.APPLYING,
            CommandStatus.APPLIED,
        },
    )


async def submit_wait_command(
    service: "RuntimeControlService",
    *,
    action: str,
    command_id: str,
    execution_id: str,
    expected_version: int,
    actor: Mapping[str, Any],
    wait_id: str,
    generation: int,
    value: Any = None,
) -> "ControlDispatch":
    """Submit one authorized answer/decline for one exact durable wait.

    Non-HTTP surfaces use this function instead of writing through
    ``QuestionRegistry``. Authorization is evaluated against the durable
    execution and its project/session binding before the wait command runs.
    """
    from ..authorization import authorize_execution_action
    from ..projections.public import project_id_for_session

    action_to_method = {
        "execution.wait.answer": service.request_wait_answer,
        "execution.wait.decline": service.request_wait_decline,
    }
    method = action_to_method.get(action)
    if method is None:
        raise ExecutionConflict("invalid_command", "unsupported wait action")
    execution = service.executions.get_execution(execution_id)
    if execution is None:
        raise ExecutionConflict("not_found", "execution not found")
    authorize_execution_action(
        actor, action, execution,
        {"project_id": project_id_for_session(execution.session_id),
         "session_id": execution.session_id},
    )
    kwargs: dict[str, Any] = {
        "command_id": command_id,
        "execution_id": execution_id,
        "expected_version": expected_version,
        "actor": actor,
        "wait_id": wait_id,
        "generation": generation,
    }
    if action == "execution.wait.answer":
        kwargs["answer"] = value
    else:
        kwargs["reason"] = value
    return await method(**kwargs)


@dataclass(frozen=True)
class SafePointCompletion:
    command: ControlCommand
    execution: ExecutionRecord
    attempt: AttemptRecord
    checkpoint: CheckpointManifest | None
    applied_commands: tuple[ControlCommand, ...] = ()


@dataclass(frozen=True)
class WaitSuspension:
    """One durable wait created at an Agent-safe resumable boundary."""

    wait: Any
    checkpoint: CheckpointManifest
    execution: ExecutionRecord
    attempt: AttemptRecord


@dataclass(frozen=True)
class AttemptCompletion:
    execution: ExecutionRecord
    attempt: AttemptRecord
    command: ControlCommand | None = None


@dataclass(frozen=True)
class ReconciliationCompletion:
    effect: EffectRecord
    execution: ExecutionRecord
    command: ControlCommand | None = None


@dataclass(frozen=True)
class RecoveryCompletion:
    execution: ExecutionRecord
    attempt: AttemptRecord | None = None
    command: ControlCommand | None = None


@dataclass(frozen=True)
class BranchCompletion:
    """Atomic result of a fork or retry command."""

    command: ControlCommand
    execution: ExecutionRecord
    child: ExecutionRecord
    revision: RevisionRecord
    checkpoint: CheckpointManifest

    @property
    def child_execution(self) -> ExecutionRecord:
        return self.child



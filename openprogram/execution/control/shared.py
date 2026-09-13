"""Durable-intent-first coordination for execution control commands."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, replace
from threading import RLock, Timer
from typing import Any, Callable, Mapping

from openprogram.paths import get_active_profile

from ..attempts import AttemptConflict, AttemptRecord, AttemptStatus, AttemptStore
from ..checkpoints import (
    CheckpointFragment,
    CheckpointManifest,
    ExecutionCheckpointStore,
)
from ..driver import (
    ActivationInput,
    DriverAck,
    DriverBinding,
    DriverRegistry,
    DriverRegistryConflict,
)
from ..effects import EffectRecord, EffectStatus, EffectStore
from ..model import (
    CommandKind,
    CommandStatus,
    ControlCommand,
    ExecutionRecord,
    ExecutionStatus,
    RevisionRecord,
    TERMINAL_COMMAND_STATUSES,
    TERMINAL_EXECUTION_STATUSES,
    _thaw_json,
)
from ..store import (
    CommandConflict,
    ExecutionConflict,
    ExecutionStore,
    _json,
    default_store,
)
from ..state_machine import InvalidCommand
from ..state_blobs import ExecutionStateBlobStore
from ..safe_points import AgentSafePointConflict


Activator = Callable[[AttemptRecord, ActivationInput], Any]
_log = logging.getLogger(__name__)


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
    from openprogram.paths import get_execution_db_path
    service_key = f"{profile}:{get_execution_db_path()}"
    with _default_control_services_lock:
        service = _default_control_services.get(service_key)
        if service is None:
            executions = default_store()
            service = RuntimeControlService(
                executions,
                AttemptStore(executions),
                DriverRegistry(),
            )
            async def _activate_agent(attempt, activation):
                # Keep activation at the canonical service boundary.  The
                # resulting driver is retained by DriverRegistry binding,
                # rather than being a WebSocket-local temporary object.
                from openprogram.agent.production_driver import AgentProductionDriver
                driver = AgentProductionDriver(executions, control_service=service)
                return await driver.activate(attempt, activation)
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
    command: ControlCommand
    execution: ExecutionRecord
    delivered: bool
    ack: DriverAck | None = None
    issue_code: str | None = None


@dataclass(frozen=True)
class ObservedCancelSubmission:
    """Outcome of one protocol cancel intent submitted from an observation.

    Protocol transports keep their original command identity and observed
    version.  A stale observation may obtain one newer snapshot before its
    first durable write; it must not manufacture a second intent locally.
    """

    command: ControlCommand | None
    execution: ExecutionRecord | None
    accepted: bool
    retried_stale_observation: bool = False


async def submit_observed_cancel(
    service: "RuntimeControlService",
    *,
    command_id: str,
    execution_id: str,
    expected_version: int,
    actor: Mapping[str, Any],
    reason_code: str,
) -> ObservedCancelSubmission:
    """Submit one exact cancel using a transport's saved observation.

    The initial request never reads a fresher version.  Only an unaccepted
    ``stale_version`` conflict permits one retry, using the newly observed
    snapshot but retaining the same command id, actor, and reason.
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

    observed_version = expected_version
    retried = False
    while True:
        try:
            dispatch = await service.request_cancel(
                command_id=command_id,
                execution_id=execution_id,
                expected_version=observed_version,
                actor=actor,
                reason_code=reason_code,
            )
        except ExecutionConflict as exc:
            latest = service.executions.get_execution(execution_id)
            if exc.code != "stale_version" or retried or latest is None:
                return ObservedCancelSubmission(
                    command=service.executions.get_command(command_id),
                    execution=latest,
                    accepted=False,
                    retried_stale_observation=retried,
                )
            if latest.status in TERMINAL_EXECUTION_STATUSES:
                return ObservedCancelSubmission(
                    command=service.executions.get_command(command_id),
                    execution=latest,
                    accepted=False,
                    retried_stale_observation=True,
                )
            observed_version = latest.status_version
            retried = True
            continue
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
                retried_stale_observation=retried,
            )
        except ProjectionRecoveryRequired:
            latest = service.executions.get_execution(execution_id)
            return ObservedCancelSubmission(
                command=service.executions.get_command(command_id),
                execution=latest,
                accepted=False,
                retried_stale_observation=retried,
            )
        except InvalidCommand:
            # A late terminal cancel has no new command row. Return the
            # current canonical snapshot and leave the protocol's local task
            # untouched rather than translating terminal rejection into a
            # second cancellation.
            return ObservedCancelSubmission(
                command=service.executions.get_command(command_id),
                execution=service.executions.get_execution(execution_id),
                accepted=False,
                retried_stale_observation=retried,
            )

        latest = service.executions.get_execution(execution_id) or dispatch.execution
        command = service.executions.get_command(command_id) or dispatch.command
        accepted = command.status in {
            CommandStatus.APPLYING,
            CommandStatus.APPLIED,
        }
        # REJECTED is intentionally not treated as a local cancellation even
        # if another owner has already changed the execution snapshot.
        return ObservedCancelSubmission(
            command=command,
            execution=latest,
            accepted=accepted,
            retried_stale_observation=retried,
        )


def _existing_observed_cancel(
    service: "RuntimeControlService",
    *,
    command: ControlCommand,
    execution_id: str,
    actor: Mapping[str, Any],
    reason_code: str,
    retried_stale_observation: bool = False,
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
        retried_stale_observation=retried_stale_observation,
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
    from ..public import project_id_for_session

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



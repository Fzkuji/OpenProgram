"""Durable-intent-first coordination for execution control commands."""

from __future__ import annotations

import inspect
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, replace
from threading import RLock
from typing import Any, Callable, Mapping

from openprogram.paths import get_active_profile

from .attempts import AttemptConflict, AttemptRecord, AttemptStatus, AttemptStore
from .checkpoints import (
    CheckpointFragment,
    CheckpointManifest,
    ExecutionCheckpointStore,
)
from .driver import (
    ActivationInput,
    DriverAck,
    DriverBinding,
    DriverRegistry,
    DriverRegistryConflict,
)
from .effects import EffectRecord, EffectStatus, EffectStore
from .model import (
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
from .store import (
    CommandConflict,
    ExecutionConflict,
    ExecutionStore,
    _json,
    default_store,
)


Activator = Callable[[AttemptRecord, ActivationInput], Any]
_log = logging.getLogger(__name__)


class ProjectionRecoveryRequired(RuntimeError):
    """Canonical terminal state exists but its legacy projection needs repair."""

    code = "projection_recovery_required"


_default_control_services: dict[str, RuntimeControlService] = {}
_default_control_services_lock = RLock()


def default_control_service() -> RuntimeControlService:
    """Return the canonical control service for the active profile."""
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
class SafePointCompletion:
    command: ControlCommand
    execution: ExecutionRecord
    attempt: AttemptRecord
    checkpoint: CheckpointManifest | None
    applied_commands: tuple[ControlCommand, ...] = ()


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


class RuntimeControlService:
    """The sole coordinator for canonical commands and live driver signals."""

    def __init__(
        self,
        executions: ExecutionStore,
        attempts: AttemptStore,
        registry: DriverRegistry,
        *,
        activator: Activator | None = None,
        owner_id: str = "control-service",
        lease_ttl_seconds: float = 30.0,
    ) -> None:
        self.executions = executions
        self.attempts = attempts
        self.registry = registry
        self.registry.set_owner_resolver(self._durable_owner)
        self.effects = EffectStore(executions)
        self.checkpoints = ExecutionCheckpointStore(executions)
        self.activator = activator
        self.owner_id = owner_id
        self.lease_ttl_seconds = lease_ttl_seconds
        self._terminal_observer: Callable[[ExecutionRecord], object] | None = None
        self._terminal_preparer: Callable[..., object] | None = None
        self._terminal_recovery: Callable[..., object] | None = None
        self._cancel_delivery_lock = RLock()
        self._delivered_cancel_commands: set[str] = set()
        self._cancel_delivery_by_execution: dict[str, set[str]] = {}

    def _remember_cancel_delivery(
        self, execution_id: str, command_id: str,
    ) -> None:
        with self._cancel_delivery_lock:
            self._delivered_cancel_commands.add(command_id)
            self._cancel_delivery_by_execution.setdefault(
                execution_id, set(),
            ).add(command_id)

    def _forget_cancel_delivery(
        self, execution_id: str, command_id: str | None = None,
    ) -> None:
        """Release the transient dedupe marker after cancellation settles."""
        with self._cancel_delivery_lock:
            if command_id is None:
                command_ids = self._cancel_delivery_by_execution.pop(
                    execution_id, set(),
                )
                self._delivered_cancel_commands.difference_update(command_ids)
                return
            self._delivered_cancel_commands.discard(command_id)
            command_ids = self._cancel_delivery_by_execution.get(execution_id)
            if command_ids is None:
                return
            command_ids.discard(command_id)
            if not command_ids:
                self._cancel_delivery_by_execution.pop(execution_id, None)

    def _prune_cancel_delivery(self, execution_id: str) -> None:
        """Drop markers whose persisted commands are already terminal."""
        with self._cancel_delivery_lock:
            command_ids = tuple(
                self._cancel_delivery_by_execution.get(execution_id, ()),
            )
        for command_id in command_ids:
            command = self.executions.get_command(command_id)
            if command is None or command.status in TERMINAL_COMMAND_STATUSES:
                self._forget_cancel_delivery(execution_id, command_id)

    def _reconcile_terminal_cancel(
        self, execution: ExecutionRecord, connection=None,
    ) -> None:
        """Finish applying cancel commands after a terminal execution CAS."""
        if connection is None:
            commands = self.executions.list_commands(
                execution.execution_id,
                statuses=(CommandStatus.APPLYING,),
                kinds=(CommandKind.CANCEL,),
            )
        else:
            rows = connection.execute(
                "SELECT * FROM commands WHERE execution_id = ? AND kind = ? "
                "AND status = ? ORDER BY submitted_at, command_id",
                (
                    execution.execution_id,
                    CommandKind.CANCEL.value,
                    CommandStatus.APPLYING.value,
                ),
            ).fetchall()
            commands = [self.executions._command(row) for row in rows]
        if not commands:
            return
        if execution.status is ExecutionStatus.CANCELLED:
            target = CommandStatus.APPLIED
            rejection_code = None
            receipt = {"recovered": "terminal_execution"}
        else:
            target = CommandStatus.REJECTED
            rejection_code = "execution_terminal"
            receipt = {"recovered": "terminal_execution"}
        for command in commands:
            if connection is None:
                self.executions.transition_command(
                    command.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=target,
                    result_version=execution.status_version,
                    rejection_code=rejection_code,
                    receipt=receipt,
                )
            else:
                self.executions._transition_command(
                    connection,
                    command.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=target,
                    result_version=execution.status_version,
                    rejection_code=rejection_code,
                    receipt=receipt,
                )

    def reconcile_terminal_cancel(self, execution: ExecutionRecord) -> None:
        """Durably settle cancel commands after a terminal execution write."""
        if execution.status not in TERMINAL_EXECUTION_STATUSES:
            return
        self._reconcile_terminal_cancel(execution)
        self._forget_cancel_delivery(execution.execution_id)

    def set_terminal_observer(
        self, observer: Callable[[ExecutionRecord], object] | None,
    ) -> None:
        """Attach a transport-neutral projection/release observer."""
        self._terminal_observer = observer

    def set_terminal_preparer(
        self, preparer: Callable[..., object] | None,
    ) -> None:
        """Attach a pre-terminal barrier for external resource projections."""
        self._terminal_preparer = preparer

    def set_terminal_recovery(
        self, recovery: Callable[..., object] | None,
    ) -> None:
        """Attach recovery recording for a failed canonical CAS."""
        self._terminal_recovery = recovery

    def _observe_terminal(self, execution: ExecutionRecord) -> None:
        if execution.status not in TERMINAL_EXECUTION_STATUSES:
            return
        observer = self._terminal_observer
        if observer is not None:
            # The observer persists a retryable projection intent before it
            # returns when the JobStore is unavailable.  Any failure to
            # persist that intent is therefore visible to the caller rather
            # than silently losing the release obligation.
            observer(execution)

    async def request_continue(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: Mapping[str, Any],
        owner_id: str | None = None,
        ttl_seconds: float | None = None,
        activator: Activator | None = None,
        driver: Any | None = None,
    ) -> ControlDispatch:
        """Resume a paused execution without changing its revision or identity."""
        command, execution, attempt, checkpoint, steer_inputs, duplicate = self._resume_transaction(
            command_id=command_id,
            execution_id=execution_id,
            expected_version=expected_version,
            actor=actor,
            kind=CommandKind.CONTINUE,
            owner_id=owner_id or self.owner_id,
            ttl_seconds=ttl_seconds or self.lease_ttl_seconds,
        )
        if duplicate:
            return ControlDispatch(command=command, execution=execution, delivered=False)
        delivered, issue = await self._activate(
            attempt, checkpoint, steer_inputs, activator=activator, driver=driver
        )
        command, execution = self._finish_activation(
            attempt, command, delivered=delivered, issue_code=issue
        )
        return ControlDispatch(
            command=command,
            execution=execution,
            delivered=delivered,
            issue_code=issue,
        )

    async def request_step(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: Mapping[str, Any],
        owner_id: str | None = None,
        ttl_seconds: float | None = None,
        activator: Activator | None = None,
        driver: Any | None = None,
    ) -> ControlDispatch:
        """Create exactly one durable permit and activate a fresh attempt."""
        command, execution, attempt, checkpoint, steer_inputs, duplicate = self._resume_transaction(
            command_id=command_id,
            execution_id=execution_id,
            expected_version=expected_version,
            actor=actor,
            kind=CommandKind.STEP,
            owner_id=owner_id or self.owner_id,
            ttl_seconds=ttl_seconds or self.lease_ttl_seconds,
        )
        if duplicate:
            return ControlDispatch(command=command, execution=execution, delivered=False)
        delivered, issue = await self._activate(
            attempt, checkpoint, steer_inputs, activator=activator, driver=driver
        )
        command, execution = self._finish_activation(
            attempt, command, delivered=delivered, issue_code=issue
        )
        return ControlDispatch(
            command=command,
            execution=execution,
            delivered=delivered,
            issue_code=issue,
        )

    def request_steer(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> ControlDispatch:
        """Persist steering input; application happens at a durable safe point."""
        command = self.executions.accept_command(
            command_id=command_id,
            execution_id=execution_id,
            expected_version=expected_version,
            kind=CommandKind.STEER,
            payload=payload,
            actor=actor,
        )
        execution = self.executions.get_execution(execution_id)
        assert execution is not None
        # Paused steering is intentionally only accepted.  It is consumed by
        # the first safe point of the next continue/step attempt.
        return ControlDispatch(command=command, execution=execution, delivered=False)

    def request_fork(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: Mapping[str, Any],
        revision_manifest: Mapping[str, Any] | None = None,
        compatible_prefix: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] | None = None,
        checkpoint_id: str | None = None,
        revision_id: str | None = None,
        child_execution_id: str | None = None,
        manifest: Mapping[str, Any] | None = None,
        source_checkpoint_id: str | None = None,
    ) -> BranchCompletion:
        """Create a queued child with a new immutable revision atomically."""
        if revision_manifest is None:
            revision_manifest = manifest
        if checkpoint_id is None:
            checkpoint_id = source_checkpoint_id
        if revision_manifest is None:
            raise ExecutionConflict("revision_manifest_required", "fork requires a revision manifest")
        if compatible_prefix is None:
            raise ExecutionConflict("compatible_prefix_required", "fork requires a compatible prefix")
        normalized_prefix = self._validate_compatible_prefix(compatible_prefix)
        payload = {
            "checkpoint_id": checkpoint_id,
            "revision_manifest": dict(revision_manifest),
            "compatible_prefix": [
                {"step_id": step_id, "contract_hash": contract_hash}
                for step_id, contract_hash in normalized_prefix
            ],
        }
        if revision_id is not None:
            payload["revision_id"] = revision_id
        if child_execution_id is not None:
            payload["child_execution_id"] = child_execution_id
        return self._request_branch(
            command_id=command_id,
            execution_id=execution_id,
            expected_version=expected_version,
            actor=actor,
            kind=CommandKind.FORK,
            payload=payload,
            manifest=revision_manifest,
            compatible_prefix=compatible_prefix,
            checkpoint_id=checkpoint_id,
            revision_id=revision_id,
            child_execution_id=child_execution_id,
        )

    def request_retry(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: Mapping[str, Any],
        child_execution_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> BranchCompletion:
        """Create a queued same-revision child from the source checkpoint head."""
        command_payload = dict(payload or {})
        if child_execution_id is not None:
            command_payload["child_execution_id"] = child_execution_id
        return self._request_branch(
            command_id=command_id,
            execution_id=execution_id,
            expected_version=expected_version,
            actor=actor,
            kind=CommandKind.RETRY,
            payload=command_payload,
            manifest=None,
            compatible_prefix=None,
            checkpoint_id=None,
            revision_id=None,
            child_execution_id=child_execution_id,
        )

    def _request_branch(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: Mapping[str, Any],
        kind: CommandKind,
        payload: Mapping[str, Any],
        manifest: Mapping[str, Any] | None,
        compatible_prefix: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] | None,
        checkpoint_id: str | None,
        revision_id: str | None,
        child_execution_id: str | None,
    ) -> BranchCompletion:
        with self.executions._transaction() as connection:
            command, duplicate = self.executions._accept_command(
                connection,
                command_id=command_id,
                execution_id=execution_id,
                expected_version=expected_version,
                kind=kind,
                payload=payload,
                actor=actor,
            )
            source = self.executions._require_execution(connection, execution_id)
            if duplicate:
                result = dict(command.result_json)
                child_id = result.get("child_execution_id")
                if not child_id:
                    raise ExecutionConflict("branch_result_missing", "branch command has no child result")
                child = self.executions._require_execution(connection, str(child_id))
                revision = self.executions._get_revision(connection, child.revision_id)
                checkpoint = (
                    self.checkpoints._get(connection, child.source_checkpoint_id)
                    if child.source_checkpoint_id
                    else None
                )
                if revision is None or checkpoint is None:
                    raise ExecutionConflict("branch_result_missing", "branch result references missing records")
                return BranchCompletion(command, source, child, revision, checkpoint)

            if kind is CommandKind.FORK and checkpoint_id is None:
                raise ExecutionConflict("checkpoint_required", "fork requires an explicit checkpoint")
            checkpoint_id = checkpoint_id or source.checkpoint_head_id
            if checkpoint_id is None:
                raise ExecutionConflict("checkpoint_required", "a published checkpoint is required")
            checkpoint = self.checkpoints._get(connection, checkpoint_id)
            if checkpoint is None:
                raise ExecutionConflict("checkpoint_not_found", "specified checkpoint is not published")
            if checkpoint.execution_id != source.execution_id or checkpoint.revision_id != source.revision_id:
                raise ExecutionConflict("invalid_checkpoint", "checkpoint does not belong to the source revision")
            unresolved = connection.execute(
                "SELECT 1 FROM effects WHERE execution_id = ? AND status IN ('dispatched', 'uncertain') LIMIT 1",
                (execution_id,),
            ).fetchone()
            if unresolved is not None:
                raise ExecutionConflict("unresolved_effect", "source execution has an unresolved effect")
            if kind is CommandKind.FORK:
                if checkpoint.completed_frontier is None:
                    raise ExecutionConflict("checkpoint_frontier_required", "fork requires a completed frontier")
                normalized = self._validate_compatible_prefix(compatible_prefix)
                source_prefix = self._validate_compatible_prefix(checkpoint.completed_frontier)
                if normalized != source_prefix:
                    raise ExecutionConflict("incompatible_prefix", "compatible prefix does not match the checkpoint")
            command = self.executions._transition_command(
                connection,
                command_id,
                expected_status=CommandStatus.ACCEPTED,
                target=CommandStatus.APPLYING,
            )
            if kind is CommandKind.FORK:
                revision = self.executions._create_revision_in_transaction(
                    connection,
                    manifest=manifest or {},
                    revision_id=revision_id,
                    requested_id=revision_id,
                    parent_revision_id=source.revision_id,
                )
            else:
                revision = self.executions._get_revision(connection, source.revision_id)
                if revision is None:
                    raise ExecutionConflict("revision_not_found", "source revision is missing")

            child_id = child_execution_id or f"exec_{uuid.uuid4().hex}"
            now = time.time()
            child = ExecutionRecord(
                execution_id=child_id,
                run_id=source.run_id,
                session_id=source.session_id,
                revision_id=revision.revision_id,
                parent_execution_id=source.execution_id,
                source_checkpoint_id=checkpoint.checkpoint_id,
                status=ExecutionStatus.QUEUED,
                status_version=1,
                capabilities=source.capabilities,
                created_at=now,
                updated_at=now,
            )
            try:
                self.executions._insert_execution(connection, child)
            except sqlite3.IntegrityError as exc:
                raise ExecutionConflict("execution_exists", f"execution already exists: {child_id}") from exc
            self.executions._copy_execution_input_in_transaction(
                connection,
                source_execution_id=source.execution_id,
                child_execution_id=child.execution_id,
                created_at=now,
            )
            self.executions._append_event(
                connection,
                execution_id=child.execution_id,
                execution_version=child.status_version,
                kind="execution.created",
                payload={"record": child.to_dict()},
                created_at=now,
            )
            self.executions._append_event(
                connection,
                execution_id=source.execution_id,
                execution_version=source.status_version,
                command_id=command_id,
                kind="execution.branch.created",
                payload={
                    "child_execution_id": child.execution_id,
                    "revision_id": revision.revision_id,
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "kind": kind.value,
                },
                created_at=now,
            )
            command = self.executions._transition_command(
                connection,
                command_id,
                expected_status=CommandStatus.APPLYING,
                target=CommandStatus.APPLIED,
                result_version=source.status_version,
                result_json={
                    "child_execution_id": child.execution_id,
                    "revision_id": revision.revision_id,
                    "checkpoint_id": checkpoint.checkpoint_id,
                },
            )
            return BranchCompletion(command, source, child, revision, checkpoint)

    @staticmethod
    def _validate_compatible_prefix(
        prefix: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] | None,
    ) -> tuple[tuple[str, str], ...]:
        if prefix is None:
            raise ExecutionConflict("compatible_prefix_required", "compatible prefix is required")
        values: list[tuple[str, str]] = []
        for item in prefix:
            if not isinstance(item, Mapping):
                raise ExecutionConflict(
                    "invalid_compatible_prefix",
                    "prefix entries must be mappings",
                )
            if set(item) != {"step_id", "contract_hash"}:
                raise ExecutionConflict("invalid_compatible_prefix", "prefix entries must contain step_id and contract_hash")
            step_id = item["step_id"]
            contract_hash = item["contract_hash"]
            if not isinstance(step_id, str) or not isinstance(contract_hash, str):
                raise ExecutionConflict("invalid_compatible_prefix", "prefix fields must be strings")
            values.append((step_id, contract_hash))
        if len({step_id for step_id, _ in values}) != len(values):
            raise ExecutionConflict("invalid_compatible_prefix", "compatible prefix contains duplicate steps")
        if values != sorted(values):
            raise ExecutionConflict("invalid_compatible_prefix", "compatible prefix must be sorted")
        return tuple(values)

    def _resume_transaction(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: Mapping[str, Any],
        kind: CommandKind,
        owner_id: str,
        ttl_seconds: float,
    ) -> tuple[
        ControlCommand,
        ExecutionRecord,
        AttemptRecord | None,
        CheckpointManifest | None,
        tuple[Mapping[str, Any], ...],
        bool,
    ]:
        if not owner_id or ttl_seconds <= 0:
            raise AttemptConflict("invalid_lease", "owner_id and a positive ttl_seconds are required")
        with self.executions._transaction() as connection:
            command, duplicate = self.executions._accept_command(
                connection,
                command_id=command_id,
                execution_id=execution_id,
                expected_version=expected_version,
                kind=kind,
                payload={},
                actor=actor,
            )
            execution = self.executions._require_execution(connection, execution_id)
            if duplicate:
                checkpoint = (
                    self.checkpoints._get(
                        connection,
                        execution.checkpoint_head_id or execution.source_checkpoint_id,
                    )
                    if (execution.checkpoint_head_id or execution.source_checkpoint_id)
                    else None
                )
                return command, execution, None, checkpoint, (), True
            if execution.current_attempt_id is not None:
                raise AttemptConflict(
                    "owner_exists",
                    "execution already has a current attempt",
                )
            checkpoint = (
                self.checkpoints._get(
                    connection,
                    execution.checkpoint_head_id or execution.source_checkpoint_id,
                )
                if (execution.checkpoint_head_id or execution.source_checkpoint_id)
                else None
            )
            if checkpoint is None:
                raise ExecutionConflict("checkpoint_required", "a published checkpoint is required")
            if checkpoint.execution_id != execution_id or checkpoint.revision_id != execution.revision_id:
                raise ExecutionConflict("invalid_checkpoint", "checkpoint does not belong to the current execution revision")
            steering_rows = connection.execute(
                "SELECT * FROM commands WHERE execution_id = ? AND kind = ? "
                "AND status IN (?, ?) ORDER BY submitted_at, command_id",
                (
                    execution_id,
                    CommandKind.STEER.value,
                    CommandStatus.ACCEPTED.value,
                    CommandStatus.APPLYING.value,
                ),
            ).fetchall()
            steer_inputs = tuple(
                {
                    "command_id": str(row["command_id"]),
                    "payload": dict(self.executions._command(row).payload),
                }
                for row in steering_rows
            )
            unresolved = connection.execute(
                "SELECT effect_id FROM effects WHERE execution_id = ? "
                "AND status IN ('dispatched', 'uncertain') LIMIT 1",
                (execution_id,),
            ).fetchone()
            if unresolved is not None:
                raise ExecutionConflict(
                    "unresolved_effect", f"effect requires resolution: {unresolved['effect_id']}"
                )
            generation = int(
                connection.execute(
                    "SELECT COALESCE(MAX(generation), 0) + 1 FROM attempts WHERE execution_id = ?",
                    (execution_id,),
                ).fetchone()[0]
            )
            now = self.attempts._clock()
            attempt = AttemptRecord(
                attempt_id=f"attempt_{uuid.uuid4().hex}",
                execution_id=execution_id,
                generation=generation,
                status=AttemptStatus.LEASED,
                owner_id=owner_id,
                lease_expires_at=now + ttl_seconds,
                leased_at=now,
                updated_at=now,
            )
            self.attempts._insert(connection, attempt)
            connection.execute(
                "UPDATE executions SET current_attempt_id = ?, owner_lease_json = ?, updated_at = ? "
                "WHERE execution_id = ? AND status_version = ?",
                (
                    attempt.attempt_id,
                    _json({"owner_id": owner_id, "generation": generation}),
                    now,
                    execution_id,
                    expected_version,
                ),
            )
            reserved = self.executions._transition_execution(
                connection,
                execution_id,
                expected_version=expected_version,
                target=ExecutionStatus.RUNNING,
                reason_code=None,
            )
            self.executions._append_event(
                connection,
                execution_id=execution_id,
                execution_version=reserved.status_version,
                kind="attempt.leased",
                payload={"attempt": attempt.to_dict()},
                created_at=now,
            )
            connection.execute(
                "UPDATE attempts SET status = ?, activated_at = ?, updated_at = ? WHERE attempt_id = ?",
                (AttemptStatus.ACTIVE.value, now, now, attempt.attempt_id),
            )
            active = self.attempts._require(connection, attempt.attempt_id)
            self.executions._append_event(
                connection,
                execution_id=execution_id,
                execution_version=reserved.status_version,
                kind="attempt.active",
                payload={"attempt": active.to_dict()},
                created_at=now,
            )
            applying = self.executions._transition_command(
                connection,
                command_id,
                expected_status=CommandStatus.ACCEPTED,
                target=CommandStatus.APPLYING,
            )
            command = applying
            return command, reserved, active, checkpoint, steer_inputs, False

    def _finish_activation(
        self,
        attempt: AttemptRecord | None,
        command: ControlCommand,
        *,
        delivered: bool,
        issue_code: str | None,
    ) -> tuple[ControlCommand, ExecutionRecord]:
        """Close the activation race while holding the execution write lock."""
        if attempt is None:
            raise AttemptConflict("activation_failed", "activation has no attempt")
        reason = issue_code or "activation_failed"
        unbind = False
        with self.executions._transaction() as connection:
            current_attempt = self.attempts._require(connection, attempt.attempt_id)
            self.attempts._validate_generation(current_attempt, attempt.generation)
            execution = self.executions._require_execution(connection, attempt.execution_id)
            current_command = self.executions._get_command(connection, command.command_id)
            if current_command is None:
                raise AttemptConflict("activation_failed", "activation command disappeared")
            if current_command.execution_id != execution.execution_id:
                raise AttemptConflict(
                    "command_mismatch",
                    "activation command belongs to another execution",
                )
            if current_command.status is CommandStatus.APPLIED:
                return current_command, execution
            if (
                current_command.status is CommandStatus.REJECTED
                and execution.status in TERMINAL_EXECUTION_STATUSES
            ):
                return current_command, execution
            if current_command.status is CommandStatus.APPLYING and delivered:
                if current_command.kind is CommandKind.CONTINUE:
                    current_command = self.executions._transition_command(
                        connection,
                        current_command.command_id,
                        expected_status=CommandStatus.APPLYING,
                        target=CommandStatus.APPLIED,
                        result_version=execution.status_version,
                    )
                return current_command, execution

            cancel = self._applying_command(
                connection, execution.execution_id, CommandKind.CANCEL
            )
            pause = self._applying_command(
                connection, execution.execution_id, CommandKind.PAUSE
            )
            unresolved = connection.execute(
                "SELECT 1 FROM effects WHERE execution_id = ? "
                "AND status IN ('dispatched', 'uncertain') LIMIT 1",
                (execution.execution_id,),
            ).fetchone() is not None
            if cancel is not None:
                target = (
                    ExecutionStatus.RECONCILIATION_REQUIRED
                    if unresolved
                    else ExecutionStatus.CANCELLED
                )
                target_reason = "effect_reconciliation" if unresolved else "cancelled_during_activation"
                outcome = "reconciliation_required" if unresolved else "cancelled_during_activation"
            elif pause is not None:
                target = ExecutionStatus.PAUSED
                target_reason = "pause_during_activation"
                outcome = "pause_during_activation"
            else:
                target = ExecutionStatus.PAUSED
                target_reason = reason
                outcome = reason

            if execution.status is ExecutionStatus.RUNNING and target is ExecutionStatus.PAUSED:
                pausing = self.executions._transition_execution(
                    connection,
                    execution.execution_id,
                    expected_version=execution.status_version,
                    target=ExecutionStatus.PAUSING,
                    reason_code=target_reason,
                )
                execution = self.executions._transition_execution(
                    connection,
                    execution.execution_id,
                    expected_version=pausing.status_version,
                    target=ExecutionStatus.PAUSED,
                    reason_code=target_reason,
                    clear_owner=True,
                )
            elif execution.status in {
                ExecutionStatus.PAUSING,
                ExecutionStatus.CANCELLING,
            }:
                execution = self.executions._transition_execution(
                    connection,
                    execution.execution_id,
                    expected_version=execution.status_version,
                    target=target,
                    reason_code=target_reason,
                    clear_owner=True,
                )
            else:
                raise AttemptConflict(
                    "activation_failed",
                    f"cannot settle activation while execution is {execution.status.value}",
                )
            ended = self.attempts._end_for_owner_loss(
                connection, current_attempt, outcome=outcome
            )
            self.executions._append_event(
                connection,
                execution_id=execution.execution_id,
                execution_version=execution.status_version,
                kind="attempt.ended",
                payload={"attempt": ended.to_dict()},
                created_at=ended.updated_at,
            )
            if current_command.status is CommandStatus.APPLYING:
                current_command = self.executions._transition_command(
                    connection,
                    current_command.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.REJECTED,
                    result_version=execution.status_version,
                    rejection_code=reason,
                )
            if cancel is not None and not unresolved:
                self.executions._transition_command(
                    connection,
                    cancel.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=execution.status_version,
                )
            elif pause is not None:
                self.executions._transition_command(
                    connection,
                    pause.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=execution.status_version,
                )
            unbind = True
        if unbind:
            self.registry.unbind(
                attempt.execution_id,
                attempt_id=attempt.attempt_id,
                generation=attempt.generation,
            )
        return current_command, execution

    async def _activate(
        self,
        attempt: AttemptRecord | None,
        checkpoint: CheckpointManifest | None,
        steer_inputs: tuple[Mapping[str, Any], ...],
        *,
        activator: Activator | None,
        driver: Any | None = None,
    ) -> tuple[bool, str | None]:
        if attempt is None:
            return False, None
        if checkpoint is None and attempt.execution_id:
            child_execution = self.executions.get_execution(attempt.execution_id)
            if child_execution is not None:
                checkpoint_id = (
                    child_execution.checkpoint_head_id
                    or child_execution.source_checkpoint_id
                )
                if checkpoint_id is not None:
                    checkpoint = self.checkpoints.get(checkpoint_id)
        callback = activator or self.activator
        if callback is None and driver is not None:
            callback = driver.activate
        if callback is None:
            return True, None
        try:
            result = callback(
                attempt,
                ActivationInput(checkpoint=checkpoint, steer_inputs=steer_inputs),
            )
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, DriverBinding):
                if (
                    result.execution_id != attempt.execution_id
                    or result.attempt_id != attempt.attempt_id
                    or result.generation != attempt.generation
                ):
                    raise DriverRegistryConflict(
                        "invalid_binding",
                        "activation returned a binding for a different attempt",
                    )
                self._bind_driver(result)
            elif isinstance(result, tuple) and len(result) == 2:
                driver, handle = result
                self._bind_driver(
                    DriverBinding(
                        execution_id=attempt.execution_id,
                        attempt_id=attempt.attempt_id,
                        generation=attempt.generation,
                        driver=driver,
                        handle=handle,
                    )
                )
            elif driver is not None:
                self._bind_driver(
                    DriverBinding(
                        execution_id=attempt.execution_id,
                        attempt_id=attempt.attempt_id,
                        generation=attempt.generation,
                        driver=driver,
                        handle=result,
                    )
                )
            return True, None
        except Exception:
            return False, "activation_failed"

    def _bind_driver(self, binding: DriverBinding[Any]) -> None:
        """Commit driver-local activation only after durable registry fencing."""
        committed = getattr(binding.driver, "activation_committed", None)
        try:
            self.registry.bind(
                binding,
                on_bound=(
                    (lambda: committed(binding))
                    if callable(committed)
                    else None
                ),
            )
        except Exception:
            aborted = getattr(binding.driver, "activation_aborted", None)
            if callable(aborted):
                aborted(binding)
            raise

    def _durable_owner(self, execution_id: str) -> tuple[str, int] | None:
        execution = self.executions.get_execution(execution_id)
        if execution is None or execution.current_attempt_id is None:
            return None
        generation = execution.owner_lease.get("generation")
        if not isinstance(generation, int):
            return None
        return execution.current_attempt_id, generation

    async def request_pause(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: Mapping[str, Any],
    ) -> ControlDispatch:
        current = self.executions.get_execution(execution_id)
        if current is None:
            raise ExecutionConflict("not_found", f"execution not found: {execution_id}")
        immediate = (
            current.status is ExecutionStatus.QUEUED
            and current.current_attempt_id is None
        )
        command, execution, duplicate = self.executions.accept_command_with_transition(
            command_id=command_id,
            execution_id=execution_id,
            expected_version=expected_version,
            kind=CommandKind.PAUSE,
            target=(ExecutionStatus.PAUSED if immediate else ExecutionStatus.PAUSING),
            payload={},
            actor=actor,
            supersede_kinds=_PAUSE_SUPERSEDES,
            supersede_code="superseded_by_pause",
            apply_command=immediate,
        )
        if duplicate:
            return ControlDispatch(
                command=command, execution=execution, delivered=False
            )
        if execution.status is ExecutionStatus.PAUSED:
            return ControlDispatch(
                command=command, execution=execution, delivered=False
            )
        return await self._dispatch(command, execution, operation="pause")

    async def request_cancel(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: Mapping[str, Any],
        reason_code: str,
    ) -> ControlDispatch:
        current = self.executions.get_execution(execution_id)
        if current is None:
            raise ExecutionConflict("not_found", f"execution not found: {execution_id}")
        prepared_before_accept = False
        terminal_candidate = (
            current.status is ExecutionStatus.QUEUED
            and current.current_attempt_id is None
            and not self.effects.list_unresolved(execution_id)
        )
        if terminal_candidate and self._terminal_preparer is not None:
            try:
                prepared = self._terminal_preparer(current, command_id)
                if prepared is False:
                    raise RuntimeError("terminal dispatch barrier unavailable")
            except Exception as exc:
                self._record_terminal_recovery(current, command_id)
                raise ProjectionRecoveryRequired(execution_id) from exc
            prepared_before_accept = True
        try:
            command, execution, duplicate = self.executions.accept_command_with_transition(
                command_id=command_id,
                execution_id=execution_id,
                expected_version=expected_version,
                kind=CommandKind.CANCEL,
                target=ExecutionStatus.CANCELLING,
                payload={"reason_code": reason_code},
                actor=actor,
                reason_code=reason_code,
                supersede_kinds=_CANCEL_SUPERSEDES,
                supersede_code="superseded_by_cancel",
            )
        except CommandConflict as exc:
            # Every transport uses the same command identity.  If another
            # cancellation won the race, adopt its durable payload/actor and
            # retry as the same idempotent command; the first reason remains
            # authoritative instead of becoming an idempotency error.
            existing = self.executions.get_command(command_id)
            if (
                existing is None
                or existing.execution_id != execution_id
                or existing.kind is not CommandKind.CANCEL
                or getattr(exc, "code", None) != "idempotency_collision"
            ):
                if prepared_before_accept:
                    self._record_terminal_recovery(current, command_id)
                    raise ProjectionRecoveryRequired(execution_id) from exc
                raise
            try:
                command, execution, duplicate = self.executions.accept_command_with_transition(
                    command_id=command_id,
                    execution_id=execution_id,
                    expected_version=existing.expected_version,
                    kind=CommandKind.CANCEL,
                    target=ExecutionStatus.CANCELLING,
                    payload=existing.payload,
                    actor=existing.actor,
                    reason_code=existing.payload.get("reason_code") or reason_code,
                    supersede_kinds=_CANCEL_SUPERSEDES,
                    supersede_code="superseded_by_cancel",
                )
            except CommandConflict as retry_exc:
                if prepared_before_accept:
                    self._record_terminal_recovery(current, command_id)
                    raise ProjectionRecoveryRequired(execution_id) from retry_exc
                raise
        except Exception as exc:
            if prepared_before_accept:
                self._record_terminal_recovery(current, command_id)
                raise ProjectionRecoveryRequired(execution_id) from exc
            raise
        if duplicate:
            return ControlDispatch(
                command=command, execution=execution, delivered=False
            )
        if execution.current_attempt_id is None and not self.effects.list_unresolved(
            execution.execution_id
        ):
            execution = self.executions.transition_execution(
                execution.execution_id,
                expected_version=execution.status_version,
                target=ExecutionStatus.CANCELLED,
                reason_code=reason_code,
            )
            try:
                self._observe_terminal(execution)
            except Exception as exc:
                try:
                    command = self.executions.transition_command(
                        command.command_id,
                        expected_status=CommandStatus.APPLYING,
                        target=CommandStatus.REJECTED,
                        result_version=execution.status_version,
                        rejection_code=ProjectionRecoveryRequired.code,
                        receipt={"error": str(exc)},
                    )
                except Exception:
                    _log.exception(
                        "failed to persist projection recovery command state for %s",
                        execution.execution_id,
                    )
                raise ProjectionRecoveryRequired(
                    execution.execution_id,
                ) from exc
            command = self._mark_applied(command, execution)
            return ControlDispatch(
                command=command, execution=execution, delivered=False
            )
        dispatch = await self._dispatch(command, execution, operation="cancel")
        if dispatch.delivered:
            self._remember_cancel_delivery(
                execution_id, command.command_id,
            )
        return dispatch

    async def deliver_pending_cancel(
        self,
        *,
        execution_id: str,
        attempt_id: str,
        generation: int,
    ) -> ControlDispatch | None:
        """Deliver a persisted cancel command to this exact local owner."""
        execution = self.executions.get_execution(execution_id)
        if execution is None:
            return None
        if execution.status in TERMINAL_EXECUTION_STATUSES:
            self._reconcile_terminal_cancel(execution)
            self._forget_cancel_delivery(execution_id)
            return None
        self._prune_cancel_delivery(execution_id)
        pending = self.executions.list_commands(
            execution_id,
            statuses=(CommandStatus.APPLYING,),
            kinds=(CommandKind.CANCEL,),
        )
        command = pending[0] if pending else None
        if command is None or (
            command.kind is not CommandKind.CANCEL
            or command.status is not CommandStatus.APPLYING
        ):
            if (
                execution.status in TERMINAL_EXECUTION_STATUSES
                or command is not None and command.status in TERMINAL_COMMAND_STATUSES
            ):
                self._forget_cancel_delivery(
                    execution_id,
                    command.command_id if command is not None else None,
                )
            return None
        if (
            execution.current_attempt_id != attempt_id
            or execution.owner_lease.get("generation") != generation
        ):
            return ControlDispatch(
                command=command,
                execution=execution,
                delivered=False,
                issue_code="stale_owner",
            )
        with self._cancel_delivery_lock:
            if command.command_id in self._delivered_cancel_commands:
                return ControlDispatch(
                    command=command,
                    execution=execution,
                    delivered=False,
                    issue_code="already_delivered",
                )
            dispatch = await self._dispatch(
                command, execution, operation="cancel",
            )
            if dispatch.delivered:
                self._remember_cancel_delivery(
                    execution_id, command.command_id,
                )
            return dispatch

    def _record_terminal_recovery(
        self, execution: ExecutionRecord, command_id: str,
    ) -> None:
        recovery = self._terminal_recovery
        if recovery is None:
            return
        try:
            recovery(execution, command_id)
        except Exception:
            _log.exception(
                "failed to persist terminal barrier recovery for %s",
                execution.execution_id,
            )

    async def terminate_attempt(
        self,
        *,
        execution_id: str,
        attempt_id: str,
        generation: int,
        reason: str,
    ):
        """Invoke exact driver's termination hook after cancellation grace."""
        execution = self.executions.get_execution(execution_id)
        if execution is None or (
            execution.current_attempt_id != attempt_id
            or execution.owner_lease.get("generation") != generation
        ):
            raise AttemptConflict("stale_owner", "termination owner is stale")
        binding = self.registry.resolve(
            execution_id, attempt_id=attempt_id, generation=generation,
        )
        return await binding.driver.terminate(binding.handle, reason)

    def arrive_safe_point(
        self,
        *,
        attempt_id: str,
        generation: int,
        command_id: str,
        expected_execution_version: int,
        fragment: CheckpointFragment,
    ) -> SafePointCompletion:
        attempt = self.attempts.get(attempt_id)
        if attempt is None:
            raise AttemptConflict("not_found", f"attempt not found: {attempt_id}")
        if attempt.generation != generation:
            raise AttemptConflict(
                "stale_generation",
                f"expected attempt generation {generation}, found {attempt.generation}",
            )
        execution = self.executions.get_execution(attempt.execution_id)
        if execution is None:
            raise AttemptConflict("execution_not_found", "attempt execution is missing")
        command = self.executions.get_command(command_id)
        if command is None:
            raise AttemptConflict("command_not_found", f"safe point command not found: {command_id}")
        if command.kind not in {
            CommandKind.STEP,
            CommandKind.PAUSE,
            CommandKind.STEER,
        }:
            raise AttemptConflict(
                "unsupported_command",
                f"safe point cannot consume {command.kind.value}",
            )
        cancelled = self._arrive_superseded_safe_point(
            attempt_id=attempt_id,
            generation=generation,
            command_id=command_id,
        )
        if cancelled is not None:
            return cancelled
        if command.kind is CommandKind.STEP:
            return self._arrive_step_safe_point(
                attempt_id=attempt_id,
                generation=generation,
                command_id=command_id,
                expected_execution_version=expected_execution_version,
                fragment=fragment,
            )
        if command is not None and command.kind is CommandKind.PAUSE:
            return self._arrive_pause_safe_point(
                attempt_id=attempt_id,
                generation=generation,
                command_id=command_id,
                expected_execution_version=expected_execution_version,
                fragment=fragment,
            )
        if command is not None and command.kind is CommandKind.STEER:
            return self._arrive_steer_safe_point(
                attempt_id=attempt_id,
                generation=generation,
                command_id=command_id,
                expected_execution_version=expected_execution_version,
                fragment=fragment,
            )
        raise AttemptConflict(
            "unsupported_command",
            f"safe point cannot consume {command.kind.value}",
        )

    def _arrive_superseded_safe_point(
        self,
        *,
        attempt_id: str,
        generation: int,
        command_id: str,
    ) -> SafePointCompletion | None:
        """Reconcile a late lower-priority report against cancellation."""
        completion = None
        unbind = False
        with self.executions._transaction() as connection:
            attempt = self.attempts._require(connection, attempt_id)
            self.attempts._validate_generation(attempt, generation)
            execution = self.executions._require_execution(connection, attempt.execution_id)
            command = self.executions._get_command(connection, command_id)
            if command is None:
                raise AttemptConflict("command_not_found", f"safe point command not found: {command_id}")
            if command.execution_id != execution.execution_id:
                raise AttemptConflict(
                    "command_mismatch",
                    "safe point command belongs to another execution",
                )
            cancel = self._applying_command(connection, execution.execution_id, CommandKind.CANCEL)
            if cancel is None:
                if not (
                    execution.status is ExecutionStatus.CANCELLED
                    and command.status in {
                        CommandStatus.APPLIED,
                        CommandStatus.REJECTED,
                    }
                ):
                    return None
                checkpoint = (
                    self.checkpoints._get(connection, execution.checkpoint_head_id)
                    if execution.checkpoint_head_id
                    else None
                )
                return SafePointCompletion(
                    command=command,
                    execution=execution,
                    attempt=attempt,
                    checkpoint=checkpoint,
                )
            if execution.status is not ExecutionStatus.CANCELLING:
                raise AttemptConflict(
                    "invalid_state",
                    f"applying cancel cannot finish execution in {execution.status.value}",
                )
            unresolved = connection.execute(
                "SELECT 1 FROM effects WHERE execution_id = ? "
                "AND status IN ('dispatched', 'uncertain') LIMIT 1",
                (execution.execution_id,),
            ).fetchone() is not None
            target = (
                ExecutionStatus.RECONCILIATION_REQUIRED
                if unresolved
                else ExecutionStatus.CANCELLED
            )
            outcome = "reconciliation_required" if unresolved else "cancelled_at_safe_point"
            reason_code = "effect_reconciliation" if unresolved else cancel.payload.get("reason_code")
            ended, cancelled = self.attempts._finish_in_transaction(
                connection,
                attempt_id,
                generation=generation,
                expected_execution_version=execution.status_version,
                target=target,
                outcome=outcome,
                reason_code=reason_code,
            )
            command = self.executions._get_command(connection, command_id)
            if command is None:
                raise AttemptConflict("command_not_found", f"safe point command not found: {command_id}")
            if command.status in {CommandStatus.ACCEPTED, CommandStatus.APPLYING}:
                command = self.executions._transition_command(
                    connection,
                    command_id,
                    expected_status=command.status,
                    target=CommandStatus.REJECTED,
                    result_version=cancelled.status_version,
                    rejection_code="superseded_by_cancel",
                )
            if not unresolved:
                cancel = self.executions._transition_command(
                    connection,
                    cancel.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=cancelled.status_version,
                )
            checkpoint = (
                self.checkpoints._get(connection, cancelled.checkpoint_head_id)
                if cancelled.checkpoint_head_id
                else None
            )
            completion = SafePointCompletion(
                command=command,
                execution=cancelled,
                attempt=ended,
                checkpoint=checkpoint,
            )
            unbind = True
        if unbind:
            self.registry.unbind(
                execution.execution_id,
                attempt_id=attempt_id,
                generation=generation,
            )
        return completion
    def _arrive_pause_safe_point(
        self,
        *,
        attempt_id: str,
        generation: int,
        command_id: str,
        expected_execution_version: int,
        fragment: CheckpointFragment,
    ) -> SafePointCompletion:
        """Commit checkpoint, steering receipts, and pause in one transaction."""
        with self.executions._transaction() as connection:
            attempt = self.attempts._require(connection, attempt_id)
            self.attempts._validate_generation(attempt, generation)
            execution = self.executions._require_execution(connection, attempt.execution_id)
            command = self.executions._get_command(connection, command_id)
            if command is not None and command.execution_id != execution.execution_id:
                raise AttemptConflict("command_mismatch", "safe point command belongs to another execution")
            if command is not None and command.status is CommandStatus.APPLIED:
                checkpoint = (
                    self.checkpoints._get(connection, execution.checkpoint_head_id)
                    if execution.checkpoint_head_id
                    else None
                )
                return SafePointCompletion(
                    command=command,
                    execution=execution,
                    attempt=attempt,
                    checkpoint=checkpoint,
                )
            if (
                execution.status_version != expected_execution_version
                or command is None
                or command.execution_id != execution.execution_id
                or command.kind is not CommandKind.PAUSE
                or command.status is not CommandStatus.APPLYING
            ):
                raise AttemptConflict("command_mismatch", "safe point does not match an applying pause command")
            if fragment.safe_point_kind not in execution.capabilities.safe_point_kinds:
                raise AttemptConflict("unsupported_safe_point", "driver reported an undeclared safe point")
            steering_commands = [
                self.executions._command(row)
                for row in connection.execute(
                    "SELECT * FROM commands WHERE execution_id = ? AND kind = ? "
                    "AND status IN (?, ?) ORDER BY submitted_at, command_id",
                    (
                        execution.execution_id,
                        CommandKind.STEER.value,
                        CommandStatus.ACCEPTED.value,
                        CommandStatus.APPLYING.value,
                    ),
                ).fetchall()
            ]
            state_refs = dict(fragment.state_refs)
            if steering_commands:
                steering = list(state_refs.get("steering", ()))
                steering.extend(
                    {"command_id": item.command_id, "payload": dict(item.payload)}
                    for item in steering_commands
                )
                state_refs["steering"] = steering
            steering_ids = {item.command_id for item in steering_commands}
            pending = tuple(
                item
                for item in dict.fromkeys(fragment.pending_command_ids)
                if item != command_id and item not in steering_ids
            )
            checkpoint, checkpointed = self.checkpoints._publish_in_transaction(
                connection,
                execution_id=execution.execution_id,
                expected_version=expected_execution_version,
                revision_id=execution.revision_id,
                parent_checkpoint_id=execution.checkpoint_head_id,
                frontier=fragment.frontier,
                completed_frontier=fragment.completed_frontier,
                state_refs=state_refs,
                completed_actions=fragment.completed_actions,
                effect_receipts=fragment.effect_receipts,
                child_frontier=fragment.child_frontier,
                pending_command_ids=pending,
                created_by_attempt_id=attempt_id,
            )
            ended, paused = self.attempts._finish_in_transaction(
                connection,
                attempt_id,
                generation=generation,
                expected_execution_version=checkpointed.status_version,
                target=ExecutionStatus.PAUSED,
                outcome="paused_at_safe_point",
            )
            safe_point = _thaw_json(checkpoint.frontier[-1]) if checkpoint.frontier else {
                "kind": fragment.safe_point_kind
            }
            receipt = {"checkpoint_id": checkpoint.checkpoint_id, "safe_point": safe_point}
            command = self.executions._transition_command(
                connection,
                command_id,
                expected_status=CommandStatus.APPLYING,
                target=CommandStatus.APPLIED,
                result_version=paused.status_version,
                receipt=receipt,
            )
            applied = [command]
            for steer in steering_commands:
                if steer.status is CommandStatus.ACCEPTED:
                    steer = self.executions._transition_command(
                        connection,
                        steer.command_id,
                        expected_status=CommandStatus.ACCEPTED,
                        target=CommandStatus.APPLYING,
                    )
                steer = self.executions._transition_command(
                    connection,
                    steer.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=paused.status_version,
                    receipt=receipt,
                )
                applied.append(steer)
        self.registry.unbind(
            execution.execution_id,
            attempt_id=attempt_id,
            generation=generation,
        )
        return SafePointCompletion(
            command=command,
            execution=paused,
            attempt=ended,
            checkpoint=checkpoint,
            applied_commands=tuple(applied),
        )

    def _arrive_steer_safe_point(
        self,
        *,
        attempt_id: str,
        generation: int,
        command_id: str,
        expected_execution_version: int,
        fragment: CheckpointFragment,
    ) -> SafePointCompletion:
        """Apply running steering at a safe point, with optional pause closeout."""
        unbind = False
        with self.executions._transaction() as connection:
            attempt = self.attempts._require(connection, attempt_id)
            self.attempts._validate_generation(attempt, generation)
            execution = self.executions._require_execution(connection, attempt.execution_id)
            command = self.executions._get_command(connection, command_id)
            if command is None or command.execution_id != execution.execution_id:
                raise AttemptConflict("command_mismatch", "safe point does not match this execution")
            if command.status is CommandStatus.APPLIED:
                self.attempts._validate_lease(attempt, self.attempts._clock())
                self.attempts._validate_owner(
                    execution, attempt, expected_execution_version
                )
                checkpoint = (
                    self.checkpoints._get(connection, execution.checkpoint_head_id)
                    if execution.checkpoint_head_id
                    else None
                )
                return SafePointCompletion(
                    command=command,
                    execution=execution,
                    attempt=attempt,
                    checkpoint=checkpoint,
                )
            if command.kind is not CommandKind.STEER or command.status not in {
                CommandStatus.ACCEPTED,
                CommandStatus.APPLYING,
            }:
                raise AttemptConflict("command_mismatch", "safe point does not match an unfinished steer command")
            if execution.status_version != expected_execution_version:
                raise AttemptConflict(
                    "stale_version",
                    f"expected execution version {expected_execution_version}, found {execution.status_version}",
                )
            if fragment.safe_point_kind not in execution.capabilities.safe_point_kinds:
                raise AttemptConflict("unsupported_safe_point", "driver reported an undeclared safe point")
            if self._applying_command(connection, execution.execution_id, CommandKind.CANCEL) is not None:
                raise AttemptConflict("superseded_by_cancel", "cancel has priority over steer")
            if self._applying_command(connection, execution.execution_id, CommandKind.STEP) is not None:
                raise AttemptConflict("superseded_by_step", "step has priority over steer")
            pause = self._applying_command(connection, execution.execution_id, CommandKind.PAUSE)
            if execution.status is ExecutionStatus.PAUSING and pause is None:
                raise AttemptConflict("invalid_state", "pausing execution has no applying pause command")
            if execution.status not in {ExecutionStatus.RUNNING, ExecutionStatus.PAUSING}:
                raise AttemptConflict("invalid_state", f"steer safe point arrived while execution is {execution.status.value}")
            steering_commands = [
                self.executions._command(row)
                for row in connection.execute(
                    "SELECT * FROM commands WHERE execution_id = ? AND kind = ? "
                    "AND status IN (?, ?) ORDER BY submitted_at, command_id",
                    (
                        execution.execution_id,
                        CommandKind.STEER.value,
                        CommandStatus.ACCEPTED.value,
                        CommandStatus.APPLYING.value,
                    ),
                ).fetchall()
            ]
            state_refs = dict(fragment.state_refs)
            steering = list(state_refs.get("steering", ()))
            steering.extend(
                {"command_id": item.command_id, "payload": dict(item.payload)}
                for item in steering_commands
            )
            state_refs["steering"] = steering
            steering_ids = {item.command_id for item in steering_commands}
            pending = tuple(
                item
                for item in dict.fromkeys(fragment.pending_command_ids)
                if item not in steering_ids
            )
            checkpoint, checkpointed = self.checkpoints._publish_in_transaction(
                connection,
                execution_id=execution.execution_id,
                expected_version=expected_execution_version,
                revision_id=execution.revision_id,
                parent_checkpoint_id=execution.checkpoint_head_id,
                frontier=fragment.frontier,
                completed_frontier=fragment.completed_frontier,
                state_refs=state_refs,
                completed_actions=fragment.completed_actions,
                effect_receipts=fragment.effect_receipts,
                child_frontier=fragment.child_frontier,
                pending_command_ids=pending,
                created_by_attempt_id=attempt_id,
            )
            current_attempt = attempt
            result_execution = checkpointed
            if pause is not None:
                current_attempt, result_execution = self.attempts._finish_in_transaction(
                    connection,
                    attempt_id,
                    generation=generation,
                    expected_execution_version=checkpointed.status_version,
                    target=ExecutionStatus.PAUSED,
                    outcome="paused_at_safe_point",
                )
                unbind = True
            safe_point = _thaw_json(checkpoint.frontier[-1]) if checkpoint.frontier else {"kind": fragment.safe_point_kind}
            receipt = {"checkpoint_id": checkpoint.checkpoint_id, "safe_point": safe_point}
            applied = []
            if pause is not None:
                applied.append(
                    self.executions._transition_command(
                        connection,
                        pause.command_id,
                        expected_status=CommandStatus.APPLYING,
                        target=CommandStatus.APPLIED,
                        result_version=result_execution.status_version,
                        receipt=receipt,
                    )
                )
            for steer in steering_commands:
                if steer.status is CommandStatus.ACCEPTED:
                    steer = self.executions._transition_command(
                        connection,
                        steer.command_id,
                        expected_status=CommandStatus.ACCEPTED,
                        target=CommandStatus.APPLYING,
                    )
                steer = self.executions._transition_command(
                    connection,
                    steer.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=result_execution.status_version,
                    receipt=receipt,
                )
                applied.append(steer)
            command = next(item for item in applied if item.command_id == command_id)
        if unbind:
            self.registry.unbind(execution.execution_id, attempt_id=attempt_id, generation=generation)
        return SafePointCompletion(
            command=command,
            execution=result_execution,
            attempt=current_attempt,
            checkpoint=checkpoint,
            applied_commands=tuple(applied),
        )

    def arrive_step_safe_point(
        self,
        *,
        attempt_id: str,
        generation: int,
        command_id: str,
        expected_execution_version: int,
        fragment: CheckpointFragment | None = None,
        safe_point_kind: str = "control.step",
        frontier: tuple[Mapping[str, Any], ...] = (),
        state_refs: Mapping[str, Any] | None = None,
        managed_action: Mapping[str, Any] | None = None,
        control_step: Mapping[str, Any] | None = None,
    ) -> SafePointCompletion:
        """Report one step unit and atomically return the execution to paused."""
        if fragment is None:
            fragment = CheckpointFragment(
                safe_point_kind=safe_point_kind,
                frontier=frontier or ({"safe_point": safe_point_kind},),
                state_refs=state_refs or {},
                managed_action=managed_action,
                control_step=control_step,
            )
        elif managed_action is not None or control_step is not None:
            fragment = replace(
                fragment,
                managed_action=managed_action,
                control_step=control_step,
            )
        return self._arrive_step_safe_point(
            attempt_id=attempt_id,
            generation=generation,
            command_id=command_id,
            expected_execution_version=expected_execution_version,
            fragment=fragment,
        )

    def _arrive_step_safe_point(
        self,
        *,
        attempt_id: str,
        generation: int,
        command_id: str,
        expected_execution_version: int,
        fragment: CheckpointFragment,
    ) -> SafePointCompletion:
        if (fragment.managed_action is None) == (fragment.control_step is None):
            raise AttemptConflict(
                "invalid_step_unit",
                "step safe point must report exactly one managed_action or control_step",
            )
        if fragment.safe_point_kind == "" or fragment.safe_point_kind is None:
            raise AttemptConflict("invalid_safe_point", "safe_point_kind is required")
        with self.executions._transaction() as connection:
            attempt = self.attempts._require(connection, attempt_id)
            self.attempts._validate_generation(attempt, generation)
            execution = self.executions._require_execution(connection, attempt.execution_id)
            command = self.executions._get_command(connection, command_id)
            if command is not None and command.execution_id != execution.execution_id:
                raise AttemptConflict("command_mismatch", "safe point command belongs to another execution")
            if command is not None and command.status is CommandStatus.APPLIED:
                checkpoint = (
                    self.checkpoints._get(connection, execution.checkpoint_head_id)
                    if execution.checkpoint_head_id
                    else None
                )
                return SafePointCompletion(
                    command=command,
                    execution=execution,
                    attempt=attempt,
                    checkpoint=checkpoint,
                )
            if execution.status_version != expected_execution_version:
                raise AttemptConflict(
                    "stale_version",
                    f"expected execution version {expected_execution_version}, found {execution.status_version}",
                )
            if (
                command is None
                or command.execution_id != execution.execution_id
                or command.kind is not CommandKind.STEP
                or command.status is not CommandStatus.APPLYING
            ):
                raise AttemptConflict("command_mismatch", "safe point does not match an applying step command")
            if fragment.safe_point_kind not in execution.capabilities.safe_point_kinds:
                raise AttemptConflict("unsupported_safe_point", "driver reported an undeclared safe point")

            pause = self._applying_command(connection, execution.execution_id, CommandKind.PAUSE)

            checkpoint_version = expected_execution_version
            if execution.status is ExecutionStatus.RUNNING:
                execution = self.executions._transition_execution(
                    connection,
                    execution.execution_id,
                    expected_version=expected_execution_version,
                    target=ExecutionStatus.PAUSING,
                    reason_code="step_at_safe_point",
                )
                checkpoint_version = execution.status_version
            elif execution.status is not ExecutionStatus.PAUSING:
                raise AttemptConflict(
                    "invalid_state",
                    f"step safe point arrived while execution is {execution.status.value}",
                )

            state_refs = dict(fragment.state_refs)
            steering = list(state_refs.get("steering", ()))
            steering_commands = connection.execute(
                "SELECT * FROM commands WHERE execution_id = ? AND kind = ? "
                "AND status IN (?, ?) ORDER BY submitted_at, command_id",
                (
                    execution.execution_id,
                    CommandKind.STEER.value,
                    CommandStatus.ACCEPTED.value,
                    CommandStatus.APPLYING.value,
                ),
            ).fetchall()
            for row in steering_commands:
                steer = self.executions._command(row)
                steering.append({"command_id": steer.command_id, "payload": dict(steer.payload)})
            if steering_commands:
                state_refs["steering"] = steering
            unit = fragment.managed_action or fragment.control_step
            completed = tuple(fragment.completed_actions) + ({"managed_action": dict(unit)} if fragment.managed_action is not None else {"control_step": dict(unit)},)
            pending = tuple(
                command_id
                for command_id in fragment.pending_command_ids
                if command_id != command.command_id
                and command_id not in {str(row["command_id"]) for row in steering_commands}
            )
            # The checkpoint and all command/attempt lifecycle writes below are
            # committed together; a crash cannot expose a half-applied step.
            checkpoint, checkpointed = self.checkpoints._publish_in_transaction(
                connection,
                execution_id=execution.execution_id,
                expected_version=checkpoint_version,
                revision_id=execution.revision_id,
                parent_checkpoint_id=execution.checkpoint_head_id,
                frontier=fragment.frontier,
                completed_frontier=fragment.completed_frontier,
                state_refs=state_refs,
                completed_actions=completed,
                effect_receipts=fragment.effect_receipts,
                child_frontier=fragment.child_frontier,
                pending_command_ids=pending,
                created_by_attempt_id=attempt_id,
            )
            ended, paused = self.attempts._finish_in_transaction(
                connection,
                attempt_id,
                generation=generation,
                expected_execution_version=checkpointed.status_version,
                target=ExecutionStatus.PAUSED,
                outcome="step_at_safe_point",
            )
            safe_point = _thaw_json(checkpoint.frontier[-1]) if checkpoint.frontier else {"kind": fragment.safe_point_kind}
            receipt = {"checkpoint_id": checkpoint.checkpoint_id, "safe_point": safe_point}
            if pause is not None:
                command = self.executions._transition_command(
                    connection,
                    command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.REJECTED,
                    result_version=paused.status_version,
                    rejection_code="superseded_by_pause",
                )
                applied = []
                pause = self.executions._transition_command(
                    connection,
                    pause.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=paused.status_version,
                    receipt=receipt,
                )
                applied.append(pause)
            else:
                command = self.executions._transition_command(
                    connection,
                    command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=paused.status_version,
                    receipt=receipt,
                )
                applied = [command]
            for row in steering_commands:
                steer = self.executions._command(row)
                if steer.status is CommandStatus.ACCEPTED:
                    steer = self.executions._transition_command(
                        connection,
                        steer.command_id,
                        expected_status=CommandStatus.ACCEPTED,
                        target=CommandStatus.APPLYING,
                    )
                steer = self.executions._transition_command(
                    connection,
                    steer.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=paused.status_version,
                    receipt=receipt,
                )
                applied.append(steer)
        self.registry.unbind(execution.execution_id, attempt_id=attempt_id, generation=generation)
        return SafePointCompletion(
            command=command,
            execution=paused,
            attempt=ended,
            checkpoint=checkpoint,
            applied_commands=tuple(applied),
        )

    def finish_attempt(
        self,
        *,
        attempt_id: str,
        generation: int,
        expected_execution_version: int,
        target: ExecutionStatus,
        outcome: str,
        command_id: str | None = None,
        reason_code: str | None = None,
    ) -> AttemptCompletion:
        if target not in TERMINAL_EXECUTION_STATUSES:
            raise AttemptConflict(
                "invalid_outcome",
                "finish_attempt requires a terminal execution target",
            )
        attempt = self.attempts.get(attempt_id)
        if attempt is None:
            raise AttemptConflict("not_found", f"attempt not found: {attempt_id}")
        command = self.executions.get_command(command_id) if command_id else None
        if command_id is not None and (
            command is None
            or command.execution_id != attempt.execution_id
            or command.status is not CommandStatus.APPLYING
        ):
            raise AttemptConflict(
                "command_mismatch",
                "attempt outcome does not match an applying command",
            )
        unresolved = self.effects.list_unresolved(attempt.execution_id)
        actual_target = (
            ExecutionStatus.RECONCILIATION_REQUIRED if unresolved else target
        )
        ended, execution = self.attempts.finish(
            attempt_id,
            generation=generation,
            expected_execution_version=expected_execution_version,
            target=actual_target,
            outcome=("reconciliation_required" if unresolved else outcome),
            reason_code=("effect_reconciliation" if unresolved else reason_code),
        )
        self.registry.unbind(
            execution.execution_id,
            attempt_id=attempt_id,
            generation=generation,
        )
        try:
            if command is not None and execution.status in TERMINAL_EXECUTION_STATUSES:
                command = self._mark_applied(command, execution)
        finally:
            if execution.status in TERMINAL_EXECUTION_STATUSES:
                self._forget_cancel_delivery(execution.execution_id)
        return AttemptCompletion(
            execution=execution,
            attempt=ended,
            command=command,
        )

    def recover_owner_loss(
        self,
        execution_id: str,
        *,
        attempt_id: str | None = None,
        generation: int | None = None,
    ) -> RecoveryCompletion:
        """Durably finalize work whose physical owner is known to be gone.

        A physical owner reports its own loss with the exact attempt identity.
        The check is performed inside the same write transaction as recovery,
        so a late report from an older owner cannot recover a newer attempt.
        Startup recovery omits the identity because it is the authority that
        discovers abandoned owners.
        """
        if (attempt_id is None) != (generation is None):
            raise AttemptConflict(
                "invalid_owner",
                "attempt_id and generation must be supplied together",
            )
        with self.executions._transaction() as connection:
            execution = self.executions._require_execution(connection, execution_id)
            if attempt_id is not None and (
                execution.current_attempt_id != attempt_id
                or execution.owner_lease.get("generation") != generation
                ):
                raise AttemptConflict(
                    "stale_owner",
                    "owner-loss report does not match the current execution owner",
                )
            if execution.status in TERMINAL_EXECUTION_STATUSES:
                self._reconcile_terminal_cancel(execution, connection)
                self._forget_cancel_delivery(execution_id)
                return RecoveryCompletion(execution=execution)
            if (
                execution.status is ExecutionStatus.PAUSED
                and execution.current_attempt_id is None
            ):
                command = self._applying_command(
                    connection, execution_id, CommandKind.PAUSE
                )
                if command is not None:
                    command = self.executions._transition_command(
                        connection,
                        command.command_id,
                        expected_status=CommandStatus.APPLYING,
                        target=CommandStatus.APPLIED,
                        result_version=execution.status_version,
                    )
                return RecoveryCompletion(execution=execution, command=command)
            if execution.status not in {
                ExecutionStatus.QUEUED,
                ExecutionStatus.RUNNING,
                ExecutionStatus.PAUSING,
                ExecutionStatus.PAUSED,
                ExecutionStatus.CANCELLING,
            }:
                self._forget_cancel_delivery(execution_id)
                return RecoveryCompletion(execution=execution)
            if (
                execution.status in {ExecutionStatus.QUEUED, ExecutionStatus.PAUSED}
                and execution.current_attempt_id is None
            ):
                return RecoveryCompletion(execution=execution)

            unresolved = connection.execute(
                "SELECT 1 FROM effects WHERE execution_id = ? "
                "AND status IN ('dispatched', 'uncertain') LIMIT 1",
                (execution_id,),
            ).fetchone() is not None
            command_kind: CommandKind | None = None
            apply_command = False
            reject_command = False
            running_commands = False
            if execution.status in {ExecutionStatus.QUEUED, ExecutionStatus.PAUSED}:
                target = execution.status
                reason_code = "owner_lost_before_activation"
                outcome = "owner_lost_before_activation"
                if execution.status is ExecutionStatus.PAUSED:
                    command_kind = CommandKind.PAUSE
                    apply_command = True
            elif execution.status is ExecutionStatus.RUNNING:
                running_commands = True
                target = (
                    ExecutionStatus.RECONCILIATION_REQUIRED
                    if unresolved
                    else ExecutionStatus.INTERRUPTED
                )
                reason_code = "effect_reconciliation" if unresolved else "owner_lost"
                outcome = "reconciliation_required" if unresolved else "owner_lost"
            elif execution.status is ExecutionStatus.PAUSING:
                command_kind = CommandKind.PAUSE
                if unresolved:
                    target = ExecutionStatus.RECONCILIATION_REQUIRED
                    reason_code = "effect_reconciliation"
                    outcome = "reconciliation_required"
                elif execution.checkpoint_head_id is not None:
                    target = ExecutionStatus.PAUSED
                    reason_code = "owner_lost_after_checkpoint"
                    outcome = "owner_lost_after_checkpoint"
                    apply_command = True
                else:
                    target = ExecutionStatus.INTERRUPTED
                    reason_code = "owner_lost_before_checkpoint"
                    outcome = "owner_lost_before_checkpoint"
                    reject_command = True
            else:
                command_kind = CommandKind.CANCEL
                if unresolved:
                    target = ExecutionStatus.RECONCILIATION_REQUIRED
                    reason_code = "effect_reconciliation"
                    outcome = "reconciliation_required"
                else:
                    target = ExecutionStatus.CANCELLED
                    reason_code = execution.reason_code or "owner_lost"
                    outcome = "owner_lost_during_cancel"
                    apply_command = True

            recovered = self.executions._transition_execution(
                connection,
                execution_id,
                expected_version=execution.status_version,
                target=target,
                reason_code=reason_code,
                clear_owner=True,
            )
            attempt = None
            if execution.current_attempt_id is not None:
                attempt = self.attempts._require(
                    connection, execution.current_attempt_id
                )
                if attempt.execution_id != execution_id:
                    raise AttemptConflict(
                        "attempt_mismatch",
                        "execution current attempt belongs to a different execution",
                    )
                attempt = self.attempts._end_for_owner_loss(
                    connection, attempt, outcome=outcome
                )
                self.executions._append_event(
                    connection,
                    execution_id=execution_id,
                    execution_version=recovered.status_version,
                    kind="attempt.ended",
                    payload={"attempt": attempt.to_dict()},
                    created_at=attempt.updated_at,
                )

            command = None
            if running_commands:
                rows = connection.execute(
                    "SELECT * FROM commands WHERE execution_id = ? "
                    "AND status IN (?, ?) ORDER BY submitted_at, command_id",
                    (
                        execution_id,
                        CommandStatus.ACCEPTED.value,
                        CommandStatus.APPLYING.value,
                    ),
                ).fetchall()
                for row in rows:
                    recovered_command = self.executions._transition_command(
                        connection,
                        str(row["command_id"]),
                        expected_status=CommandStatus(row["status"]),
                        target=CommandStatus.REJECTED,
                        result_version=recovered.status_version,
                        rejection_code=(
                            "effect_reconciliation" if unresolved else "owner_lost"
                        ),
                    )
                    if command is None or recovered_command.kind is CommandKind.STEP:
                        command = recovered_command
            else:
                command = self._applying_command(
                    connection, execution_id, command_kind
                )
            if command is not None and apply_command:
                command = self.executions._transition_command(
                    connection,
                    command.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=recovered.status_version,
                )
            elif command is not None and reject_command:
                command = self.executions._transition_command(
                    connection,
                    command.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.REJECTED,
                    result_version=recovered.status_version,
                    rejection_code="owner_lost_before_checkpoint",
                )
        if attempt is not None:
            self.registry.unbind(
                execution_id,
                attempt_id=attempt.attempt_id,
                generation=attempt.generation,
            )
        if recovered.status in TERMINAL_EXECUTION_STATUSES:
            self._forget_cancel_delivery(execution_id)
        return RecoveryCompletion(
            execution=recovered,
            attempt=attempt,
            command=command,
        )

    def recover_startup(self) -> tuple[RecoveryCompletion, ...]:
        """Recover nonterminal executions whose previous owner is gone.

        An admitted Agent execution is normally activated immediately. If a
        process stops after admission but before an attempt is leased, startup
        terminalizes that record instead of leaving it queued forever.
        """
        self.replay_finish_repairs()
        # A stalled repair is a durable, explicit recovery item. Startup may
        # retry it once through the same fenced path; if it remains blocked,
        # its marker prevents generic owner-loss recovery from changing the
        # desired outcome.
        self.replay_finish_repairs(include_stalled=True, due_only=True)
        stalled_repairs = set()
        repair_cursor = None
        while True:
            repair_page = self.executions.list_finish_repairs(
                limit=256,
                include_stalled=True,
                after=repair_cursor,
            )
            if not repair_page:
                break
            for repair in repair_page:
                repair_cursor = (
                    float(repair["created_at"]),
                    str(repair["execution_id"]),
                    str(repair["attempt_id"]),
                    int(repair["generation"]),
                )
                if repair.get("reason_code") == "finish_repair_stalled":
                    stalled_repairs.add(str(repair["execution_id"]))
        recoveries = []
        for execution in self.executions.list_nonterminal():
            if (
                execution.execution_id in stalled_repairs
                or execution.reason_code == "finish_repair_capacity_migration"
            ):
                # A bounded in-process retry explicitly dead-lettered this
                # finish. Keep its owner state visible for manual reconcile;
                # startup owner-loss recovery must not overwrite the repair.
                continue
            if execution.status in {
                ExecutionStatus.RUNNING,
                ExecutionStatus.PAUSING,
                ExecutionStatus.CANCELLING,
            } or (
                execution.status in {ExecutionStatus.QUEUED, ExecutionStatus.PAUSED}
                and execution.current_attempt_id is not None
            ):
                recoveries.append(self.recover_owner_loss(execution.execution_id))
            elif (
                execution.status is ExecutionStatus.QUEUED
                and execution.current_attempt_id is None
                and self.executions.get_agent_turn_input(execution.execution_id) is not None
            ):
                try:
                    recovered = self.executions.transition_execution(
                        execution.execution_id,
                        expected_version=execution.status_version,
                        target=ExecutionStatus.FAILED,
                        reason_code="owner_lost_before_activation",
                    )
                except (AttemptConflict, ExecutionConflict):
                    recovered = self.executions.get_execution(execution.execution_id)
                    if recovered is None:
                        continue
                recoveries.append(RecoveryCompletion(execution=recovered))
        return tuple(recoveries)

    def replay_finish_repairs(
        self, *, include_stalled: bool = False, due_only: bool = False,
    ) -> int:
        """Replay durable Agent finish intents with current fencing state."""
        repaired = 0
        cursor = None
        while True:
            repairs = self.executions.list_finish_repairs(
                limit=256,
                include_stalled=include_stalled,
                after=cursor,
                due_before=time.time() if due_only else None,
            )
            if not repairs:
                break
            for repair in repairs:
                cursor = (
                    float(repair["created_at"]),
                    str(repair["execution_id"]),
                    str(repair["attempt_id"]),
                    int(repair["generation"]),
                )
                if (
                    repair.get("reason_code") == "finish_repair_stalled"
                    and not include_stalled
                ):
                    continue
                execution_id = str(repair["execution_id"])
                attempt_id = str(repair["attempt_id"])
                generation = int(repair["generation"])
                execution = self.executions.get_execution(execution_id)
                attempt = self.attempts.get(attempt_id)
                if (
                    execution is None
                    or attempt is None
                    or execution.status in TERMINAL_EXECUTION_STATUSES
                    or execution.current_attempt_id != attempt_id
                    or execution.owner_lease.get("generation") != generation
                    or attempt.generation != generation
                    or attempt.status is not AttemptStatus.ACTIVE
                ):
                    self.executions.delete_finish_repair(
                        execution_id, attempt_id, generation,
                    )
                    repaired += 1
                    continue
                try:
                    target = ExecutionStatus(str(repair["target"]))
                except ValueError:
                    self.executions.delete_finish_repair(
                        execution_id, attempt_id, generation,
                    )
                    repaired += 1
                    continue
                outcome = str(repair["outcome"])
                reason_code = repair.get("reason_code")
                command_id = repair.get("command_id")
                if execution.status is ExecutionStatus.CANCELLING:
                    target = ExecutionStatus.CANCELLED
                    outcome = "cancelled"
                    reason_code = execution.reason_code or "cancelled"
                    applying_cancels = self.executions.list_commands(
                        execution_id,
                        statuses=(CommandStatus.APPLYING,),
                        kinds=(CommandKind.CANCEL,),
                    )
                    command_id = (
                        applying_cancels[0].command_id
                        if applying_cancels
                        else None
                    )
                    if command_id is None:
                        # A cancelling execution with an active attempt must
                        # be completed through its applying cancel command.
                        continue
                    self.executions.upsert_finish_repair(
                        execution_id=execution_id,
                        attempt_id=attempt_id,
                        generation=generation,
                        expected_version=execution.status_version,
                        target=target.value,
                        outcome=outcome,
                        reason_code=reason_code,
                        command_id=command_id,
                    )
                try:
                    self.finish_attempt(
                        attempt_id=attempt_id,
                        generation=generation,
                        expected_execution_version=execution.status_version,
                        target=target,
                        outcome=outcome,
                        command_id=(str(command_id) if command_id else None),
                        reason_code=reason_code,
                    )
                except Exception:
                    retry_count = int(repair.get("retry_count") or 0) + 1
                    try:
                        self.executions.defer_finish_repair(
                            execution_id,
                            attempt_id,
                            generation,
                            retry_count=retry_count,
                            next_attempt_at=time.time() + min(
                                3600.0, 30.0 * (2 ** min(retry_count, 7))
                            ),
                        )
                    except Exception:
                        continue
                    continue
                self.executions.delete_finish_repair(
                    execution_id, attempt_id, generation,
                )
                repaired += 1
        return repaired

    def resolve_effect(
        self,
        *,
        effect_id: str,
        expected_status: EffectStatus,
        outcome: EffectStatus,
        receipt: Mapping[str, Any],
    ) -> ReconciliationCompletion:
        effect = self.effects.resolve(
            effect_id,
            expected_status=expected_status,
            outcome=outcome,
            receipt=receipt,
        )
        execution = self.executions.get_execution(effect.execution_id)
        if execution is None:
            raise AttemptConflict("execution_not_found", "effect execution is missing")
        if (
            execution.status is not ExecutionStatus.RECONCILIATION_REQUIRED
            or self.effects.list_unresolved(execution.execution_id)
        ):
            return ReconciliationCompletion(effect=effect, execution=execution)
        commands = self.executions.list_commands(
            execution.execution_id,
            statuses=(CommandStatus.APPLYING,),
            kinds=(CommandKind.CANCEL, CommandKind.PAUSE),
        )
        command = commands[0] if commands else None
        if command is None or command.kind is CommandKind.PAUSE:
            execution = self.executions.transition_execution(
                execution.execution_id,
                expected_version=execution.status_version,
                target=ExecutionStatus.PAUSED,
                reason_code="effects_reconciled",
            )
            if command is not None:
                command = self._mark_applied(command, execution)
        else:
            execution = self.executions.transition_execution(
                execution.execution_id,
                expected_version=execution.status_version,
                target=ExecutionStatus.CANCELLING,
                reason_code="effects_reconciled",
            )
            execution = self.executions.transition_execution(
                execution.execution_id,
                expected_version=execution.status_version,
                target=ExecutionStatus.CANCELLED,
                reason_code="cancelled_after_reconciliation",
            )
            command = self._mark_applied(command, execution)
        return ReconciliationCompletion(
            effect=effect,
            execution=execution,
            command=command,
        )

    def _mark_applied(
        self,
        command: ControlCommand,
        execution: ExecutionRecord,
        *,
        receipt: Mapping[str, Any] | None = None,
    ) -> ControlCommand:
        if command.execution_id != execution.execution_id:
            raise AttemptConflict(
                "command_mismatch",
                "command belongs to another execution",
            )
        if command.status is CommandStatus.APPLIED:
            if command.kind is CommandKind.CANCEL:
                self._forget_cancel_delivery(
                    command.execution_id, command.command_id,
                )
            return command
        applied = self.executions.transition_command(
            command.command_id,
            expected_status=CommandStatus.APPLYING,
            target=CommandStatus.APPLIED,
            result_version=execution.status_version,
            receipt=receipt,
        )
        if command.kind is CommandKind.CANCEL:
            self._forget_cancel_delivery(
                command.execution_id, command.command_id,
            )
        return applied

    def _applying_command(
        self,
        connection,
        execution_id: str,
        kind: CommandKind | None,
    ) -> ControlCommand | None:
        if kind is None:
            return None
        row = connection.execute(
            "SELECT * FROM commands WHERE execution_id = ? AND kind = ? "
            "AND status = ? ORDER BY submitted_at, command_id LIMIT 1",
            (execution_id, kind.value, CommandStatus.APPLYING.value),
        ).fetchone()
        return self.executions._command(row) if row is not None else None

    async def _dispatch(
        self,
        command: ControlCommand,
        execution: ExecutionRecord,
        *,
        operation: str,
    ) -> ControlDispatch:
        attempt_id = execution.current_attempt_id
        generation = execution.owner_lease.get("generation")
        if attempt_id is None or not isinstance(generation, int):
            return ControlDispatch(
                command=command,
                execution=execution,
                delivered=False,
                issue_code="owner_not_active",
            )
        try:
            binding = self.registry.resolve(
                execution.execution_id,
                attempt_id=attempt_id,
                generation=generation,
            )
        except DriverRegistryConflict as exc:
            issue = "owner_not_local" if exc.code == "not_found" else exc.code
            return ControlDispatch(
                command=command,
                execution=execution,
                delivered=False,
                issue_code=issue,
            )
        try:
            if operation == "pause":
                ack = await binding.driver.request_pause(
                    binding.handle, command.command_id
                )
            else:
                ack = await binding.driver.request_cancel(
                    binding.handle, command.command_id
                )
        except Exception:
            return ControlDispatch(
                command=command,
                execution=execution,
                delivered=False,
                issue_code="driver_error",
            )
        if ack.command_id != command.command_id or ack.attempt_id != attempt_id:
            return ControlDispatch(
                command=command,
                execution=execution,
                delivered=False,
                issue_code="invalid_ack",
            )
        return ControlDispatch(
            command=command,
            execution=execution,
            delivered=True,
            ack=ack,
        )

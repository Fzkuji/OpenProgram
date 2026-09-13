"""Execution control branches operations on the owning service."""
from __future__ import annotations
import sqlite3
import time
import uuid
from typing import Any, Mapping
from ..attempts import AttemptConflict, AttemptRecord, AttemptStatus
from ..checkpoints import CheckpointManifest
from ..model import CommandKind, CommandStatus, ControlCommand, ExecutionRecord, ExecutionStatus
from ..store import ExecutionConflict, _json
from ..safe_points import AgentSafePointConflict
from ..state_machine import InvalidCommand

from .shared import (
    Activator,
    BranchCompletion,
    ControlDispatch,
)


class BranchesOperations:
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
        code_change_policy: str | None = None,
    ) -> ControlDispatch:
        """Resume a paused execution without changing its revision or identity."""
        if code_change_policy is not None and (not isinstance(code_change_policy, str) or code_change_policy not in {"keep_original", "use_latest"}):
            raise InvalidCommand("invalid_payload", "invalid function code policy")
        current = self.executions.get_execution(execution_id)
        input_record = self.executions.get_execution_input(execution_id) if current is not None else None
        if (
            current is not None
            and input_record is not None
            and input_record.entrypoint == "openprogram.agent.production_driver:AgentProductionDriver"
            and activator is None and driver is None and self.activator is None
        ):
            raise AgentSafePointConflict("activation_unavailable", "Agent continuation requires a production activation owner")
        if (
            current is not None
            and "pause" not in current.capabilities
            and current.reason_code != "restart_pending"
        ):
            raise AgentSafePointConflict("unsupported", "execution does not declare the pause capability")
        command, execution, attempt, checkpoint, steer_inputs, duplicate = self._resume_transaction(
            command_id=command_id,
            execution_id=execution_id,
            expected_version=expected_version,
            actor=actor,
            kind=CommandKind.CONTINUE,
            payload={"code_change_policy": code_change_policy} if code_change_policy is not None else {},
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
        current = self.executions.get_execution(execution_id)
        input_record = self.executions.get_execution_input(execution_id) if current is not None else None
        if (
            current is not None and input_record is not None
            and input_record.entrypoint == "openprogram.agent.production_driver:AgentProductionDriver"
            and activator is None and driver is None and self.activator is None
        ):
            raise AgentSafePointConflict("activation_unavailable", "Agent continuation requires a production activation owner")
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


    async def activate_accepted_job_command(
        self,
        *,
        command_id: str,
        execution_id: str,
        owner_id: str,
        ttl_seconds: float | None = None,
        activator: Activator | None = None,
        driver: Any | None = None,
    ) -> ControlDispatch:
        """Activate one resource-claimed Job command through the canonical owner.

        Job resource admission may wait after a public continue/step command
        has been accepted.  This internal entry consumes that exact durable
        command only after ResourceGovernor has assigned capacity; transports
        never call it.
        """
        command = self.executions.get_command(command_id)
        if command is None or command.execution_id != execution_id:
            raise ExecutionConflict("command_not_found", "accepted Job command is unavailable")
        if command.kind not in {CommandKind.CONTINUE, CommandKind.STEP}:
            raise ExecutionConflict("invalid_command", "Job activation requires continue or step")
        command, execution, attempt, checkpoint, steer_inputs, duplicate = self._resume_transaction(
            command_id=command_id,
            execution_id=execution_id,
            expected_version=command.expected_version,
            actor=command.actor,
            kind=command.kind,
            owner_id=owner_id,
            ttl_seconds=ttl_seconds or self.lease_ttl_seconds,
            activate_existing_accepted=True,
            allow_queued_initial_step=True,
        )
        if duplicate:
            return ControlDispatch(command=command, execution=execution, delivered=False)
        delivered, issue = await self._activate(
            attempt, checkpoint, steer_inputs, activator=activator, driver=driver,
        )
        command, execution = self._finish_activation(
            attempt, command, delivered=delivered, issue_code=issue,
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
        manifest_id: str,
        checkpoint_id: str,
        proof_hash: str,
    ) -> BranchCompletion:
        """Fork only from an already published, bound RevisionManifestV1."""
        payload = {"manifest_id": manifest_id, "checkpoint_id": checkpoint_id, "proof_hash": proof_hash}
        return self._request_branch(
            command_id=command_id,
            execution_id=execution_id,
            expected_version=expected_version,
            actor=actor,
            kind=CommandKind.FORK,
            payload=payload,
            manifest_id=manifest_id,
            checkpoint_id=checkpoint_id,
            child_execution_id=None,
        )


    def request_retry(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: Mapping[str, Any],
        checkpoint_id: str | None = None,
        child_execution_id: str | None = None,
    ) -> BranchCompletion:
        """Create a queued same-revision child from the source checkpoint head."""
        command_payload: dict[str, Any] = {}
        if checkpoint_id is not None:
            command_payload["checkpoint_id"] = checkpoint_id
        if child_execution_id is not None:
            command_payload["child_execution_id"] = child_execution_id
        return self._request_branch(
            command_id=command_id,
            execution_id=execution_id,
            expected_version=expected_version,
            actor=actor,
            kind=CommandKind.RETRY,
            payload=command_payload,
            manifest_id=None,
            checkpoint_id=checkpoint_id,
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
        manifest_id: str | None,
        checkpoint_id: str | None,
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

            manifest = None
            if kind is CommandKind.FORK:
                if checkpoint_id is None or manifest_id is None:
                    raise ExecutionConflict("revision_manifest_required", "fork requires a published revision manifest")
                from ..revisions import RevisionControlService
                manifest = RevisionControlService(self.executions).require_fork_manifest(
                    connection,
                    manifest_id=manifest_id,
                    source_execution_id=source.execution_id,
                    checkpoint_id=checkpoint_id,
                    proof_hash=str(payload.get("proof_hash", "")),
                )
                if manifest.parent_revision_id != source.revision_id:
                    raise ExecutionConflict("revision_manifest_base_mismatch", "revision manifest base does not match source")
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
                assert manifest is not None
                if checkpoint.checkpoint_id != manifest.compatible_checkpoint_id:
                    raise ExecutionConflict("revision_manifest_checkpoint_mismatch", "revision manifest checkpoint does not match")
            command = self.executions._transition_command(
                connection,
                command_id,
                expected_status=CommandStatus.ACCEPTED,
                target=CommandStatus.APPLYING,
            )
            if kind is CommandKind.FORK:
                assert manifest is not None
                revision = self.executions._get_revision(connection, manifest.revision_id)
                if revision is None:
                    raise ExecutionConflict("revision_manifest_invalid", "revision manifest revision is missing")
            else:
                revision = self.executions._get_revision(connection, source.revision_id)
                if revision is None:
                    raise ExecutionConflict("revision_not_found", "source revision is missing")

            child_id = child_execution_id or f"exec_{uuid.uuid4().hex}"
            instruction_branch = (kind is CommandKind.FORK
                and RevisionControlService(self.executions).published_instructions(revision.revision_id) is not None)
            source_input = self.executions.get_agent_turn_input(source.execution_id)
            agent_retry = kind is CommandKind.RETRY and source_input is not None and source_input.get("kind") == "chat"
            isolated_agent_branch = instruction_branch or agent_retry
            now = time.time()
            child = ExecutionRecord(
                execution_id=child_id,
                run_id=source.run_id,
                session_id=source.session_id,
                revision_id=revision.revision_id,
                parent_execution_id=source.execution_id,
                source_checkpoint_id=checkpoint.checkpoint_id,
                status=ExecutionStatus.PAUSED if isolated_agent_branch else ExecutionStatus.QUEUED,
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
                assistant_message_id=f"{child_id}_reply" if isolated_agent_branch else None,
            )
            self.executions._append_event(
                connection,
                execution_id=child.execution_id,
                execution_version=child.status_version,
                kind="execution.created",
                payload={"record": child.to_dict()},
                created_at=now,
            )
            if instruction_branch:
                _manifest, instructions = RevisionControlService(self.executions).published_instructions(revision.revision_id)
                self.executions._accept_command(
                    connection, command_id=f"revision-instructions:{child_id}",
                    execution_id=child_id, expected_version=child.status_version,
                    kind=CommandKind.STEER, payload={"message": instructions}, actor=actor,
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
                    **({"manifest_id": manifest_id} if kind is CommandKind.FORK else {}),
                },
            )
            return BranchCompletion(command, source, child, revision, checkpoint)


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
        activate_existing_accepted: bool = False,
        allow_queued_initial_step: bool = False,
        payload: Mapping[str, Any] | None = None,
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
                payload=dict(payload or {}),
                actor=actor,
            )
            execution = self.executions._require_execution(connection, execution_id)
            if duplicate:
                if not activate_existing_accepted:
                    checkpoint = (
                        self.checkpoints._get(
                            connection,
                            execution.checkpoint_head_id or execution.source_checkpoint_id,
                        )
                        if (execution.checkpoint_head_id or execution.source_checkpoint_id)
                        else None
                    )
                    return command, execution, None, checkpoint, (), True
                if command.status is not CommandStatus.ACCEPTED:
                    checkpoint = (
                        self.checkpoints._get(
                            connection,
                            execution.checkpoint_head_id or execution.source_checkpoint_id,
                        )
                        if (execution.checkpoint_head_id or execution.source_checkpoint_id)
                        else None
                    )
                    return command, execution, None, checkpoint, (), True
                if command.kind is not kind:
                    raise ExecutionConflict("command_mismatch", "accepted command kind changed")
            if activate_existing_accepted and (
                execution.status is not ExecutionStatus.PAUSED and not (
                    allow_queued_initial_step
                    and kind is CommandKind.STEP
                    and execution.status is ExecutionStatus.QUEUED
                )
            ):
                raise ExecutionConflict(
                    "invalid_state",
                    "accepted Job command no longer has a resumable execution",
                )
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
            # A queued execution may be paused before its initial activation.
            # It has no checkpoint because no provider or tool action has run,
            # but continue may still claim its first owner and run the durable
            # admission input.  Any previous attempt without a checkpoint is
            # owner loss, not a resumable root state.
            initial_activation = (
                checkpoint is None
                and (
                    kind is CommandKind.CONTINUE
                    or (
                        allow_queued_initial_step
                        and kind is CommandKind.STEP
                        and execution.status is ExecutionStatus.QUEUED
                    )
                )
                and connection.execute(
                    "SELECT COUNT(*) FROM attempts WHERE execution_id = ?",
                    (execution_id,),
                ).fetchone()[0] == 0
            )
            # Startup recovery may intentionally retain the original
            # admission when the first provider response was interrupted
            # before a checkpoint could be published.  This is safe only for
            # the private restart_pending state; ordinary paused executions
            # still require a checkpoint.
            restart_initial_activation = (
                checkpoint is None
                and kind is CommandKind.CONTINUE
                and execution.status is ExecutionStatus.PAUSED
                and execution.reason_code == "restart_pending"
            )
            if checkpoint is None and not (initial_activation or restart_initial_activation):
                raise ExecutionConflict("checkpoint_required", "a published checkpoint is required")
            if (
                checkpoint is not None
                and (
                    checkpoint.execution_id != execution_id
                    or checkpoint.revision_id != execution.revision_id
                )
            ):
                if (connection.execute("SELECT 1 FROM attempts WHERE execution_id = ? LIMIT 1", (execution_id,)).fetchone()
                        or not self._agent_branch_checkpoint_is_valid(self.executions, connection, execution, checkpoint)):
                    raise ExecutionConflict("invalid_checkpoint", "checkpoint does not belong to the current execution revision")
            if (
                kind is CommandKind.STEP
                and self._agent_step_has_no_next_action(checkpoint)
            ):
                rejected = self.executions._transition_command(
                    connection,
                    command.command_id,
                    expected_status=CommandStatus.ACCEPTED,
                    target=CommandStatus.REJECTED,
                    result_version=execution.status_version,
                    rejection_code="no_next_action",
                )
                return rejected, execution, None, checkpoint, (), True
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
            from ..process_owner import current_process_owner
            connection.execute(
                "UPDATE executions SET current_attempt_id = ?, owner_lease_json = ?, updated_at = ? "
                "WHERE execution_id = ? AND status_version = ?",
                (
                    attempt.attempt_id,
                    _json({"owner_id": owner_id, "generation": generation,
                           "process_owner": current_process_owner(),
                           **({"resume_checkpoint_id": checkpoint.checkpoint_id} if checkpoint is not None else {})}),
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


    @staticmethod
    def _agent_branch_checkpoint_is_valid(store, connection, execution, checkpoint) -> bool:
        """Accept only the exact source of an isolated Agent Retry/Fork."""
        if (not execution.parent_execution_id
                or execution.source_checkpoint_id != checkpoint.checkpoint_id
                or execution.parent_execution_id != checkpoint.execution_id):
            return False
        source = store._get_execution(connection, execution.parent_execution_id)
        record = store.get_execution_input(execution.execution_id)
        payload = store.get_agent_turn_input(execution.execution_id)
        if (source is None or source.session_id != execution.session_id
                or source.revision_id != checkpoint.revision_id
                or record is None or record.assistant_message_id != f"{execution.execution_id}_reply"
                or not payload or payload.get("kind") != "chat"):
            return False
        from openprogram.agent.continuation import AgentCheckpointV1
        state = AgentCheckpointV1.load(store, checkpoint)
        ancestors = set()
        current = source
        while current is not None and current.execution_id not in ancestors:
            ancestors.add(current.execution_id)
            current = store._get_execution(connection, current.parent_execution_id) if current.parent_execution_id else None
        for receipt in state.payload["terminal_effect_receipts"]:
            effect = connection.execute("SELECT * FROM effects WHERE effect_id = ?", (receipt["effect_id"],)).fetchone()
            if (effect is None or effect["execution_id"] not in ancestors
                    or effect["action_id"] != receipt["action_id"] or effect["status"] != receipt["outcome"]):
                return False
        if execution.revision_id == checkpoint.revision_id:
            return True
        from ..revisions import RevisionControlService
        return RevisionControlService(store).instruction_branch(connection, execution, checkpoint) is not None


    @staticmethod
    def _agent_step_has_no_next_action(checkpoint: CheckpointManifest | None) -> bool:
        """Recognize a terminal provider checkpoint before issuing STEP.

        An after-provider decision without tool calls has already completed
        the turn.  Starting an attempt solely to manufacture a STEP event
        would leave its permit APPLYING, so reject it durably instead.
        """
        if checkpoint is None:
            return False
        state = checkpoint.state_refs.get("agent_checkpoint_v1")
        if not isinstance(state, Mapping):
            return False
        safe_point = state.get("safe_point")
        decision = state.get("current_decision")
        return (
            isinstance(safe_point, Mapping)
            and safe_point.get("phase") == "after_provider"
            and isinstance(decision, Mapping)
            and not decision.get("tool_call_ids")
        )


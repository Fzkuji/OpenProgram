"""Atomic Agent checkpoint delivery and managed action commands."""
from __future__ import annotations
import time
from typing import Any, Mapping
from ..attempts import AttemptConflict, AttemptStatus
from ..effects import EffectStatus
from ..model import CommandKind, CommandStatus, ControlCommand, ExecutionStatus
from ..store import ExecutionConflict, _json
from ..safe_points import AgentSafePointConflict
from .shared import SafePointCompletion


class AgentSafePointOperations:
    def commit_agent_safe_point(
        self,
        *,
        execution_id: str,
        attempt_id: str,
        generation: int,
        expected_version: int,
        safe_point_kind: str,
        frontier: tuple[Mapping[str, Any], ...],
        state_refs: Mapping[str, Any],
        effect_id: str,
        terminal_receipt: Mapping[str, Any],
        receipt_blob: bytes,
        checkpoint_state_blob: bytes | None = None,
        agent_checkpoint: Any | None = None,
        command_id: str | None = None,
        managed_action_id: str | None = None,
        consumed_steer_command_ids: tuple[str, ...] = (),
        fault_at: str | None = None,
    ) -> SafePointCompletion:
        """Atomically terminalize one effect and publish its Agent frontier."""
        try:
            with self.executions._transaction() as connection:
                completion = self._commit_agent_safe_point_in_transaction(connection,
                    execution_id=execution_id,
                    attempt_id=attempt_id,
                    generation=generation,
                    expected_version=expected_version,
                    safe_point_kind=safe_point_kind,
                    frontier=frontier,
                    state_refs=state_refs,
                    effect_id=effect_id,
                    terminal_receipt=terminal_receipt,
                    receipt_blob=receipt_blob,
                    checkpoint_state_blob=checkpoint_state_blob,
                    agent_checkpoint=agent_checkpoint,
                    command_id=command_id,
                    managed_action_id=managed_action_id,
                    consumed_steer_command_ids=consumed_steer_command_ids,
                    fault_at=fault_at,
                )
            self._observe_paused(completion.execution)
            return completion
        except AgentSafePointConflict:
            raise
        except (ExecutionConflict, AttemptConflict) as exc:
            raise AgentSafePointConflict(getattr(exc, "code", "safe_point_failed"), str(exc)) from exc

    def _commit_agent_safe_point_in_transaction(
        self, connection,
        *,
        execution_id: str,
        attempt_id: str,
        generation: int,
        expected_version: int,
        safe_point_kind: str,
        frontier: tuple[Mapping[str, Any], ...],
        state_refs: Mapping[str, Any],
        effect_id: str,
        terminal_receipt: Mapping[str, Any],
        receipt_blob: bytes,
        checkpoint_state_blob: bytes | None = None,
        agent_checkpoint: Any | None = None,
        command_id: str | None = None,
        managed_action_id: str | None = None,
        consumed_steer_command_ids: tuple[str, ...] = (),
        fault_at: str | None = None,
        recovery_effect_id: str | None = None,
    ) -> SafePointCompletion:
        """Atomically terminalize one effect and publish its Agent frontier."""
        if safe_point_kind not in {"agent.provider.decision.after", "agent.tool.action.after"}:
            raise AgentSafePointConflict("invalid_safe_point", "unsupported Agent safe point")
        attempt = self.attempts._require(connection, attempt_id)
        self.attempts._validate_generation(attempt, generation)
        execution = self.executions._require_execution(connection, execution_id)
        effect = self.effects._require(connection, effect_id)
        recorded = effect.status in {EffectStatus.COMMITTED, EffectStatus.NOT_COMMITTED} and effect.receipt.get("checkpoint_pending") is True
        if recovery_effect_id is not None and (recovery_effect_id != effect_id or not recorded):
            raise AgentSafePointConflict("receipt_invalid", "recovery requires the exact pending result")
        if recorded:
            from ..agent_receipts import validate_checkpoint
            envelope = validate_checkpoint(self.executions, connection, effect, agent_checkpoint, terminal_receipt)
            if (tuple(envelope["consumed_steer_command_ids"]) != consumed_steer_command_ids
                    or managed_action_id != effect.action_id):
                raise AgentSafePointConflict("receipt_conflict", "result delivery changed action or steering identity")
        if (
            attempt.execution_id != execution_id
            or execution.current_attempt_id != attempt_id
            or attempt.status is not AttemptStatus.ACTIVE
            or execution.owner_lease.get("generation") != generation
            or (attempt.lease_expires_at <= time.time() and recovery_effect_id is None)
        ):
            raise AgentSafePointConflict("stale_attempt", "Agent safe point owner is stale")
        if execution.status_version != expected_version:
            raise AgentSafePointConflict("stale_version", "Agent safe point has a stale execution version")
        effect = self.effects._require(connection, effect_id)
        not_started = dict(terminal_receipt).get("outcome") == "not_started"
        if effect.execution_id != execution_id or effect.attempt_id != attempt_id or (
            effect.status is not EffectStatus.DISPATCHED
            and not recorded
            and not (not_started and effect.status is EffectStatus.PLANNED)
        ):
            raise AgentSafePointConflict("effect_state_invalid", "Agent effect is not dispatched by this owner")
        if terminal_receipt.get("function_suspended") is True:
            from openprogram.agentic_programming.continuation import suspension_evidence
            call_key = terminal_receipt.get("tool_call_id")
            if not isinstance(call_key, str) or not suspension_evidence(self.executions, connection, execution_id, call_key):
                raise AgentSafePointConflict("function_state_invalid", "Function suspension has no settled durable continuation")
        try:
            receipt_json = _json(dict(terminal_receipt))
        except (TypeError, ValueError) as exc:
            raise AgentSafePointConflict("receipt_invalid", "terminal receipt must be JSON") from exc
        if len(receipt_json.encode("utf-8")) > 1024 * 1024:
            raise AgentSafePointConflict("receipt_too_large", "terminal receipt exceeds the size limit")
        stored = None
        steering_commands: list[ControlCommand] = []
        if command_id is not None:
            stored = self.executions._get_command(connection, command_id)
            if stored is None or stored.execution_id != execution_id:
                raise AgentSafePointConflict(
                    "command_state_invalid",
                    "safe point command is not owned by this execution",
                )
            if stored.kind not in {
                CommandKind.PAUSE, CommandKind.STEP, CommandKind.STEER,
            }:
                raise AgentSafePointConflict(
                    "command_state_invalid",
                    "unsupported command at Agent safe point",
                )
            if stored.status not in {
                CommandStatus.ACCEPTED, CommandStatus.APPLYING,
            }:
                if stored.status is CommandStatus.APPLIED:
                    checkpoint = (
                        self.checkpoints._get(connection, execution.checkpoint_head_id)
                        if execution.checkpoint_head_id else None
                    )
                    return SafePointCompletion(
                        command=stored, execution=execution,
                        attempt=attempt, checkpoint=checkpoint,
                    )
                raise AgentSafePointConflict(
                    "command_state_invalid",
                    "safe point command is no longer pending",
                )
            if stored.kind is CommandKind.STEER:
                if self._applying_command(connection, execution_id, CommandKind.CANCEL) is not None:
                    raise AgentSafePointConflict("superseded_by_cancel", "cancel has priority over steer")
                if self._applying_command(connection, execution_id, CommandKind.PAUSE) is not None:
                    raise AgentSafePointConflict("superseded_by_pause", "pause has priority over steer")
                if self._applying_command(connection, execution_id, CommandKind.STEP) is not None:
                    raise AgentSafePointConflict("superseded_by_step", "step has priority over steer")
            if stored.kind is CommandKind.STEER:
                steering_rows = connection.execute(
                    "SELECT * FROM commands WHERE execution_id = ? AND kind = ? "
                    "AND status IN (?, ?) ORDER BY submitted_at, command_id",
                    (
                        execution_id, CommandKind.STEER.value,
                        CommandStatus.ACCEPTED.value, CommandStatus.APPLYING.value,
                    ),
                ).fetchall()
                steering_commands = [
                    self.executions._command(row) for row in steering_rows
                ]
        consumed_steering = []
        for consumed_id in dict.fromkeys(consumed_steer_command_ids):
            consumed = self.executions._get_command(connection, consumed_id)
            if (
                consumed is None or consumed.execution_id != execution_id
                or consumed.kind is not CommandKind.STEER
                or consumed.status not in {
                    CommandStatus.ACCEPTED, CommandStatus.APPLYING, CommandStatus.APPLIED,
                }
            ):
                raise AgentSafePointConflict(
                    "command_state_invalid", "consumed steering is not owned by this execution",
                )
            if consumed.status is not CommandStatus.APPLIED:
                consumed_steering.append(consumed)
        receipt_ref = self.executions._put_state_blob_in_transaction(
            connection, execution_id=execution_id, payload=receipt_blob,
            media_type="application/json", schema_version=1,
        )
        refs = dict(state_refs)
        if agent_checkpoint is not None:
            # The Agent serializer computes every descriptor before
            # this transaction.  Recompute nothing here: a mismatch
            # means the owner supplied content different from the
            # checkpoint it asked us to publish.
            from openprogram.agent.continuation import AgentCheckpointV1

            if not isinstance(agent_checkpoint, AgentCheckpointV1):
                raise AgentSafePointConflict(
                    "checkpoint_schema_invalid",
                    "Agent checkpoint must use AgentCheckpointV1",
                )
            try:
                agent_checkpoint.validate()
                for descriptor_ref, payload in agent_checkpoint.blob_payloads.items():
                    descriptor = self.executions._put_state_blob_in_transaction(
                        connection,
                        execution_id=execution_id,
                        payload=payload,
                        media_type="application/json",
                        schema_version=1,
                    )
                    if descriptor["ref"] != descriptor_ref:
                        raise AgentSafePointConflict(
                            "state_ref_invalid",
                            "Agent checkpoint blob descriptor does not match payload",
                        )
                checkpoint_descriptor = self.executions._put_state_blob_in_transaction(
                    connection,
                    execution_id=execution_id,
                    payload=agent_checkpoint.to_bytes(),
                    media_type="application/json",
                    schema_version=1,
                )
            except AgentSafePointConflict:
                raise
            except Exception as exc:
                raise AgentSafePointConflict(
                    getattr(exc, "code", "checkpoint_schema_invalid"), str(exc)
                ) from exc
            refs["agent_checkpoint"] = checkpoint_descriptor
            # The checkpoint payload itself is content-addressed.
            # Keep only a shallow manifest projection for existing
            # status readers; recovery always reloads and verifies
            # ``agent_checkpoint`` above.
            refs["agent_checkpoint_v1"] = {
                key: agent_checkpoint.payload[key]
                for key in (
                    "safe_point", "frontier", "turn",
                    "current_decision", "next_tool_index",
                )
            }
        elif checkpoint_state_blob is not None:
            refs["agent_checkpoint"] = self.executions._put_state_blob_in_transaction(
                connection, execution_id=execution_id, payload=checkpoint_state_blob,
                media_type="application/json", schema_version=1,
            )
        if steering_commands:
            steering = list(refs.get("steering", ()))
            steering.extend(
                {
                    "command_id": item.command_id,
                    "payload": dict(item.payload),
                }
                for item in steering_commands
            )
            refs["steering"] = steering
        now = time.time()
        effect_status = (
            EffectStatus.NOT_COMMITTED.value
            if dict(terminal_receipt).get("outcome") == "not_started"
            else EffectStatus.COMMITTED.value
        )
        connection.execute(
            "UPDATE effects SET status = ?, receipt_json = ?, updated_at = ?, resolved_at = ? WHERE effect_id = ?",
            (effect_status, _json({**dict(effect.receipt), **dict(terminal_receipt), "receipt_ref": receipt_ref, **({"checkpoint_pending": False} if recorded else {})}), now, now, effect_id),
        )
        effect = self.effects._require(connection, effect_id)
        self.effects._append_event(connection, execution.status_version, effect, now)
        if fault_at == "after_receipt_blob":
            raise AgentSafePointConflict("injected_failure", "injected failure after terminal receipt blob")
        checkpoint, updated = self.checkpoints._publish_in_transaction(
            connection, execution_id=execution_id, expected_version=expected_version,
            revision_id=execution.revision_id, parent_checkpoint_id=execution.checkpoint_head_id,
            frontier=frontier, state_refs={**refs, "terminal_receipt": receipt_ref},
            completed_actions=({"action_id": effect.action_id},),
            effect_receipts=({"effect_id": effect_id, "outcome": "committed", "receipt_ref": receipt_ref},),
            child_frontier={}, pending_command_ids=(), created_by_attempt_id=attempt_id,
            recovery_effect_id=recovery_effect_id,
        )
        command = ControlCommand(
                command_id="", execution_id=execution_id, expected_version=expected_version,
                kind=CommandKind.PAUSE, payload={}, actor={}, status=CommandStatus.APPLIED,
                submitted_at=now, updated_at=now,
            )
        applied_commands: list[ControlCommand] = []
        # The decision receipt and consumed input acknowledgments must
        # commit together, including when Step/Pause takes priority.
        if stored is None or stored.kind is not CommandKind.STEER:
            for steer in consumed_steering:
                if steer.status is CommandStatus.ACCEPTED:
                    steer = self.executions._transition_command(
                        connection, steer.command_id,
                        expected_status=CommandStatus.ACCEPTED,
                        target=CommandStatus.APPLYING,
                    )
                applied_commands.append(self.executions._transition_command(
                    connection, steer.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=updated.status_version,
                    receipt={"checkpoint_id": checkpoint.checkpoint_id,
                             "safe_point": checkpoint.safe_point,
                             "terminal_receipt": dict(terminal_receipt)},
                ))
        if command_id is not None:
            assert stored is not None
            if stored.kind is CommandKind.STEER:
                if execution.status not in {ExecutionStatus.RUNNING, ExecutionStatus.PAUSING}:
                    raise AgentSafePointConflict(
                        "execution_state_invalid",
                        "steer safe point requires a running Agent",
                    )
                for steer in steering_commands:
                    if steer.status is CommandStatus.ACCEPTED:
                        steer = self.executions._transition_command(
                            connection,
                            steer.command_id,
                            expected_status=CommandStatus.ACCEPTED,
                            target=CommandStatus.APPLYING,
                        )
                    # The runtime still has to persist this input as a
                    # user message. Its receipt closes APPLYING only after
                    # that write; a checkpoint alone is not delivery.
                    applied_commands.append(steer)
                command = next(
                    item for item in applied_commands
                    if item.command_id == command_id
                )
                return SafePointCompletion(
                    command=command,
                    execution=updated,
                    attempt=attempt,
                    checkpoint=checkpoint,
                    applied_commands=tuple(applied_commands),
                )
            if stored.status is not CommandStatus.APPLYING:
                raise AgentSafePointConflict("command_state_invalid", "safe point command is no longer applying")
            if stored.kind is CommandKind.STEP:
                if not managed_action_id or stored.result_json.get("managed_action_id"):
                    raise AgentSafePointConflict("step_permit_consumed", "step permit was already consumed")
                connection.execute(
                    "UPDATE commands SET result_json = ?, updated_at = ? WHERE command_id = ? AND status = ?",
                    (_json({"managed_action_id": managed_action_id}), now, command_id, CommandStatus.APPLYING.value),
                )
            pausing = self.executions._transition_execution(
                connection, execution_id, expected_version=updated.status_version,
                target=ExecutionStatus.PAUSING, reason_code="step_at_safe_point",
            ) if updated.status is ExecutionStatus.RUNNING else updated
            if pausing.status is not ExecutionStatus.PAUSING:
                raise AgentSafePointConflict("execution_state_invalid", "Agent safe point must settle a pausing execution")
            updated = self.executions._transition_execution(
                connection, execution_id, expected_version=pausing.status_version,
                target=ExecutionStatus.PAUSED, reason_code=None, clear_owner=True,
            )
            ended = self.attempts._end_for_owner_loss(connection, attempt, outcome="paused_at_safe_point")
            self.executions._append_event(
                connection, execution_id=execution_id, execution_version=updated.status_version,
                kind="attempt.ended", payload={"attempt": ended.to_dict()}, created_at=ended.updated_at,
            )
            command = self.executions._transition_command(
                connection, command_id, expected_status=CommandStatus.APPLYING,
                target=CommandStatus.APPLIED, result_version=updated.status_version,
                receipt={"checkpoint_id": checkpoint.checkpoint_id, "safe_point": checkpoint.safe_point, "managed_action_id": managed_action_id},
                result_json=(
                    {"managed_action_id": managed_action_id}
                    if stored.kind is CommandKind.STEP
                    else None
                ),
            )
            if stored.kind is CommandKind.STEP:
                self.executions._append_event(
                    connection, execution_id=execution_id,
                    execution_version=updated.status_version,
                    kind="agent.action.completed",
                    payload={"action_id": managed_action_id, "command_id": command_id},
                    created_at=now,
                )
            applied_commands.append(command)
        completion = SafePointCompletion(
            command=command, execution=updated, attempt=attempt,
            checkpoint=checkpoint,
            applied_commands=tuple(applied_commands),
        )
        return completion

    def consume_agent_step_permit(self, *, execution_id: str, command_id: str, action_id: str) -> None:
        with self.executions._transaction() as connection:
            command = self.executions._get_command(connection, command_id)
            if command is None or command.execution_id != execution_id or command.kind is not CommandKind.STEP:
                raise AgentSafePointConflict("step_permit_missing", "step permit is not active")
            if command.status is not CommandStatus.APPLYING or command.result_json.get("managed_action_id"):
                raise AgentSafePointConflict("step_permit_consumed", "step permit was already consumed")
            connection.execute(
                "UPDATE commands SET result_json = ?, updated_at = ? WHERE command_id = ? AND status = ?",
                (_json({"managed_action_id": action_id}), time.time(), command_id, CommandStatus.APPLYING.value),
            )


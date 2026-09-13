"""Execution control waits operations on the owning service."""
from __future__ import annotations
import inspect
import time
from typing import Any, Mapping
from ..attempts import AttemptConflict, AttemptStatus
from ..checkpoints import CheckpointFragment
from ..effects import EffectStatus
from ..model import CommandKind, CommandStatus, ControlCommand, ExecutionRecord, ExecutionStatus
from ..store import ExecutionConflict, _json
from ..safe_points import AgentSafePointConflict

from .shared import (
    ControlDispatch,
    SafePointCompletion,
    WaitSuspension,
    _log,
)


class WaitsOperations:
    async def request_wait_answer(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: Mapping[str, Any],
        wait_id: str,
        generation: int,
        answer: Any,
    ) -> ControlDispatch:
        """Commit one exact durable answer through the canonical command log."""
        from ..waits import DurableWaitStore

        waits = DurableWaitStore(self.executions)
        command, wait, duplicate = waits.resolve_with_command(
            command_id=command_id, execution_id=execution_id,
            expected_version=expected_version, actor=actor,
            kind=CommandKind.WAIT_ANSWER, wait_id=wait_id,
            generation=generation, answer=answer,
        )
        execution = self.executions.get_execution(execution_id)
        if execution is None:
            raise ExecutionConflict("not_found", f"execution not found: {execution_id}")
        execution = await self._resume_wait_if_required(wait=wait, execution=execution)
        return ControlDispatch(command=command, execution=execution, delivered=False)


    async def request_wait_decline(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: Mapping[str, Any],
        wait_id: str,
        generation: int,
        reason: Any = None,
    ) -> ControlDispatch:
        """Commit one exact durable decline through the canonical command log."""
        from ..waits import DurableWaitStore

        waits = DurableWaitStore(self.executions)
        command, wait, duplicate = waits.resolve_with_command(
            command_id=command_id, execution_id=execution_id,
            expected_version=expected_version, actor=actor,
            kind=CommandKind.WAIT_DECLINE, wait_id=wait_id,
            generation=generation, answer=reason,
        )
        execution = self.executions.get_execution(execution_id)
        if execution is None:
            raise ExecutionConflict("not_found", f"execution not found: {execution_id}")
        execution = await self._resume_wait_if_required(wait=wait, execution=execution)
        return ControlDispatch(command=command, execution=execution, delivered=False)


    async def _resume_wait_if_required(
        self,
        *,
        wait: Any,
        execution: ExecutionRecord,
    ) -> ExecutionRecord:
        """Apply the wait policy through the normal continuation activation.

        A wait outcome is durable before this method is entered.  If this
        process stops before it obtains the next attempt, the execution stays
        paused with the exact checkpoint recorded on the wait; startup or a
        scheduler can submit the deterministic internal continue intent.
        """
        if (
            execution.status is not ExecutionStatus.PAUSED
            or execution.current_attempt_id is not None
            or execution.reason_code not in {"wait_open", "system_access_required"}
        ):
            return execution
        wait_checkpoint_id = getattr(wait, "checkpoint_id", None)
        if not wait_checkpoint_id or wait_checkpoint_id != execution.checkpoint_head_id:
            return execution
        outcome = wait.outcome
        policy_key = {
            "answered": "on_answer", "declined": "on_decline", "timeout": "on_timeout",
            "granted": "on_grant",
        }.get(outcome)
        if policy_key is None:
            return execution
        disposition = str(wait.policy_snapshot.get(policy_key, "continue" if outcome in {"answered", "granted"} else "fail"))
        if disposition == "continue":
            scheduler = self._wait_resume_scheduler
            if scheduler is not None:
                scheduled = scheduler(wait, execution)
                if inspect.isawaitable(scheduled):
                    await scheduled
                latest = self.executions.get_execution(execution.execution_id)
                return latest or execution
            # This is an internal command, not a second public protocol.
            # Its deterministic id makes recovery idempotent after an
            # answer has committed but before activation has begun.
            resume = await self.request_continue(
                command_id=f"wait-resume:{wait.wait_id}:{outcome}",
                execution_id=execution.execution_id,
                expected_version=execution.status_version,
                actor={"surface": "durable-wait", "wait_id": wait.wait_id},
            )
            return resume.execution
        if disposition == "fail":
            return self.executions.transition_execution(
                execution.execution_id,
                expected_version=execution.status_version,
                target=ExecutionStatus.FAILED,
                reason_code=f"wait_{outcome}",
            )
        if disposition == "cancel":
            cancelled = await self.request_cancel(
                command_id=f"wait-cancel:{wait.wait_id}:{outcome}",
                execution_id=execution.execution_id,
                expected_version=execution.status_version,
                actor={"surface": "durable-wait", "wait_id": wait.wait_id},
                reason_code=f"wait_{outcome}",
            )
            return cancelled.execution
        raise ExecutionConflict("invalid_wait_policy", "wait policy has an invalid outcome disposition")


    async def recover_wait_outcomes(self) -> tuple[ExecutionRecord, ...]:
        """Resume or settle outcomes committed before a process stopped.

        The wait row is the durable saga record.  A second invocation observes
        the new active/terminal execution state and cannot create another
        attempt or replay the checkpoint frontier.
        """
        from ..waits import DurableWaitStore

        waits = DurableWaitStore(self.executions)
        # System access is resolved only by a worker on the execution host.
        # The report is read-only and a failed or unknown probe leaves the
        # durable wait open for a later reconciliation pass.
        from openprogram.system_access import report as system_access_report
        open_waits = waits.list_open()
        system_access_snapshot = None
        if any(wait.kind == "system_access" for wait in open_waits):
            try:
                # One fresh executor probe is shared by every open wait in this
                # reconciliation pass; report() itself never requests access.
                system_access_snapshot = system_access_report()
            except Exception:
                _log.debug("fresh system access reconciliation failed", exc_info=True)
        for wait in open_waits:
            if wait.kind != "system_access" or system_access_snapshot is None:
                continue
            try:
                granted = waits.resolve_system_access(
                    wait.wait_id, report=system_access_snapshot, owner_id=self.owner_id,
                )
            except Exception:
                granted = None
            if granted is not None:
                try:
                    from openprogram.events import emit_ws_frame
                    execution = self.executions.get_execution(granted.execution_id)
                    emit_ws_frame({"type": "system_access.resolved", "data": {
                        "wait_id": granted.wait_id,
                        "execution_id": granted.execution_id,
                        "session_id": execution.session_id if execution else None,
                        "required_capabilities": list(granted.request.get("required_capabilities", [])),
                    }})
                except Exception:
                    _log.debug("failed to publish system access resolution", exc_info=True)

        from openprogram.agent.permissions import reconcile_permission_waits
        permission_sessions = set()
        for wait in DurableWaitStore(self.executions).list_open():
            if wait.kind == "approval" and wait.request.get("approval_reason") == "MODE_APPROVAL":
                execution = self.executions.get_execution(wait.execution_id)
                if execution is not None:
                    permission_sessions.add(execution.session_id)
        for session_id in permission_sessions:
            await reconcile_permission_waits(session_id, service=self)
        recovered: list[ExecutionRecord] = []
        for wait in waits.list_outcomes():
            execution = self.executions.get_execution(wait.execution_id)
            if execution is None:
                continue
            updated = await self._resume_wait_if_required(
                wait=wait, execution=execution,
            )
            if updated is not execution:
                recovered.append(updated)
        return tuple(recovered)


    def open_wait_at_safe_point(
        self,
        *,
        execution_id: str,
        attempt_id: str,
        generation: int,
        expected_version: int,
        fragment: CheckpointFragment,
        kind: str,
        request: Mapping[str, Any],
        policy_snapshot: Mapping[str, Any],
        expires_at: float,
        wait_id: str | None = None,
        agent_checkpoint: Any | None = None,
    ) -> WaitSuspension:
        """Publish a checkpoint, open a wait, and end its owner atomically.

        This is the only API for a resumable Agent question or approval.  It
        intentionally has no live-thread callback: after commit the prior
        attempt is fenced and the wait record is the only authority for a
        later answer, timeout, or restart recovery.
        """
        from ..waits import DurableWaitStore

        if policy_snapshot.get("version") != 1:
            raise AgentSafePointConflict("invalid_wait_policy", "safe-point wait policy must use version 1")
        policy_fields = ("on_grant",) if kind == "system_access" else ("on_answer", "on_decline", "on_timeout")
        for field in policy_fields:
            value = policy_snapshot.get(field)
            if value is not None and value not in {"continue", "fail", "cancel"}:
                raise AgentSafePointConflict("invalid_wait_policy", f"{field} has an invalid disposition")
        wall_deadline = policy_snapshot.get("wall_deadline_at")
        if wall_deadline is not None and (
            type(wall_deadline) not in {int, float} or expires_at == 0 or expires_at > wall_deadline
        ):
            raise AgentSafePointConflict("invalid_wait_policy", "wait expiry exceeds its wall deadline")
        try:
            with self.executions._transaction() as connection:
                attempt = self.attempts._require(connection, attempt_id)
                self.attempts._validate_generation(attempt, generation)
                execution = self.executions._require_execution(connection, execution_id)
                if (
                    attempt.execution_id != execution_id
                    or attempt.status is not AttemptStatus.ACTIVE
                    or execution.status is not ExecutionStatus.RUNNING
                    or execution.current_attempt_id != attempt_id
                    or execution.owner_lease.get("generation") != generation
                    or attempt.lease_expires_at <= time.time()
                ):
                    raise AgentSafePointConflict("stale_attempt", "wait safe-point owner is stale")
                if execution.status_version != expected_version:
                    raise AgentSafePointConflict("stale_version", "wait safe point has a stale execution version")
                if fragment.safe_point_kind not in execution.capabilities.safe_point_kinds:
                    raise AgentSafePointConflict("invalid_safe_point", "wait is not at a declared execution safe point")
                state_refs = dict(fragment.state_refs)
                if agent_checkpoint is not None:
                    from openprogram.agent.continuation import AgentCheckpointV1

                    if not isinstance(agent_checkpoint, AgentCheckpointV1):
                        raise AgentSafePointConflict("checkpoint_schema_invalid", "wait checkpoint must use AgentCheckpointV1")
                    agent_checkpoint.validate()
                    for descriptor_ref, payload in agent_checkpoint.blob_payloads.items():
                        descriptor = self.executions._put_state_blob_in_transaction(
                            connection, execution_id=execution_id, payload=payload,
                            media_type="application/json", schema_version=1,
                        )
                        if descriptor["ref"] != descriptor_ref:
                            raise AgentSafePointConflict("state_ref_invalid", "Agent checkpoint blob descriptor does not match payload")
                    descriptor = self.executions._put_state_blob_in_transaction(
                        connection, execution_id=execution_id,
                        payload=agent_checkpoint.to_bytes(),
                        media_type="application/json", schema_version=1,
                    )
                    state_refs["agent_checkpoint"] = descriptor
                    state_refs["agent_checkpoint_v1"] = {
                        key: agent_checkpoint.payload[key]
                        for key in ("safe_point", "frontier", "turn", "current_decision", "next_tool_index")
                    }
                checkpoint, updated = self.checkpoints._publish_in_transaction(
                    connection,
                    execution_id=execution_id,
                    expected_version=expected_version,
                    revision_id=execution.revision_id,
                    parent_checkpoint_id=execution.checkpoint_head_id,
                    frontier=fragment.frontier,
                    completed_frontier=fragment.completed_frontier,
                    state_refs=state_refs,
                    completed_actions=fragment.completed_actions,
                    effect_receipts=fragment.effect_receipts,
                    child_frontier=fragment.child_frontier,
                    pending_command_ids=fragment.pending_command_ids,
                    created_by_attempt_id=attempt_id,
                )
                durable_waits = DurableWaitStore(self.executions)
                opened_id = durable_waits._open_in_transaction(
                    connection,
                    execution_id=execution_id,
                    attempt_id=attempt_id,
                    generation=generation,
                    kind=kind,
                    request=request,
                    policy_snapshot=policy_snapshot,
                    expires_at=expires_at,
                    checkpoint_id=checkpoint.checkpoint_id,
                    wait_id=wait_id,
                )
                wait_reason = "system_access_required" if kind == "system_access" else "wait_open"
                pausing = self.executions._transition_execution(
                    connection, execution_id,
                    expected_version=updated.status_version,
                    target=ExecutionStatus.PAUSING,
                    reason_code=wait_reason,
                )
                paused = self.executions._transition_execution(
                    connection, execution_id,
                    expected_version=pausing.status_version,
                    target=ExecutionStatus.PAUSED,
                    reason_code=wait_reason,
                    clear_owner=True,
                )
                ended = self.attempts._end_for_owner_loss(
                    connection, attempt, outcome="waiting_at_safe_point",
                )
                self.executions._append_event(
                    connection, execution_id=execution_id,
                    execution_version=paused.status_version,
                    kind="attempt.ended", payload={"attempt": ended.to_dict()},
                    created_at=ended.updated_at,
                )
        except AgentSafePointConflict:
            raise


        except (ExecutionConflict, AttemptConflict) as exc:
            raise AgentSafePointConflict(getattr(exc, "code", "wait_safe_point_failed"), str(exc)) from exc
        self.registry.unbind(execution_id, attempt_id=attempt_id, generation=generation)
        wait = DurableWaitStore(self.executions).get_wait(opened_id)
        assert wait is not None
        suspension = WaitSuspension(wait=wait, checkpoint=checkpoint, execution=paused, attempt=ended)
        observer = self._wait_suspension_observer
        if observer is not None:
            observer(suspension)
        return suspension


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
        if safe_point_kind not in {"agent.provider.decision.after", "agent.tool.action.after"}:
            raise AgentSafePointConflict("invalid_safe_point", "unsupported Agent safe point")
        try:
            with self.executions._transaction() as connection:
                attempt = self.attempts._require(connection, attempt_id)
                self.attempts._validate_generation(attempt, generation)
                execution = self.executions._require_execution(connection, execution_id)
                if (
                    attempt.execution_id != execution_id
                    or execution.current_attempt_id != attempt_id
                    or attempt.status is not AttemptStatus.ACTIVE
                    or execution.owner_lease.get("generation") != generation
                    or attempt.lease_expires_at <= time.time()
                ):
                    raise AgentSafePointConflict("stale_attempt", "Agent safe point owner is stale")
                if execution.status_version != expected_version:
                    raise AgentSafePointConflict("stale_version", "Agent safe point has a stale execution version")
                effect = self.effects._require(connection, effect_id)
                if effect.execution_id != execution_id or effect.attempt_id != attempt_id or effect.status is not EffectStatus.DISPATCHED:
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
                connection.execute(
                    "UPDATE effects SET status = ?, receipt_json = ?, updated_at = ?, resolved_at = ? WHERE effect_id = ?",
                    (EffectStatus.COMMITTED.value, _json({**dict(terminal_receipt), "receipt_ref": receipt_ref}), now, now, effect_id),
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
            self._observe_paused(completion.execution)
            return completion
        except AgentSafePointConflict:
            raise
        except (ExecutionConflict, AttemptConflict) as exc:
            raise AgentSafePointConflict(getattr(exc, "code", "safe_point_failed"), str(exc)) from exc


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


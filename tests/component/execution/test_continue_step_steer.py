from __future__ import annotations

import asyncio

import pytest

from openprogram.execution.attempts import AttemptConflict, AttemptStore
from openprogram.execution.checkpoints import CheckpointFragment
from openprogram.execution.control import RuntimeControlService
from openprogram.execution.driver import ActivationInput, DriverBinding, DriverRegistry
from openprogram.execution.effects import EffectClassification, EffectStatus, EffectStore
from openprogram.execution.model import CapabilitySet, CommandStatus, ExecutionStatus
from openprogram.execution.store import ExecutionStore


def _paused(tmp_path):
    store = ExecutionStore(tmp_path / "execution.db")
    revision = store.create_revision(manifest={"entrypoint": "fake"})
    execution = store.create_execution(
        execution_id="exec_1",
        run_id="run_1",
        session_id="session_1",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(
            pause=True,
            step=True,
            steer=True,
            safe_point_kinds=("action.after", "control.step"),
            state_schema_version=1,
        ),
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="worker",
        ttl_seconds=30,
        attempt_id="attempt_1",
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    checkpoints = RuntimeControlService(store, attempts, DriverRegistry()).checkpoints
    checkpoint, running = checkpoints.publish(
        running.execution_id,
        expected_version=running.status_version,
        revision_id=running.revision_id,
        parent_checkpoint_id=None,
        frontier=({"step_id": "start", "phase": "after"},),
        state_refs={"program": {"cursor": 0}},
        completed_actions=(),
        effect_receipts=(),
        child_frontier={},
        pending_command_ids=(),
        created_by_attempt_id=active.attempt_id,
    )
    service = RuntimeControlService(store, attempts, DriverRegistry())
    pausing = asyncio.run(
        service.request_pause(
            command_id="pause_1",
            execution_id=running.execution_id,
            expected_version=running.status_version,
            actor={"surface": "test"},
        )
    )
    paused = service.arrive_safe_point(
        attempt_id=active.attempt_id,
        generation=active.generation,
        command_id="pause_1",
        expected_execution_version=pausing.execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="action.after",
            frontier=({"step_id": "start", "phase": "after"},),
            state_refs={"program": {"cursor": 0}},
        ),
    ).execution
    assert paused.status is ExecutionStatus.PAUSED
    assert paused.checkpoint_head_id is not None
    return store, attempts, service, paused


def test_continue_reuses_execution_and_creates_one_new_attempt(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)
    result = asyncio.run(
        service.request_continue(
            command_id="continue_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
        )
    )
    assert result.execution.status is ExecutionStatus.RUNNING
    assert result.command.status is CommandStatus.APPLIED
    assert result.execution.revision_id == paused.revision_id
    assert result.execution.current_attempt_id is not None
    assert len(store.list_commands(paused.execution_id)) == 2
    current = attempts.get(result.execution.current_attempt_id)
    assert current is not None and current.generation == 2


def test_step_is_one_permit_and_atomically_pauses_with_receipt(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)
    steer = service.request_steer(
        command_id="steer_1",
        execution_id=paused.execution_id,
        expected_version=paused.status_version,
        actor={"surface": "test"},
        payload={"message": "use source A"},
    )
    assert steer.command.status is CommandStatus.ACCEPTED
    result = asyncio.run(
        service.request_step(
            command_id="step_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
        )
    )
    assert result.command.status is CommandStatus.APPLYING
    completed = service.arrive_step_safe_point(
        attempt_id=result.execution.current_attempt_id,
        generation=result.execution.owner_lease["generation"],
        command_id="step_1",
        expected_execution_version=result.execution.status_version,
        safe_point_kind="control.step",
        frontier=({"step_id": "next", "phase": "after"},),
        state_refs={"program": {"cursor": 1}},
        managed_action={"action_id": "action_1"},
    )
    assert completed.execution.status is ExecutionStatus.PAUSED
    assert completed.attempt.status.value == "ended"
    assert completed.command.status is CommandStatus.APPLIED
    events = store.list_events(paused.execution_id)
    applied = [event for event in events if event.kind == "command.applied"]
    receipt = next(event.payload["receipt"] for event in applied if event.command_id == "step_1")
    assert receipt["checkpoint_id"] == completed.checkpoint.checkpoint_id
    assert receipt["safe_point"]["step_id"] == "next"
    assert store.get_command("steer_1").status is CommandStatus.APPLIED
    assert completed.checkpoint.state_refs["steering"][0]["payload"]["message"] == "use source A"

    repeated = service.arrive_step_safe_point(
        attempt_id=result.execution.current_attempt_id,
        generation=result.execution.owner_lease["generation"],
        command_id="step_1",
        expected_execution_version=completed.execution.status_version,
        safe_point_kind="control.step",
        frontier=({"step_id": "ignored"},),
        state_refs={},
        control_step={"step_id": "ignored"},
    )
    assert repeated.command == completed.command
    assert repeated.execution == completed.execution
    assert repeated.checkpoint == completed.checkpoint


def test_continue_activation_receives_paused_steering(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)
    steer = service.request_steer(
        command_id="steer_1",
        execution_id=paused.execution_id,
        expected_version=paused.status_version,
        actor={"surface": "test"},
        payload={"message": "continue with source B"},
    )
    seen = []

    def activate(attempt, activation):
        seen.append(activation)

    result = asyncio.run(
        service.request_continue(
            command_id="continue_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
            activator=activate,
        )
    )
    assert result.execution.status is ExecutionStatus.RUNNING
    assert seen[0].checkpoint.checkpoint_id == paused.checkpoint_head_id
    assert seen[0].checkpoint.content_hash == service.checkpoints.get(paused.checkpoint_head_id).content_hash
    assert seen[0].steer_inputs[0]["payload"] == dict(steer.command.payload)


def test_activation_input_rejects_nested_mutation_and_preserves_checkpoint_hash(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)
    checkpoint = service.checkpoints.get(paused.checkpoint_head_id)
    assert checkpoint is not None
    activation = ActivationInput(
        checkpoint=checkpoint,
        steer_inputs=(
            {
                "command_id": "steer_1",
                "payload": {"nested": {"items": [1], "flags": frozenset({"a"})}},
            },
        ),
    )
    before = checkpoint.content_hash
    with pytest.raises(TypeError):
        activation.checkpoint.frontier[0]["step_id"] = "changed"
    with pytest.raises(AttributeError):
        activation.steer_inputs[0]["payload"]["nested"]["items"].append(2)
    with pytest.raises(AttributeError):
        activation.steer_inputs[0]["payload"]["nested"]["flags"].add("b")
    assert checkpoint.content_hash == before
    assert service.checkpoints.get(checkpoint.checkpoint_id).content_hash == before


def test_invalid_activation_binding_uses_activation_failure_lifecycle(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)

    def activate(attempt, activation):
        return DriverBinding(
            execution_id=attempt.execution_id,
            attempt_id="other-attempt",
            generation=attempt.generation,
            driver=object(),
            handle=object(),
        )

    result = asyncio.run(
        service.request_continue(
            command_id="continue_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
            activator=activate,
        )
    )
    assert result.issue_code == "activation_failed"
    assert result.command.rejection_code == "activation_failed"
    assert result.execution.status is ExecutionStatus.PAUSED
    assert result.execution.current_attempt_id is None


def test_activation_failure_rejects_continue_and_releases_attempt(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)

    def fail(attempt, activation):
        raise RuntimeError("activation failed")

    result = asyncio.run(
        service.request_continue(
            command_id="continue_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
            activator=fail,
        )
    )
    assert result.issue_code == "activation_failed"
    assert result.command.status is CommandStatus.REJECTED
    assert result.command.rejection_code == "activation_failed"
    assert result.execution.status is ExecutionStatus.PAUSED
    assert result.execution.current_attempt_id is None
    assert store.get_command("continue_1").status is CommandStatus.REJECTED


def test_activation_failure_rejects_step_and_allows_new_continue(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)

    def fail(attempt, activation):
        raise RuntimeError("activation failed")

    result = asyncio.run(
        service.request_step(
            command_id="step_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
            activator=fail,
        )
    )
    assert result.issue_code == "activation_failed"
    assert result.command.status is CommandStatus.REJECTED
    assert result.execution.status is ExecutionStatus.PAUSED
    current = store.get_execution(paused.execution_id)
    assert current is not None and current.current_attempt_id is None


def test_legacy_checkpoint_second_argument_is_not_an_activation_contract(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)

    def legacy(attempt, checkpoint):
        return checkpoint.state_refs

    result = asyncio.run(
        service.request_continue(
            command_id="continue_legacy",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
            activator=legacy,
        )
    )
    assert result.issue_code == "activation_failed"
    assert result.command.status is CommandStatus.REJECTED
    assert result.execution.status is ExecutionStatus.PAUSED


def test_safe_point_rejects_missing_and_continue_commands_explicitly(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)
    continued = asyncio.run(
        service.request_continue(
            command_id="continue_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
        )
    )
    with pytest.raises(AttemptConflict) as unsupported:
        service.arrive_safe_point(
            attempt_id=continued.execution.current_attempt_id,
            generation=continued.execution.owner_lease["generation"],
            command_id="continue_1",
            expected_execution_version=continued.execution.status_version,
            fragment=CheckpointFragment(
                safe_point_kind="action.after",
                frontier=({"step_id": "invalid"},),
                state_refs={},
            ),
        )
    assert unsupported.value.code == "unsupported_command"
    with pytest.raises(AttemptConflict) as missing:
            service.arrive_safe_point(
                attempt_id=continued.execution.current_attempt_id,
                generation=continued.execution.owner_lease["generation"],
                command_id="missing",
                expected_execution_version=continued.execution.status_version,
                fragment=CheckpointFragment(
                    safe_point_kind="action.after",
                    frontier=({"step_id": "invalid"},),
                    state_refs={},
                ),
        )
    assert missing.value.code == "command_not_found"


def test_late_success_after_cancel_finishes_cancel_intent(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def activate(attempt, activation):
            entered.set()
            await release.wait()

        continuation = asyncio.create_task(
            service.request_continue(
                command_id="continue_1",
                execution_id=paused.execution_id,
                expected_version=paused.status_version,
                actor={"surface": "test"},
                activator=activate,
            )
        )
        await entered.wait()
        running = store.get_execution(paused.execution_id)
        cancelled = await service.request_cancel(
            command_id="cancel_1",
            execution_id=paused.execution_id,
            expected_version=running.status_version,
            actor={"surface": "test"},
            reason_code="race",
        )
        release.set()
        late = await continuation
        return store, cancelled, late

    store, cancelled, late = asyncio.run(scenario())
    assert late.command.status is CommandStatus.REJECTED
    assert cancelled.execution.status is ExecutionStatus.CANCELLING
    assert store.get_command("cancel_1").status is CommandStatus.APPLIED
    execution = store.get_execution("exec_1")
    assert execution.status is ExecutionStatus.CANCELLED
    assert execution.current_attempt_id is None


def test_late_activation_failure_after_cancel_finishes_cancel_intent(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def activate(attempt, activation):
            entered.set()
            await release.wait()
            raise RuntimeError("late activation failure")

        continuation = asyncio.create_task(
            service.request_continue(
                command_id="continue_1",
                execution_id=paused.execution_id,
                expected_version=paused.status_version,
                actor={"surface": "test"},
                activator=activate,
            )
        )
        await entered.wait()
        running = store.get_execution(paused.execution_id)
        cancelled = await service.request_cancel(
            command_id="cancel_1",
            execution_id=paused.execution_id,
            expected_version=running.status_version,
            actor={"surface": "test"},
            reason_code="race",
        )
        release.set()
        late = await continuation
        return store, cancelled, late

    store, cancelled, late = asyncio.run(scenario())
    assert late.command.status is CommandStatus.REJECTED
    assert cancelled.execution.status is ExecutionStatus.CANCELLING
    assert store.get_command("cancel_1").status is CommandStatus.APPLIED
    execution = store.get_execution("exec_1")
    assert execution.status is ExecutionStatus.CANCELLED
    assert execution.current_attempt_id is None


@pytest.mark.parametrize("fail", [False, True])
def test_late_step_activation_after_cancel_finishes_cancel_intent(tmp_path, fail):
    store, attempts, service, paused = _paused(tmp_path)

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def activate(attempt, activation):
            entered.set()
            await release.wait()
            if fail:
                raise RuntimeError("late activation failure")

        step = asyncio.create_task(
            service.request_step(
                command_id="step_1",
                execution_id=paused.execution_id,
                expected_version=paused.status_version,
                actor={"surface": "test"},
                activator=activate,
            )
        )
        await entered.wait()
        running = store.get_execution(paused.execution_id)
        cancelled = await service.request_cancel(
            command_id="cancel_1",
            execution_id=paused.execution_id,
            expected_version=running.status_version,
            actor={"surface": "test"},
            reason_code="race",
        )
        release.set()
        late = await step
        return store, cancelled, late

    store, cancelled, late = asyncio.run(scenario())
    assert late.command.status is CommandStatus.REJECTED
    assert cancelled.execution.status is ExecutionStatus.CANCELLING
    assert store.get_command("cancel_1").status is CommandStatus.APPLIED
    execution = store.get_execution("exec_1")
    assert execution.status is ExecutionStatus.CANCELLED
    assert execution.current_attempt_id is None


def test_late_step_safe_point_after_cancel_finishes_and_retries_cancel(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def activate(attempt, activation):
            entered.set()
            await release.wait()

        step = asyncio.create_task(
            service.request_step(
                command_id="step_1",
                execution_id=paused.execution_id,
                expected_version=paused.status_version,
                actor={"surface": "test"},
                activator=activate,
            )
        )
        await entered.wait()
        running = store.get_execution(paused.execution_id)
        cancelled = await service.request_cancel(
            command_id="cancel_1",
            execution_id=paused.execution_id,
            expected_version=running.status_version,
            actor={"surface": "test"},
            reason_code="race",
        )
        point = await asyncio.to_thread(
            service.arrive_safe_point,
            attempt_id=running.current_attempt_id,
            generation=running.owner_lease["generation"],
            command_id="step_1",
            expected_execution_version=cancelled.execution.status_version,
            fragment=CheckpointFragment(
                safe_point_kind="control.step",
                frontier=({"step_id": "late"},),
                state_refs={},
                control_step={"step_id": "late"},
            ),
        )
        release.set()
        activation_result = await step
        repeated = await asyncio.to_thread(
            service.arrive_safe_point,
            attempt_id=running.current_attempt_id,
            generation=running.owner_lease["generation"],
            command_id="step_1",
            expected_execution_version=cancelled.execution.status_version,
            fragment=CheckpointFragment(
                safe_point_kind="control.step",
                frontier=({"step_id": "ignored"},),
                state_refs={},
                control_step={"step_id": "ignored"},
            ),
        )
        return cancelled, point, activation_result, repeated

    cancelled, point, activation_result, repeated = asyncio.run(scenario())
    assert cancelled.execution.status is ExecutionStatus.CANCELLING
    assert point.command.status is CommandStatus.REJECTED
    assert activation_result.command.status is CommandStatus.REJECTED
    assert repeated.command == point.command
    assert repeated.execution.status is ExecutionStatus.CANCELLED
    assert store.get_command("cancel_1").status is CommandStatus.APPLIED
    assert store.get_execution("exec_1").current_attempt_id is None


def test_late_step_safe_point_with_unresolved_effect_requires_reconciliation(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)
    started = asyncio.run(
        service.request_step(
            command_id="step_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
        )
    )
    effect = EffectStore(store).register(
        effect_id="effect_1",
        execution_id=paused.execution_id,
        attempt_id=started.execution.current_attempt_id,
        action_id="send_message",
        classification=EffectClassification.NONREPEATABLE,
        idempotency_key=None,
        metadata={},
    )
    EffectStore(store).mark_dispatched(effect.effect_id, expected_status=effect.status)
    cancelling = asyncio.run(
        service.request_cancel(
            command_id="cancel_1",
            execution_id=paused.execution_id,
            expected_version=started.execution.status_version,
            actor={"surface": "test"},
            reason_code="race",
        )
    )
    completion = service.arrive_safe_point(
        attempt_id=started.execution.current_attempt_id,
        generation=started.execution.owner_lease["generation"],
        command_id="step_1",
        expected_execution_version=cancelling.execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="control.step",
            frontier=({"step_id": "late"},),
            state_refs={},
            control_step={"step_id": "late"},
        ),
    )
    assert completion.execution.status is ExecutionStatus.RECONCILIATION_REQUIRED
    assert completion.command.status is CommandStatus.REJECTED
    assert store.get_command("cancel_1").status is CommandStatus.APPLYING
    resolved = service.resolve_effect(
        effect_id="effect_1",
        expected_status=EffectStatus.DISPATCHED,
        outcome=EffectStatus.COMMITTED,
        receipt={"provider_message_id": "message_1"},
    )
    assert resolved.execution.status is ExecutionStatus.CANCELLED
    assert resolved.command is not None
    assert resolved.command.status is CommandStatus.APPLIED


def test_pause_and_step_applied_fast_paths_reject_cross_execution_command(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)
    other = store.create_execution(
        execution_id="exec_2",
        run_id="run_2",
        session_id="session_2",
        revision_id=paused.revision_id,
        capabilities=paused.capabilities,
    )
    leased, reserved = attempts.lease(
        other.execution_id,
        expected_version=other.status_version,
        owner_id="other-worker",
        ttl_seconds=30,
        attempt_id="other_attempt",
    )
    other_attempt, other_running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    other_checkpoint, other_running = service.checkpoints.publish(
        other.execution_id,
        expected_version=other_running.status_version,
        revision_id=other_running.revision_id,
        parent_checkpoint_id=None,
        frontier=({"step_id": "other-start"},),
        state_refs={},
        completed_actions=(),
        effect_receipts=(),
        child_frontier={},
        pending_command_ids=(),
        created_by_attempt_id=other_attempt.attempt_id,
    )
    other_pause = asyncio.run(
        service.request_pause(
            command_id="pause_other",
            execution_id=other.execution_id,
            expected_version=other_running.status_version,
            actor={"surface": "test"},
        )
    )
    service.arrive_safe_point(
        attempt_id=other_attempt.attempt_id,
        generation=other_attempt.generation,
        command_id="pause_other",
        expected_execution_version=other_pause.execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="action.after",
            frontier=({"step_id": "other-pause"},),
            state_refs={},
        ),
    )
    other_paused = store.get_execution(other.execution_id)
    assert other_paused is not None
    other_step = asyncio.run(
        service.request_step(
            command_id="step_other",
            execution_id=other.execution_id,
            expected_version=other_paused.status_version,
            actor={"surface": "test"},
        )
    )
    service.arrive_step_safe_point(
        attempt_id=other_step.execution.current_attempt_id,
        generation=other_step.execution.owner_lease["generation"],
        command_id="step_other",
        expected_execution_version=other_step.execution.status_version,
        safe_point_kind="control.step",
        frontier=({"step_id": "other-step"},),
        state_refs={},
        control_step={"step_id": "other-step"},
    )
    continued = asyncio.run(
        service.request_continue(
            command_id="continue_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
        )
    )
    for command_id in (other_pause.command.command_id, other_step.command.command_id):
        with pytest.raises(AttemptConflict) as mismatch:
                service.arrive_safe_point(
                    attempt_id=continued.execution.current_attempt_id,
                    generation=continued.execution.owner_lease["generation"],
                    command_id=command_id,
                    expected_execution_version=continued.execution.status_version,
                    fragment=CheckpointFragment(
                        safe_point_kind=(
                            "control.step"
                            if command_id == "step_other"
                            else "action.after"
                        ),
                        frontier=({"step_id": "invalid"},),
                        state_refs={},
                        control_step=(
                            {"step_id": "invalid"}
                            if command_id == "step_other"
                            else None
                        ),
                    ),
            )
        assert mismatch.value.code == "command_mismatch"


@pytest.mark.parametrize("fail", [False, True])
def test_late_activation_after_pause_finishes_pause_intent(tmp_path, fail):
    store, attempts, service, paused = _paused(tmp_path)

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def activate(attempt, activation):
            entered.set()
            await release.wait()
            if fail:
                raise RuntimeError("late activation failure")

        continuation = asyncio.create_task(
            service.request_continue(
                command_id="continue_1",
                execution_id=paused.execution_id,
                expected_version=paused.status_version,
                actor={"surface": "test"},
                activator=activate,
            )
        )
        await entered.wait()
        running = store.get_execution(paused.execution_id)
        pausing = await service.request_pause(
            command_id="pause_2",
            execution_id=paused.execution_id,
            expected_version=running.status_version,
            actor={"surface": "test"},
        )
        release.set()
        late = await continuation
        return pausing, late

    pausing, late = asyncio.run(scenario())
    assert pausing.execution.status is ExecutionStatus.PAUSING
    assert late.command.status is CommandStatus.REJECTED
    assert store.get_command("pause_2").status is CommandStatus.APPLIED
    execution = store.get_execution("exec_1")
    assert execution.status is ExecutionStatus.PAUSED
    assert execution.current_attempt_id is None


def test_step_owner_loss_rejects_step_command_and_is_idempotent(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)
    started = asyncio.run(
        service.request_step(
            command_id="step_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
        )
    )
    recovered = service.recover_owner_loss(paused.execution_id)
    assert recovered.execution.status is ExecutionStatus.INTERRUPTED
    assert recovered.command is not None
    assert recovered.command.command_id == started.command.command_id
    assert recovered.command.status is CommandStatus.REJECTED
    repeated = asyncio.run(
        service.request_step(
            command_id="step_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
        )
    )
    assert repeated.command == recovered.command


def test_pause_safe_point_commits_steer_checkpoint_and_receipts_together(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)
    continued = asyncio.run(
        service.request_continue(
            command_id="continue_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
        )
    )
    steer = service.request_steer(
        command_id="steer_1",
        execution_id=paused.execution_id,
        expected_version=continued.execution.status_version,
        actor={"surface": "test"},
        payload={"message": "apply at pause"},
    )
    pausing = asyncio.run(
        service.request_pause(
            command_id="pause_2",
            execution_id=paused.execution_id,
            expected_version=continued.execution.status_version,
            actor={"surface": "test"},
        )
    )
    completed = service.arrive_safe_point(
        attempt_id=continued.execution.current_attempt_id,
        generation=continued.execution.owner_lease["generation"],
        command_id="pause_2",
        expected_execution_version=pausing.execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="action.after",
            frontier=({"step_id": "pause", "phase": "after"},),
            state_refs={"program": {"cursor": 2}},
        ),
    )
    assert completed.checkpoint is not None
    assert completed.checkpoint.state_refs["steering"][0]["payload"] == dict(steer.command.payload)
    assert store.get_command("steer_1").status is CommandStatus.APPLIED
    assert "steer_1" not in completed.checkpoint.pending_command_ids
    events = store.list_events(paused.execution_id)
    receipts = [event.payload.get("receipt") for event in events if event.kind == "command.applied"]
    assert any(item and item["checkpoint_id"] == completed.checkpoint.checkpoint_id for item in receipts)

    repeated = service.arrive_safe_point(
        attempt_id=continued.execution.current_attempt_id,
        generation=continued.execution.owner_lease["generation"],
        command_id="pause_2",
        expected_execution_version=completed.execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="action.after",
            frontier=({"step_id": "ignored", "phase": "after"},),
            state_refs={},
        ),
    )
    assert repeated.command == completed.command
    assert repeated.execution == completed.execution
    assert repeated.checkpoint == completed.checkpoint


def test_pause_safe_point_rolls_back_when_attempt_close_fails(tmp_path, monkeypatch):
    store, attempts, service, paused = _paused(tmp_path)
    continued = asyncio.run(
        service.request_continue(
            command_id="continue_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
        )
    )
    steer = service.request_steer(
        command_id="steer_1",
        execution_id=paused.execution_id,
        expected_version=continued.execution.status_version,
        actor={"surface": "test"},
        payload={"message": "rollback if close fails"},
    )
    pausing = asyncio.run(
        service.request_pause(
            command_id="pause_2",
            execution_id=paused.execution_id,
            expected_version=continued.execution.status_version,
            actor={"surface": "test"},
        )
    )

    def fail_close(*args, **kwargs):
        raise RuntimeError("injected close failure")

    monkeypatch.setattr(attempts, "_finish_in_transaction", fail_close)
    with pytest.raises(RuntimeError, match="injected close failure"):
        service.arrive_safe_point(
            attempt_id=continued.execution.current_attempt_id,
            generation=continued.execution.owner_lease["generation"],
            command_id="pause_2",
            expected_execution_version=pausing.execution.status_version,
            fragment=CheckpointFragment(
                safe_point_kind="action.after",
                frontier=({"step_id": "pause", "phase": "after"},),
                state_refs={"program": {"cursor": 2}},
            ),
        )
    after = store.get_execution(paused.execution_id)
    assert after is not None
    assert after.status is ExecutionStatus.PAUSING
    assert after.checkpoint_head_id == continued.execution.checkpoint_head_id
    assert store.get_command(steer.command.command_id).status is CommandStatus.ACCEPTED


def test_running_steer_is_consumed_at_safe_point_once(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)
    continued = asyncio.run(
        service.request_continue(
            command_id="continue_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
        )
    )
    steer = service.request_steer(
        command_id="steer_1",
        execution_id=paused.execution_id,
        expected_version=continued.execution.status_version,
        actor={"surface": "test"},
        payload={"message": "apply while running"},
    )
    completed = service.arrive_safe_point(
        attempt_id=continued.execution.current_attempt_id,
        generation=continued.execution.owner_lease["generation"],
        command_id=steer.command.command_id,
        expected_execution_version=continued.execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="action.after",
            frontier=({"step_id": "running-steer", "phase": "after"},),
            state_refs={"program": {"cursor": 3}},
        ),
    )
    assert completed.execution.status is ExecutionStatus.RUNNING
    assert completed.checkpoint is not None
    assert completed.checkpoint.state_refs["steering"][0]["payload"] == dict(steer.command.payload)
    assert completed.command.status is CommandStatus.APPLIED
    applied_events = [event for event in store.list_events(paused.execution_id) if event.kind == "command.applied"]
    assert len([event for event in applied_events if event.command_id == "steer_1"]) == 1
    receipt = next(event.payload["receipt"] for event in applied_events if event.command_id == "steer_1")
    assert receipt["checkpoint_id"] == completed.checkpoint.checkpoint_id

    repeated = service.arrive_safe_point(
        attempt_id=continued.execution.current_attempt_id,
        generation=continued.execution.owner_lease["generation"],
        command_id=steer.command.command_id,
        expected_execution_version=completed.execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="action.after",
            frontier=({"step_id": "ignored", "phase": "after"},),
            state_refs={"program": {"cursor": 4}},
        ),
    )
    assert repeated.checkpoint == completed.checkpoint
    assert len([event for event in store.list_events(paused.execution_id) if event.kind == "command.applied" and event.command_id == "steer_1"]) == 1


def test_running_steer_safe_point_rolls_back_on_command_failure(tmp_path, monkeypatch):
    store, attempts, service, paused = _paused(tmp_path)
    continued = asyncio.run(
        service.request_continue(
            command_id="continue_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
        )
    )
    steer = service.request_steer(
        command_id="steer_1",
        execution_id=paused.execution_id,
        expected_version=continued.execution.status_version,
        actor={"surface": "test"},
        payload={"message": "rollback"},
    )
    original = store._transition_command

    def fail_after_checkpoint(connection, command_id, **kwargs):
        if kwargs.get("target") is CommandStatus.APPLIED:
            raise RuntimeError("injected command failure")
        return original(connection, command_id, **kwargs)

    monkeypatch.setattr(store, "_transition_command", fail_after_checkpoint)
    with pytest.raises(RuntimeError, match="injected command failure"):
        service.arrive_safe_point(
            attempt_id=continued.execution.current_attempt_id,
            generation=continued.execution.owner_lease["generation"],
            command_id=steer.command.command_id,
            expected_execution_version=continued.execution.status_version,
            fragment=CheckpointFragment(
                safe_point_kind="action.after",
                frontier=({"step_id": "rollback", "phase": "after"},),
                state_refs={"program": {"cursor": 5}},
            ),
        )
    after = store.get_execution(paused.execution_id)
    assert after is not None
    assert after.status is ExecutionStatus.RUNNING
    assert after.checkpoint_head_id == continued.execution.checkpoint_head_id
    assert store.get_command(steer.command.command_id).status is CommandStatus.ACCEPTED


def test_pause_priority_still_closes_attempt_when_arrival_names_steer(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)
    continued = asyncio.run(
        service.request_continue(
            command_id="continue_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
        )
    )
    steer = service.request_steer(
        command_id="steer_1",
        execution_id=paused.execution_id,
        expected_version=continued.execution.status_version,
        actor={"surface": "test"},
        payload={"message": "at pause boundary"},
    )
    pausing = asyncio.run(
        service.request_pause(
            command_id="pause_2",
            execution_id=paused.execution_id,
            expected_version=continued.execution.status_version,
            actor={"surface": "test"},
        )
    )
    completed = service.arrive_safe_point(
        attempt_id=continued.execution.current_attempt_id,
        generation=continued.execution.owner_lease["generation"],
        command_id=steer.command.command_id,
        expected_execution_version=pausing.execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="action.after",
            frontier=({"step_id": "pause-steer", "phase": "after"},),
            state_refs={"program": {"cursor": 6}},
        ),
    )
    assert completed.execution.status is ExecutionStatus.PAUSED
    assert store.get_command("pause_2").status is CommandStatus.APPLIED
    assert store.get_command("steer_1").status is CommandStatus.APPLIED

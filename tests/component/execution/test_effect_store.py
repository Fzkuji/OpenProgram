from __future__ import annotations

import time

import pytest

from openprogram.execution.attempts import AttemptStore
from openprogram.execution.effects import (
    EffectClassification,
    EffectConflict,
    EffectStatus,
    EffectStore,
)
from openprogram.execution.model import CapabilitySet
from openprogram.execution.store import ExecutionStore


def _stores(tmp_path):
    executions = ExecutionStore(tmp_path / "executions.db")
    revision = executions.create_revision(manifest={"entrypoint": "workflow"})
    execution = executions.create_execution(
        execution_id="exec_1",
        run_id="run_1",
        session_id="session_1",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(pause=True),
    )
    attempts = AttemptStore(executions)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="worker_1",
        ttl_seconds=30,
        attempt_id="attempt_1",
    )
    _, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    return executions, EffectStore(executions), running


def test_nonrepeatable_effect_requires_resolution_after_uncertain_dispatch(
    tmp_path,
) -> None:
    _, effects, execution = _stores(tmp_path)
    planned = effects.register(
        effect_id="effect_1",
        execution_id=execution.execution_id,
        attempt_id="attempt_1",
        action_id="send_message",
        classification=EffectClassification.NONREPEATABLE,
        idempotency_key=None,
        metadata={"destination_hash": "recipient_1"},
    )
    dispatched = effects.mark_dispatched(
        planned.effect_id,
        expected_status=EffectStatus.PLANNED,
    )
    uncertain = effects.mark_uncertain(
        dispatched.effect_id,
        expected_status=EffectStatus.DISPATCHED,
    )

    assert effects.list_unresolved(execution.execution_id) == [uncertain]
    committed = effects.resolve(
        uncertain.effect_id,
        expected_status=EffectStatus.UNCERTAIN,
        outcome=EffectStatus.COMMITTED,
        receipt={"provider_message_id": "message_123"},
    )
    assert committed.status is EffectStatus.COMMITTED
    assert effects.list_unresolved(execution.execution_id) == []


def test_effect_registration_is_idempotent_but_id_collision_is_rejected(
    tmp_path,
) -> None:
    _, effects, execution = _stores(tmp_path)
    values = {
        "effect_id": "effect_1",
        "execution_id": execution.execution_id,
        "attempt_id": "attempt_1",
        "action_id": "api_upsert",
        "classification": EffectClassification.IDEMPOTENT,
        "idempotency_key": "exec_1:api_upsert:1",
        "metadata": {"resource": "record_1"},
    }
    first = effects.register(**values)
    assert effects.register(**values) == first

    with pytest.raises(EffectConflict) as collision:
        effects.register(**{**values, "action_id": "different_action"})
    assert collision.value.code == "idempotency_collision"


def test_committed_effect_cannot_be_reopened(tmp_path) -> None:
    _, effects, execution = _stores(tmp_path)
    planned = effects.register(
        effect_id="effect_1",
        execution_id=execution.execution_id,
        attempt_id="attempt_1",
        action_id="write_blob",
        classification=EffectClassification.IDEMPOTENT,
        idempotency_key="blob_1",
        metadata={},
    )
    dispatched = effects.mark_dispatched(
        planned.effect_id,
        expected_status=EffectStatus.PLANNED,
    )
    committed = effects.resolve(
        dispatched.effect_id,
        expected_status=EffectStatus.DISPATCHED,
        outcome=EffectStatus.COMMITTED,
        receipt={"blob": "sha256:abc"},
    )

    with pytest.raises(EffectConflict) as terminal:
        effects.mark_uncertain(
            committed.effect_id,
            expected_status=EffectStatus.COMMITTED,
        )
    assert terminal.value.code == "terminal"


def test_expired_attempt_cannot_register_an_effect(tmp_path) -> None:
    executions, _, execution = _stores(tmp_path)
    effects = EffectStore(executions, clock=lambda: time.time() + 60)

    with pytest.raises(EffectConflict) as stale:
        effects.register(
            effect_id="effect_1",
            execution_id=execution.execution_id,
            attempt_id="attempt_1",
            action_id="send_message",
            classification=EffectClassification.NONREPEATABLE,
            idempotency_key=None,
            metadata={},
        )
    assert stale.value.code == "stale_attempt"

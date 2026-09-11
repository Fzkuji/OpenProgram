from __future__ import annotations

import json

from openprogram.execution.attempts import AttemptStore
from openprogram.execution.control import RuntimeControlService
from openprogram.execution.driver import DriverRegistry
from openprogram.execution.driver import DriverBinding
from openprogram.execution.effects import EffectClassification, EffectStatus, EffectStore
from openprogram.execution.model import CapabilitySet, ExecutionStatus
from openprogram.execution.store import ExecutionStore


def _running(tmp_path, *, kind: str = "chat", pause: bool = True):
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    revision = store.create_revision(manifest={"entrypoint": "chat"})
    payload = (
        {"version": 1, "kind": kind, "request": {"user_text": "continue", "agent_id": "main", "source": "test"}}
        if kind == "chat"
        else {"version": 1, "kind": "forced_tool", "tool_name": "gui_agent", "tool_input": {}}
    )
    import hashlib
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    execution = store.admit_execution(
        session_id="session",
        revision_id=revision.revision_id,
        input_ref="agent-turn:" + hashlib.sha256(encoded.encode()).hexdigest(),
        input_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        entrypoint="openprogram.agent.production_driver:AgentProductionDriver",
        trusted_actor={"subject": "test"},
        config_snapshot_ref="config:test",
        user_message_id="user",
        assistant_message_id="assistant",
        capabilities=CapabilitySet(pause=pause),
        agent_turn_payload=payload,
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id, expected_version=execution.status_version,
        owner_id="worker", ttl_seconds=30, attempt_id="attempt",
    )
    active, running = attempts.activate(
        leased.attempt_id, generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    return store, attempts, running, active


def test_owner_loss_marks_ordinary_chat_restart_pending_and_resolves_provider_only_effect(tmp_path, monkeypatch):
    store, attempts, running, active = _running(tmp_path)
    effects = EffectStore(store)
    effects.register(
        effect_id="provider-effect", execution_id=running.execution_id,
        attempt_id=active.attempt_id, action_id="provider-action",
        classification=EffectClassification.NONREPEATABLE, idempotency_key=None,
        metadata={"kind": "provider.before"},
    )
    effects.mark_dispatched("provider-effect", expected_status=EffectStatus.PLANNED)
    monkeypatch.setattr("openprogram.execution.process_owner.process_owner_may_be_alive", lambda *args, **kwargs: False)
    service = RuntimeControlService(store, attempts, DriverRegistry())

    recovered = service.recover_owner_loss(running.execution_id, only_if_abandoned=True)

    assert recovered.execution.status is ExecutionStatus.PAUSED
    assert recovered.execution.reason_code == "restart_pending"
    assert recovered.attempt is not None
    assert effects.get("provider-effect").status is EffectStatus.NOT_COMMITTED


def test_forced_tool_owner_loss_is_not_automatically_resumable(tmp_path, monkeypatch):
    store, attempts, running, _ = _running(tmp_path, kind="forced_tool")
    monkeypatch.setattr("openprogram.execution.process_owner.process_owner_may_be_alive", lambda *args, **kwargs: False)
    service = RuntimeControlService(store, attempts, DriverRegistry())

    recovered = service.recover_owner_loss(running.execution_id, only_if_abandoned=True)

    assert recovered.execution.status is ExecutionStatus.INTERRUPTED
    assert recovered.execution.reason_code == "owner_lost"


class _StartupDriver:
    activations = []

    def __init__(self, *args, **kwargs):
        pass

    async def activate(self, attempt, activation):
        self.activations.append((attempt.execution_id, activation.checkpoint))
        return DriverBinding(
            execution_id=attempt.execution_id,
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            driver=self,
            handle=attempt.attempt_id,
        )


class _NoopProjection:
    def recover_startup(self, *, owner_id):
        return type("Result", (), {"claimed": 0, "delivered": 0, "failed": 0})()


def test_startup_reactivates_same_admission_after_two_owner_losses(tmp_path, monkeypatch):
    from openprogram.execution.startup import recover_execution_startup

    store, attempts, running, _ = _running(tmp_path)
    _StartupDriver.activations = []
    service = RuntimeControlService(store, attempts, DriverRegistry())
    monkeypatch.setattr("openprogram.execution.process_owner.process_owner_may_be_alive", lambda *args, **kwargs: False)
    monkeypatch.setattr("openprogram.agent.production_driver.AgentProductionDriver", _StartupDriver)

    first = service.recover_owner_loss(running.execution_id, only_if_abandoned=True)
    assert first.execution.reason_code == "restart_pending"
    recover_execution_startup(control_service=service, projection_dispatcher=_NoopProjection())
    resumed = store.get_execution(running.execution_id)
    assert resumed.status is ExecutionStatus.RUNNING
    first_attempt = resumed.current_attempt_id

    second = service.recover_owner_loss(running.execution_id, only_if_abandoned=True)
    assert second.execution.reason_code == "restart_pending"
    recover_execution_startup(control_service=service, projection_dispatcher=_NoopProjection())
    resumed_again = store.get_execution(running.execution_id)
    assert resumed_again.status is ExecutionStatus.RUNNING
    assert resumed_again.current_attempt_id != first_attempt
    assert len(_StartupDriver.activations) == 2


def test_startup_does_not_resume_manual_paused_execution(tmp_path, monkeypatch):
    from openprogram.execution.startup import recover_execution_startup

    store, attempts, running, _ = _running(tmp_path)
    service = RuntimeControlService(store, attempts, DriverRegistry())
    monkeypatch.setattr("openprogram.execution.process_owner.process_owner_may_be_alive", lambda *args, **kwargs: False)
    # A user pause is represented by a paused ownerless record with a
    # distinct reason and is intentionally outside the startup policy.
    with store._transaction() as connection:
        paused = store._transition_execution(
            connection, running.execution_id,
            expected_version=running.status_version,
            target=ExecutionStatus.PAUSED,
            reason_code="user_pause",
            clear_owner=True,
        )
    recover_execution_startup(control_service=service, projection_dispatcher=_NoopProjection())
    assert store.get_execution(running.execution_id) == paused


def test_startup_internal_resume_does_not_require_pause_capability(tmp_path, monkeypatch):
    from openprogram.execution.startup import recover_execution_startup

    store, attempts, running, _ = _running(tmp_path, pause=False)
    service = RuntimeControlService(store, attempts, DriverRegistry())
    monkeypatch.setattr("openprogram.execution.process_owner.process_owner_may_be_alive", lambda *args, **kwargs: False)
    monkeypatch.setattr("openprogram.agent.production_driver.AgentProductionDriver", _StartupDriver)
    _StartupDriver.activations = []
    service.recover_owner_loss(running.execution_id, only_if_abandoned=True)

    recover_execution_startup(control_service=service, projection_dispatcher=_NoopProjection())

    assert store.get_execution(running.execution_id).status is ExecutionStatus.RUNNING
    assert len(_StartupDriver.activations) == 1


def test_owner_loss_does_not_replay_tool_without_continuation_record(tmp_path):
    store, attempts, running, active = _running(tmp_path)
    effects = EffectStore(store)
    effects.register(
        effect_id="write", execution_id=running.execution_id,
        attempt_id=active.attempt_id, action_id="write",
        classification=EffectClassification.NONREPEATABLE, idempotency_key=None,
        metadata={"kind": "tool.before"},
    )
    effects.mark_dispatched("write", expected_status=EffectStatus.PLANNED)
    service = RuntimeControlService(store, attempts, DriverRegistry())
    unresolved = service.recover_owner_loss(running.execution_id)
    assert unresolved.execution.status is ExecutionStatus.RECONCILIATION_REQUIRED
    assert effects.get("write").status is EffectStatus.DISPATCHED


def test_legacy_completed_tool_without_checkpoint_is_not_replayed(tmp_path):
    store, attempts, running, active = _running(tmp_path)
    effects = EffectStore(store)
    effects.register(
        effect_id="write", execution_id=running.execution_id,
        attempt_id=active.attempt_id, action_id="write",
        classification=EffectClassification.NONREPEATABLE, idempotency_key=None,
        metadata={"kind": "tool.before"},
    )
    effects.mark_dispatched("write", expected_status=EffectStatus.PLANNED)
    effects.resolve(
        "write", expected_status=EffectStatus.DISPATCHED,
        outcome=EffectStatus.COMMITTED, receipt={"result": "saved"},
        attempt_id=active.attempt_id, generation=active.generation,
    )
    service = RuntimeControlService(store, attempts, DriverRegistry())
    recovered = service.recover_owner_loss(running.execution_id)
    assert recovered.execution.status is ExecutionStatus.INTERRUPTED


def test_legacy_checkpoint_before_completed_tool_does_not_replay_tool(tmp_path):
    from tests.component.agent.test_agent_durable_safe_point import _real_provider_safe_point
    store, service, active, running, checkpoint, _ = _real_provider_safe_point(tmp_path, pause=False)
    effects = EffectStore(store)
    effects.register(
        effect_id="legacy-write", execution_id=running.execution_id,
        attempt_id=active.attempt_id, action_id="legacy-write",
        classification=EffectClassification.NONREPEATABLE, idempotency_key=None,
        metadata={"kind": "tool.before"},
    )
    effects.mark_dispatched("legacy-write", expected_status=EffectStatus.PLANNED)
    effects.resolve(
        "legacy-write", expected_status=EffectStatus.DISPATCHED,
        outcome=EffectStatus.COMMITTED, receipt={"result": "saved"},
        attempt_id=active.attempt_id, generation=active.generation,
    )
    recovered = service.recover_owner_loss(running.execution_id)
    assert recovered.execution.checkpoint_head_id == checkpoint.checkpoint_id
    assert recovered.execution.status is ExecutionStatus.INTERRUPTED


def test_completed_tool_cursor_resumes_when_next_provider_is_interrupted(tmp_path):
    import threading
    from tests.component.agent.test_agent_durable_safe_point import _real_provider_safe_point
    from openprogram.agent.continuation import AgentContinuation
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.agent.production_driver import AgentProductionDriver

    store, service, active, running, checkpoint, _ = _real_provider_safe_point(tmp_path, pause=False)
    request = TurnRequest(session_id=running.session_id, user_text="continue", agent_id="main", source="component", user_msg_id="user-anchor")
    continuation = AgentContinuation.from_checkpoint(store=store, checkpoint=checkpoint, request=request)
    hook = AgentProductionDriver(store, control_service=service)._safe_point_hook(active, request, threading.Event(), continuation=continuation)
    assert hook("tool.before", {"tool_call_id": "tool-1", "tool_name": "echo", "arguments": {}}) is False
    assert hook("tool.after", {
        "tool_call_id": "tool-1", "tool_name": "echo", "next_tool_index": 1,
        "tool_call_ids": ["tool-1"], "is_error": False,
        "result": {"role": "toolResult", "tool_call_id": "tool-1", "tool_name": "echo", "content": [{"type": "text", "text": "saved"}], "timestamp": 2},
    }) is False
    current = store.get_execution(running.execution_id)
    assert current.checkpoint_head_id != checkpoint.checkpoint_id
    assert hook("provider.before", {"resolved_snapshot": continuation.resolved_snapshot, "context": {"messages": []}}) is False
    recovered = service.recover_owner_loss(running.execution_id)
    assert recovered.execution.reason_code == "restart_pending"
    assert recovered.execution.checkpoint_head_id == current.checkpoint_head_id
    assert service.effects.list_unresolved(running.execution_id) == []

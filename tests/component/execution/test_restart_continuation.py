from __future__ import annotations

import json
import threading

import pytest

from openprogram.runtime.execution.attempts import AttemptConflict, AttemptStore
from openprogram.runtime.execution.control import RuntimeControlService
from openprogram.runtime.execution.driver import DriverRegistry
from openprogram.runtime.execution.driver import DriverBinding
from openprogram.runtime.execution.effects import EffectClassification, EffectStatus, EffectStore
from openprogram.runtime.execution.model import CapabilitySet, ExecutionStatus
from openprogram.runtime.execution.store import ExecutionStore


def _running(tmp_path, *, kind: str = "chat", pause: bool = True, activate: bool = True):
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
        entrypoint="openprogram.runtime.conversation.production_driver:AgentProductionDriver",
        trusted_actor={"subject": "test"},
        config_snapshot_ref="config:test",
        user_message_id="user",
        assistant_message_id="assistant",
        capabilities=CapabilitySet(pause=pause),
        agent_turn_payload=payload,
    )
    attempts = AttemptStore(store)
    if not activate:
        return store, attempts, execution, None
    leased, reserved = attempts.lease(
        execution.execution_id, expected_version=execution.status_version,
        owner_id="worker", attempt_id="attempt",
    )
    active, running = attempts.activate(
        leased.attempt_id, generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    return store, attempts, running, active


def test_legacy_migration_waits_for_running_execution_with_string_state_hook(
    tmp_path, monkeypatch,
):
    """The execution DB remains authoritative when a test/embedder returns a string path."""
    from openprogram.state.store.session import migration

    store, _attempts, running, _active = _running(tmp_path)
    state = tmp_path / "state"
    (state / "projects").mkdir(parents=True)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: str(state))
    monkeypatch.setattr("openprogram.runtime.execution.store.default_store", lambda: store)

    assert migration._live_jobs("session") is True
    assert migration.quiesce_session(state / "projects", "session", timeout=0.05) is False
    assert migration.hold_path(state / "projects", "session").is_file()


def test_execution_admission_is_rejected_while_migration_hold_is_active(
    tmp_path, monkeypatch,
):
    from openprogram.runtime.execution.store import ExecutionConflict
    from openprogram.state.store.session import migration

    store = ExecutionStore(tmp_path / "execution.sqlite3")
    revision = store.create_revision(manifest={"entrypoint": "chat"})
    state = tmp_path / "state"
    (state / "projects").mkdir(parents=True)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    migration._set_hold(state / "projects", "session")

    try:
        with pytest.raises(ExecutionConflict, match="session migration"):
            store.admit_execution(
                session_id="session", revision_id=revision.revision_id,
                input_ref="agent-turn:test", input_hash="test",
                entrypoint="openprogram.runtime.conversation.production_driver:AgentProductionDriver",
                trusted_actor={"subject": "test"}, config_snapshot_ref="config:test",
            )
    finally:
        migration._clear_hold(state / "projects", "session")


def test_attempt_lease_and_activation_honor_migration_hold(tmp_path, monkeypatch):
    from openprogram.state.store.session import migration

    store = ExecutionStore(tmp_path / "execution.sqlite3")
    revision = store.create_revision(manifest={"entrypoint": "chat"})
    execution = store.admit_execution(
        session_id="session", revision_id=revision.revision_id,
        input_ref="agent-turn:test", input_hash="test",
        entrypoint="openprogram.runtime.conversation.production_driver:AgentProductionDriver",
        trusted_actor={"subject": "test"}, config_snapshot_ref="config:test",
    )
    attempts = AttemptStore(store)
    state = tmp_path / "state"
    (state / "projects").mkdir(parents=True)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    leased, reserved = attempts.lease(
        execution.execution_id, expected_version=execution.status_version,
        owner_id="worker",
    )
    migration._set_hold(state / "projects", "session")

    with pytest.raises(AttemptConflict, match="session migration"):
        attempts.activate(
            leased.attempt_id, generation=leased.generation,
            expected_execution_version=reserved.status_version,
        )

    migration._clear_hold(state / "projects", "session")
    active, running = attempts.activate(
        leased.attempt_id, generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    assert active.status.value == "active"
    assert running.status is ExecutionStatus.RUNNING


def test_owner_loss_before_any_model_answer_marks_chat_restart_pending(tmp_path, monkeypatch):
    store, attempts, running, active = _running(tmp_path)
    monkeypatch.setattr("openprogram.runtime.execution.process_owner.process_owner_may_be_alive", lambda *args, **kwargs: False)
    service = RuntimeControlService(store, attempts, DriverRegistry())

    recovered = service.recover_owner_loss(running.execution_id, only_if_abandoned=True)

    assert recovered.execution.status is ExecutionStatus.PAUSED
    assert recovered.execution.reason_code == "restart_pending"
    assert recovered.attempt is not None


def test_forced_tool_owner_loss_is_not_automatically_resumable(tmp_path, monkeypatch):
    store, attempts, running, _ = _running(tmp_path, kind="forced_tool")
    monkeypatch.setattr("openprogram.runtime.execution.process_owner.process_owner_may_be_alive", lambda *args, **kwargs: False)
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


def _restart_after_startup(service, monkeypatch):
    """Use the same bounded reconciler that the initialized JobRunner invokes."""
    from types import SimpleNamespace
    from openprogram.runtime.execution.restart import reconcile

    service.activator = _StartupDriver().activate
    monkeypatch.setattr("openprogram.runtime.execution.default_control_service", lambda: service)
    reconcile(SimpleNamespace(_execution_store=service.executions, _execution_control=service))


def test_startup_reactivates_same_admission_after_two_owner_losses(tmp_path, monkeypatch):
    from openprogram.runtime.execution.startup import recover_execution_startup

    store, attempts, running, _ = _running(tmp_path)
    _StartupDriver.activations = []
    service = RuntimeControlService(store, attempts, DriverRegistry())
    monkeypatch.setattr("openprogram.runtime.execution.process_owner.process_owner_may_be_alive", lambda *args, **kwargs: False)
    monkeypatch.setattr("openprogram.runtime.conversation.production_driver.AgentProductionDriver", _StartupDriver)

    first = service.recover_owner_loss(running.execution_id, only_if_abandoned=True)
    assert first.execution.reason_code == "restart_pending"
    recover_execution_startup(control_service=service)
    assert store.get_execution(running.execution_id).status is ExecutionStatus.PAUSED
    _restart_after_startup(service, monkeypatch)
    resumed = store.get_execution(running.execution_id)
    assert resumed.status is ExecutionStatus.RUNNING
    first_attempt = resumed.current_attempt_id

    second = service.recover_owner_loss(running.execution_id, only_if_abandoned=True)
    assert second.execution.reason_code == "restart_pending"
    recover_execution_startup(control_service=service)
    _restart_after_startup(service, monkeypatch)
    resumed_again = store.get_execution(running.execution_id)
    assert resumed_again.status is ExecutionStatus.RUNNING
    assert resumed_again.current_attempt_id != first_attempt
    assert len(_StartupDriver.activations) == 2


def test_startup_does_not_resume_manual_paused_execution(tmp_path, monkeypatch):
    from openprogram.runtime.execution.startup import recover_execution_startup

    store, attempts, running, _ = _running(tmp_path)
    service = RuntimeControlService(store, attempts, DriverRegistry())
    monkeypatch.setattr("openprogram.runtime.execution.process_owner.process_owner_may_be_alive", lambda *args, **kwargs: False)
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
    recover_execution_startup(control_service=service)
    assert store.get_execution(running.execution_id) == paused


def test_startup_internal_resume_does_not_require_pause_capability(tmp_path, monkeypatch):
    from openprogram.runtime.execution.startup import recover_execution_startup

    store, attempts, running, _ = _running(tmp_path, pause=False)
    service = RuntimeControlService(store, attempts, DriverRegistry())
    monkeypatch.setattr("openprogram.runtime.execution.process_owner.process_owner_may_be_alive", lambda *args, **kwargs: False)
    monkeypatch.setattr("openprogram.runtime.conversation.production_driver.AgentProductionDriver", _StartupDriver)
    _StartupDriver.activations = []
    service.recover_owner_loss(running.execution_id, only_if_abandoned=True)

    recover_execution_startup(control_service=service)

    assert store.get_execution(running.execution_id).status is ExecutionStatus.PAUSED
    _restart_after_startup(service, monkeypatch)
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


def test_owner_loss_tells_the_model_a_tool_was_interrupted(tmp_path, monkeypatch):
    from tests.component.agent.execution.test_agent_durable_safe_point import _real_provider_safe_point

    store, service, active, running, checkpoint, _ = _real_provider_safe_point(tmp_path, pause=False)
    hook = None
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.runtime.conversation.checkpoint import AgentContinuation

    request = TurnRequest(
        session_id=running.session_id, user_text="continue",
        agent_id="main", source="component", user_msg_id="user-anchor",
    )
    continuation = AgentContinuation.from_checkpoint(
        store=store, checkpoint=checkpoint, request=request,
    )
    hook = AgentProductionDriver(store, control_service=service)._safe_point_hook(
        active, request, threading.Event(), continuation=continuation,
    )
    assert hook("tool.before", {
        "tool_call_id": "tool-1", "tool_name": "echo", "arguments": {},
    }) is False
    assert hook("tool.started", {"tool_call_id": "tool-1"}) is False
    monkeypatch.setattr(
        "openprogram.runtime.execution.process_owner.process_owner_may_be_alive",
        lambda *args, **kwargs: False,
    )
    recovered = service.recover_owner_loss(running.execution_id, only_if_abandoned=True)
    assert recovered.execution.status is ExecutionStatus.PAUSED
    assert recovered.execution.reason_code == "restart_recoverable"
    resumed = AgentContinuation.from_checkpoint(
        store=store, checkpoint=service.checkpoints.get(recovered.execution.checkpoint_head_id),
        request=request,
    )
    assert resumed.interrupted_result is not None
    assert resumed.tool_results[0].is_error
    assert "unknown whether it finished" in resumed.tool_results[0].content[0].text


def test_startup_resumes_without_rerunning_an_interrupted_tool(tmp_path, monkeypatch):
    from openprogram.runtime.execution.startup import recover_execution_startup
    from tests.component.agent.execution.test_agent_durable_safe_point import _real_provider_safe_point

    store, service, active, running, checkpoint, _ = _real_provider_safe_point(tmp_path, pause=False)
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.runtime.conversation.checkpoint import AgentContinuation

    request = TurnRequest(
        session_id=running.session_id, user_text="continue",
        agent_id="main", source="component", user_msg_id="user-anchor",
    )
    continuation = AgentContinuation.from_checkpoint(
        store=store, checkpoint=checkpoint, request=request,
    )
    hook = AgentProductionDriver(store, control_service=service)._safe_point_hook(
        active, request, threading.Event(), continuation=continuation,
    )
    assert hook("tool.before", {
        "tool_call_id": "tool-1", "tool_name": "echo", "arguments": {},
    }) is False
    assert hook("tool.started", {"tool_call_id": "tool-1"}) is False
    monkeypatch.setattr(
        "openprogram.runtime.execution.process_owner.process_owner_may_be_alive",
        lambda *args, **kwargs: False,
    )
    _StartupDriver.activations = []
    monkeypatch.setattr("openprogram.runtime.conversation.production_driver.AgentProductionDriver", _StartupDriver)
    recover_execution_startup(control_service=service)
    _restart_after_startup(service, monkeypatch)
    resumed = store.get_execution(running.execution_id)
    assert resumed.status is ExecutionStatus.RUNNING
    assert len(_StartupDriver.activations) == 1
    head = service.checkpoints.get(resumed.checkpoint_head_id)
    assert AgentContinuation.from_checkpoint(store=store, checkpoint=head, request=request).interrupted_result is not None


def test_completed_tool_cursor_resumes_when_next_provider_is_interrupted(tmp_path):
    import threading
    from tests.component.agent.execution.test_agent_durable_safe_point import _real_provider_safe_point
    from openprogram.runtime.conversation.checkpoint import AgentContinuation
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver

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
    assert hook("provider.before", {"resolved_snapshot": continuation.resolved_snapshot, "context": {"messages": []}}) is False
    recovered = service.recover_owner_loss(running.execution_id)
    assert recovered.execution.reason_code == "restart_recoverable"
    head = service.checkpoints.get(recovered.execution.checkpoint_head_id)
    assert head.state_refs["agent_position"]["next_tool_index"] == 1


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("window", [-1, 7200])
@pytest.mark.parametrize("elapsed", [7200, 7201, 86400])
def test_initial_admission_recovery_preserves_restart_policy(tmp_path, monkeypatch, queued, elapsed, window):
    from openprogram.runtime.execution import restart
    from openprogram.runtime.execution.startup import recover_execution_startup

    monkeypatch.setattr(restart, "window_seconds", lambda: window)
    resumes = window == -1 or elapsed <= window

    store, attempts, running, active = _running(tmp_path, activate=not queued)
    # A fresh admission has not run a tool and needs no continuation cursor.
    interrupted_at = store.get_execution(running.execution_id).updated_at
    if not queued:
        interrupted_at = max(interrupted_at, active.updated_at)
    monkeypatch.setattr("openprogram.runtime.execution.process_owner.process_owner_may_be_alive", lambda *args, **kwargs: False)
    monkeypatch.setattr(restart, "time", lambda: interrupted_at + elapsed)
    service = RuntimeControlService(store, attempts, DriverRegistry())
    _StartupDriver.activations = []
    recover_execution_startup(control_service=service)
    intent = next(e for e in store.list_events(running.execution_id) if e.kind == restart._REQUEST)
    assert intent.payload["resume_before"] == (None if window == -1 else interrupted_at + window)
    _restart_after_startup(service, monkeypatch)
    expected = ExecutionStatus.RUNNING if resumes else ExecutionStatus.PAUSED
    assert store.get_execution(running.execution_id).status is expected
    assert len(_StartupDriver.activations) == int(resumes)
    if not resumes:
        recover_execution_startup(control_service=service)
        _restart_after_startup(service, monkeypatch)
        assert len(_StartupDriver.activations) == 0
        assert len([e for e in store.list_events(running.execution_id) if e.kind == restart._REQUEST]) == 1


def test_recovery_of_a_pausing_run_whose_attempt_ended_with_no_recorded_ending_is_interrupted(tmp_path, monkeypatch):
    from types import SimpleNamespace

    store, attempts, running, active = _running(tmp_path)
    with store._transaction() as connection:
        connection.update(
            "attempts", {"status": "ended", "outcome": "completed"},
            attempt_id=active.attempt_id,
        )
        store._transition_execution(
            connection, running.execution_id, expected_version=running.status_version,
            target=ExecutionStatus.PAUSING, reason_code="worker_restart",
        )
    monkeypatch.setattr(
        "openprogram.runtime.execution.process_owner.process_owner_may_be_alive",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        "openprogram.runtime.execution.control.recovery.journal_head",
        lambda *args, **kwargs: (SimpleNamespace(has_decision=True), ()),
    )

    RuntimeControlService(store, attempts, DriverRegistry()).recover_startup()

    recovered = store.get_execution(running.execution_id)
    assert recovered.status is ExecutionStatus.INTERRUPTED
    assert attempts.get(active.attempt_id).outcome == "completed"


def test_worker_shutdown_leaves_a_run_owned_by_another_process_alone(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from openprogram.runtime.execution import restart

    store, _attempts, running, _active = _running(tmp_path)
    with store._transaction() as connection:
        lease = {**running.owner_lease, "process_owner": {"instance": "another-process"}}
        connection.update("executions", {"owner_lease_json": json.dumps(lease)}, execution_id=running.execution_id)
    before = store.get_execution(running.execution_id)
    monkeypatch.setattr(restart, "window_seconds", lambda: -1)

    restart.prepare_shutdown(SimpleNamespace(_execution_store=store))

    assert store.get_execution(running.execution_id) == before
    assert not any(e.kind == restart._REQUEST for e in store.list_events(running.execution_id))

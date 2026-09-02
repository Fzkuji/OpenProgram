"""Public RED contracts for the durable background Agent Job control plane.

The tests enter through ``JobRunner.spawn_job`` and the transport adapters.  A
test may inspect the resulting durable stores, but it must not make a private
Control Service call to manufacture a green result.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from tests.component.agent.async_job_support import store_fixture  # noqa: F401


class _WS:
    def __init__(self, session_id: str) -> None:
        from openprogram.agent.authority import owner_authority

        self.frames: list[dict] = []
        self.scope = {
            "state": {
                "session_id": session_id,
                "authority": owner_authority("owner/install/0123456789abcdef"),
            },
        }

    async def send_text(self, payload: str) -> None:
        self.frames.append(json.loads(payload))


@pytest.fixture
def durable_job(tmp_path, store_fixture, monkeypatch):
    """Create a real JobRunner with isolated execution and resource stores."""

    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.execution import default_store as execution_store
    from openprogram.usage.ledger import UsageLedger

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.paths.get_execution_db_path", lambda: execution_db,
    )
    ledger = UsageLedger(tmp_path / "usage.sqlite3")
    class _SafePointResult:
        _execution_safe_point_handoff = True
        failed = False
        error = None

    def one_provider_action(*, request, execution_context, **_kwargs):
        hook = execution_context["safe_point_hook"]
        snapshot = {
            "contract_version": 1,
            "model": {
                "id": "test-model", "api": "test", "provider": "test",
                "base_url": "https://test.invalid", "endpoint": "responses",
            },
            "system_prompt": "",
            "tools": [],
            "structured_output": {},
            "toolset": {},
            "request_semantics": {
                "_execution_revision_id": request._execution_revision_id,
                "execution_location": {},
            },
        }
        hook("provider.before", {
            "resolved_snapshot": snapshot,
            "context": {"fixture": "durable-job"},
            "supports_idempotency_key": True,
        })
        assert hook("provider.after", {
            "resolved_snapshot": snapshot,
            "message": {
                "api": "test", "provider": "test", "model": "test",
                "content": [],
            },
            "usage": {},
        })
        return _SafePointResult()

    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(ledger),
        agent_driver_factory=lambda store, control: AgentProductionDriver(
            store, control_service=control, turn_runner=one_provider_action,
        ),
    )
    try:
        yield runner, ledger, execution_store, execution_db
    finally:
        runner.shutdown()


def _spawn(runner, **kwargs) -> str:
    return runner.spawn_job(
        session_id="p1",
        prompt=kwargs.pop("prompt", "durable background turn"),
        agent_id="main",
        parent_msg_id=kwargs.pop("parent_msg_id", "a1"),
        defer_dispatch=True,
        **kwargs,
    )


def _execution(runner, execution_store, **kwargs):
    job_id = _spawn(runner, **kwargs)
    record = execution_store().get_execution(job_id)
    assert record is not None
    return job_id, record


def _run_runtime_action(action: str, ws: _WS, envelope: dict) -> None:
    from openprogram.webui.ws_actions import runtime

    asyncio.run(runtime.ACTIONS[action](ws, envelope))


def _command_frame(ws: _WS) -> dict:
    return next(
        frame for frame in reversed(ws.frames)
        if frame["type"] == "execution.command.updated"
    )


def test_background_spawn_has_one_canonical_execution_and_no_inner_exec_identity(
    durable_job,
):
    runner, _ledger, execution_store, _db = durable_job
    job_id, execution = _execution(runner, execution_store)

    assert job_id == execution.execution_id
    input_record = execution_store().get_execution_input(job_id)
    assert input_record is not None
    assert input_record.execution_id == job_id
    payload = json.loads(input_record.input_ref.removeprefix("job-input-v1:"))
    assert payload["version"] == 1
    assert payload["kind"] == "job_agent"
    identities = {
        value
        for value in json.dumps(payload).split('"')
        if re.fullmatch(r"(?:exec|j)_[A-Za-z0-9]+", value)
    }
    assert identities <= {job_id}


def test_job_negotiates_the_two_agent_safe_points(durable_job):
    runner, _ledger, execution_store, _db = durable_job
    job_id, execution = _execution(runner, execution_store)

    assert execution.capabilities.to_dict() == {
        "pause": True,
        "step": True,
        "steer": False,
        "fork": False,
        "retry": False,
        "safe_point_kinds": [
            "agent.provider.decision.after",
            "agent.tool.action.after",
        ],
        "state_schema_version": 1,
    }
    assert runner.get_job(job_id).id == execution.execution_id


def test_strict_job_agent_input_keeps_turn_request_and_job_context_separate(
    durable_job,
):
    runner, _ledger, execution_store, _db = durable_job
    parent_id, _parent = _execution(runner, execution_store, caller_msg_id="parent-a1")
    job_id, _execution_record = _execution(
        runner,
        execution_store,
        caller_msg_id="caller-a1",
        caller_session_id="p1",
        parent_job_id=parent_id,
        worktree_id="wt-1",
    )
    input_record = execution_store().get_execution_input(job_id)
    assert input_record is not None
    payload = json.loads(input_record.input_ref.removeprefix("job-input-v1:"))

    assert set(payload) == {"version", "kind", "turn_request", "job_context"}
    assert payload["kind"] == "job_agent"
    assert payload["turn_request"]["user_text"] == "durable background turn"
    assert payload["turn_request"]["spawn_caller"] == "caller-a1"
    assert payload["job_context"]["worktree_id"] == "wt-1"
    assert payload["job_context"]["caller"]["msg_id"] == "caller-a1"
    assert "worktree_id" not in payload["turn_request"]


def test_resource_dto_contains_canonical_snapshot_and_reconnect_cursor(durable_job):
    runner, _ledger, execution_store, _db = durable_job
    job_id, execution = _execution(runner, execution_store)
    view = runner.get_job_resource_view(job_id)
    assert view is not None
    dto = view.to_dict()

    assert dto["job_id"] == execution.execution_id
    assert dto["execution_id"] == execution.execution_id
    assert dto["status"] == execution.status.value
    assert dto["capabilities"] == execution.capabilities.to_dict()
    assert dto["checkpoint_head_id"] is None
    assert dto["resource"]["admission_id"]
    assert dto["event_cursor"] == {
        "execution_id": execution.execution_id,
        "next_sequence": dto["execution"]["event_sequence"] + 1,
        "snapshot_status_version": execution.status_version,
    }


def test_job_projection_rebuilds_from_execution_sequence_and_not_job_row(durable_job):
    runner, _ledger, execution_store, _db = durable_job
    job_id, execution = _execution(runner, execution_store)
    from openprogram.execution.outbox import ProjectionDispatcher
    from openprogram.execution.projections import (
        ExecutionProjectionReadModel,
        projection_handlers,
    )

    dispatcher = ProjectionDispatcher(
        execution_store(), projection_handlers(execution_store()),
    )
    result = dispatcher.drain(owner_id="job-projection-test", max_batches=1)
    assert result.delivered > 0
    projection = ExecutionProjectionReadModel(execution_store()).get_current(
        "job", job_id,
    )
    assert projection is not None
    assert projection.event_sequence == execution.status_version
    assert projection.payload["job"]["execution_id"] == job_id
    assert projection.payload["execution"]["capabilities"]["pause"] is True
    assert projection.payload["event_cursor"]["next_sequence"] == execution.status_version + 1


def test_ws_pause_uses_complete_execution_command_update_envelope(durable_job, monkeypatch):
    runner, _ledger, execution_store, _db = durable_job
    job_id, execution = _execution(runner, execution_store)
    ws = _WS("p1")
    monkeypatch.setattr(
        "openprogram.webui.ws_actions.runtime._broadcast_execution",
        lambda *_args, **_kwargs: None,
    )
    command = {
        "type": "execution.command",
        "action": "execution.pause",
        "command_id": "cmd-job-pause",
        "execution_id": job_id,
        "expected_version": execution.status_version,
        "payload": {"reason_code": "pause.user"},
    }

    _run_runtime_action("execution.pause", ws, command)
    frame = _command_frame(ws)
    assert frame["command"]["execution_id"] == job_id
    assert frame["execution"]["execution_id"] == job_id
    assert frame["execution"]["resource"] is not None
    assert frame["execution"]["capabilities"]["safe_point_kinds"]
    assert frame["event_cursor"]["next_sequence"] > 0


def test_queued_pause_releases_reservation_and_continue_uses_initial_handoff(
    durable_job,
):
    runner, ledger, execution_store, _db = durable_job
    job_id, execution = _execution(runner, execution_store)
    ws = _WS("p1")
    _run_runtime_action("execution.pause", ws, {
        "type": "execution.command", "action": "execution.pause",
        "command_id": "cmd-queued-pause", "execution_id": job_id,
        "expected_version": execution.status_version, "payload": {},
    })
    paused = execution_store().get_execution(job_id)
    assert paused is not None and paused.status.value == "paused"
    assert paused.current_attempt_id is None
    assert paused.checkpoint_head_id is None
    resource = runner.get_job_resource_view(job_id).to_dict()
    assert resource["resource"]["resource_state"] == "released"

    _run_runtime_action("execution.continue", ws, {
        "type": "execution.command", "action": "execution.continue",
        "command_id": "cmd-queued-continue", "execution_id": job_id,
        "expected_version": paused.status_version, "payload": {},
    })
    continue_frame = _command_frame(ws)
    assert continue_frame["command"]["status"] in {"accepted", "applying", "applied"}
    assert continue_frame["execution"]["execution_id"] == job_id
    assert ledger.connection().execute(
        "SELECT COUNT(*) FROM job_admissions WHERE job_id = ?", (job_id,),
    ).fetchone()[0] == 1


def test_queued_initial_step_consumes_one_provider_action_and_returns_paused(
    durable_job,
):
    runner, _ledger, execution_store, _db = durable_job
    job_id, execution = _execution(runner, execution_store)
    ws = _WS("p1")
    _run_runtime_action("execution.step", ws, {
        "type": "execution.command", "action": "execution.step",
        "command_id": "cmd-initial-step", "execution_id": job_id,
        "expected_version": execution.status_version, "payload": {},
    })
    frame = _command_frame(ws)
    assert frame["command"]["status"] in {"applying", "applied"}
    assert frame["execution"]["status"] == "paused"
    assert frame["execution"]["checkpoint_head_id"]
    assert frame["command"]["managed_action_count"] == 1


def test_resource_shortage_accepts_continue_and_exposes_queue_wait(durable_job):
    runner, _ledger, execution_store, _db = durable_job
    job_id, execution = _execution(runner, execution_store)
    ws = _WS("p1")
    _run_runtime_action("execution.pause", ws, {
        "type": "execution.command", "action": "execution.pause",
        "command_id": "cmd-shortage-pause", "execution_id": job_id,
        "expected_version": execution.status_version, "payload": {},
    })
    paused = execution_store().get_execution(job_id)
    assert paused is not None
    _run_runtime_action("execution.continue", ws, {
        "type": "execution.command", "action": "execution.continue",
        "command_id": "cmd-shortage-continue", "execution_id": job_id,
        "expected_version": paused.status_version, "payload": {},
    })
    frame = _command_frame(ws)
    assert frame["command"]["status"] == "accepted"
    assert frame["execution"]["resource"]["queue_wait"]["state"] in {
        "queued_resume", "paused_waiting_claim",
    }


def test_restart_replays_cross_db_intents_without_minting_another_execution(
    durable_job,
):
    runner, ledger, execution_store, db = durable_job
    job_id, execution = _execution(runner, execution_store)
    ws = _WS("p1")
    _run_runtime_action("execution.pause", ws, {
        "type": "execution.command", "action": "execution.pause",
        "command_id": "cmd-restart-pause", "execution_id": job_id,
        "expected_version": execution.status_version, "payload": {},
    })
    runner.shutdown()

    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.resource_governance import ResourceGovernor

    restarted = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    try:
        recovered = execution_store().get_execution(job_id)
        assert recovered is not None
        assert recovered.execution_id == job_id
        assert recovered.status.value == "paused"
        assert restarted.get_job(job_id).id == job_id
        assert restarted.get_job_resource_view(job_id).to_dict()["execution_id"] == job_id
        assert db.exists()
    finally:
        restarted.shutdown()


def test_admission_and_release_are_replayable_cross_database_intents(durable_job):
    runner, _ledger, execution_store, _db = durable_job
    job_id, _execution_record = _execution(runner, execution_store)
    execution = execution_store().get_execution(job_id)
    assert execution is not None
    _run_runtime_action("execution.pause", _WS("p1"), {
        "type": "execution.command", "action": "execution.pause",
        "command_id": "cmd-intent-pause", "execution_id": job_id,
        "expected_version": execution.status_version, "payload": {},
    })
    events = execution_store().list_events(job_id)
    kinds = {event.kind for event in events}

    assert "execution.admission.intent" in kinds
    assert "resource.admission.intent" in kinds
    assert "execution.release.intent" in kinds
    assert "resource.release.intent" in kinds


def test_deferred_resume_reuses_input_hash_and_execution_without_replaying_turn(
    durable_job,
):
    runner, _ledger, execution_store, _db = durable_job
    job_id = _spawn(
        runner,
        prompt="deferred turn",
        caller_msg_id="caller-msg",
        caller_session_id="p1",
        creates_agent=False,
    )
    before = execution_store().get_execution_input(job_id)
    assert before is not None
    before_payload = before.input_ref
    resumed_id = runner.spawn_job(
        session_id="p1", prompt="deferred turn", agent_id="main",
        parent_msg_id="target-head-2", caller_msg_id="caller-msg",
        caller_session_id="p1", creates_agent=False, job_id=job_id,
        resume_deferred=True, defer_dispatch=True,
    )
    after = execution_store().get_execution_input(resumed_id)
    assert resumed_id == job_id
    assert after is not None
    assert after.execution_id == before.execution_id
    assert after.input_hash == before.input_hash
    assert after.input_ref == before_payload




def test_child_deferred_and_borrowed_relations_preserve_caller_dag_identity(
    durable_job,
):
    runner, _ledger, execution_store, _db = durable_job
    parent_id, parent = _execution(runner, execution_store, caller_msg_id="root-msg")
    child_id = _spawn(
        runner,
        prompt="child turn",
        parent_job_id=parent_id,
        caller_msg_id="caller-msg",
        caller_session_id="p1",
        creates_agent=True,
    )
    child = execution_store().get_execution(child_id)
    assert child is not None
    assert child.parent_execution_id == parent_id
    assert child.execution_id != parent_id

    deferred_id = _spawn(
        runner,
        prompt="deferred turn",
        caller_msg_id="caller-msg",
        caller_session_id="p1",
        creates_agent=False,
    )
    resumed_id = runner.spawn_job(
        session_id="p1", prompt="deferred turn", agent_id="main",
        parent_msg_id="target-head-2", caller_msg_id="caller-msg",
        caller_session_id="p1", creates_agent=False, job_id=deferred_id,
        resume_deferred=True, defer_dispatch=True,
    )
    assert resumed_id == deferred_id
    resumed_input = execution_store().get_execution_input(resumed_id)
    assert resumed_input is not None
    assert json.loads(resumed_input.input_ref.removeprefix("job-input-v1:"))["job_context"]["caller"]["node_id"] == "caller-msg"
    assert parent.execution_id != resumed_id


def test_runtime_control_rejects_forged_project_session_attempt_and_capability(
    durable_job,
):
    runner, _ledger, execution_store, _db = durable_job
    job_id, execution = _execution(runner, execution_store)
    ws = _WS("other-session")
    _run_runtime_action("execution.pause", ws, {
        "type": "execution.command", "action": "execution.pause",
        "command_id": "cmd-forged", "execution_id": job_id,
        "expected_version": execution.status_version,
        "project_id": "forged-project", "attempt_id": "forged-attempt",
        "resource_lease_generation": 999, "capabilities": {"pause": True},
        "payload": {},
    })
    frame = _command_frame(ws)
    assert frame["command"]["status"] == "rejected"
    assert frame["command"]["rejection_code"] == "not_found"

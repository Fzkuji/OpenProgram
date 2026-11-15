"""driver activation tests."""
from __future__ import annotations
from openprogram.runtime.execution.model import CommandStatus, ExecutionStatus
from ._support import (
    AttemptStore,
    ExecutionStore,
    SimpleNamespace,
    _admitted,
    asyncio,
    sqlite3,
    threading,
)


def test_admission_persists_a_replayable_agent_turn_payload(tmp_path):
    store, execution = _admitted(tmp_path)

    assert store.get_agent_turn_input(execution.execution_id) == {
        "version": 1,
        "kind": "chat",
        "request": {
            "user_text": "durable agent turn",
            "agent_id": "default",
            "source": "web",
            "permission_mode": "ask",
        },
    }



def test_ordinary_agent_and_job_advertise_branch_capabilities():
    from openprogram.runtime.jobs.input import JobAgentInputV1
    from openprogram.runtime.jobs.types import Job
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver

    chat = {
        "version": 1,
        "kind": "chat",
        "request": {
            "user_text": "ordinary agent turn",
            "agent_id": "default",
            "source": "web",
        },
    }
    job = JobAgentInputV1.from_job(
        Job(id="job-branch-capability", parent_session_id="session-1", prompt="ordinary job", agent_id="default"),
        run_id="run-branch-capability",
    ).to_dict()

    for payload in (chat, job):
        capabilities = AgentProductionDriver.capabilities_for_payload(payload)
        assert capabilities.fork is True
        assert capabilities.retry is True

    for text in ("/forced_tool", "/spawn child", "/merge child"):
        capabilities = AgentProductionDriver.capabilities_for_payload({
            "version": 1,
            "kind": "chat",
            "request": {"user_text": text, "agent_id": "default", "source": "web"},
        })
        assert capabilities.fork is False
        assert capabilities.retry is False



def test_public_retry_gate_accepts_an_ordinary_agent_execution(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.runtime.execution import ExecutionStore
    from openprogram.interfaces.server.ws_actions.runtime import submit_execution_control

    store = ExecutionStore(tmp_path / "execution.sqlite3")
    payload = {
        "version": 1,
        "kind": "chat",
        "request": {
            "user_text": "ordinary agent turn",
            "agent_id": "default",
            "source": "web",
        },
    }
    revision = store.create_revision(manifest={"entrypoint": "agent"})
    execution = store.admit_execution(
        execution_id="exec-public-branch-capability",
        run_id="run-public-branch-capability",
        session_id="session-public-branch-capability",
        revision_id=revision.revision_id,
        input_ref="input:public-branch-capability",
        input_hash="hash-public-branch-capability",
        entrypoint="openprogram.runtime.conversation.production_driver:AgentProductionDriver",
        trusted_actor={"subject": "owner"},
        config_snapshot_ref="config:public-branch-capability",
        capabilities=AgentProductionDriver.capabilities_for_payload(payload),
        agent_turn_payload=payload,
    )
    called: list[str] = []
    command = SimpleNamespace(status=CommandStatus.APPLIED)
    service = SimpleNamespace(
        effects=SimpleNamespace(list_unresolved=lambda _execution_id: []),
        request_retry=lambda **_kwargs: (
            called.append("retry") or SimpleNamespace(command=command, execution=execution)
        ),
    )
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.runtime.execution.default_control_service", lambda: service)
    monkeypatch.setattr("openprogram.runtime.jobs.runner.runner_for_execution_store", lambda _store: None)

    actor = {
        "speaker_kind": "owner", "speaker_id": "owner/local", "speaker_display": "Owner",
        "authority_tier": "owner", "principal_id": "owner/install/0123456789abcdef",
        "interaction": "interactive",
    }
    returned, _snapshot = asyncio.run(submit_execution_control(
        {
            "type": "execution.command", "action": "execution.retry",
            "command_id": "retry-public-branch-capability",
            "execution_id": execution.execution_id,
            "expected_version": execution.status_version,
            "payload": {},
        },
        "retry",
        actor=actor,
        bound_session=execution.session_id,
    ))

    assert returned is command
    assert called == ["retry"]



def test_activation_builds_existing_turn_from_immutable_input(tmp_path):
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver

    store, execution = _admitted(tmp_path)
    seen = {}

    def resolve(record):
        seen["record"] = record
        return {
            "version": 1,
            "kind": "chat",
            "request": {
                "user_text": "continue the existing turn",
                "agent_id": "default",
                "source": "canonical-agent",
                "permission_mode": "bypass",
            },
        }

    def run_turn(*, request, cancel_event):
        assert isinstance(request, TurnRequest)
        seen["request"] = request
        assert cancel_event is not None
        return type("Result", (), {"failed": False, "error": None})()

    driver = AgentProductionDriver(
        executions=store,
        input_resolver=resolve,
        turn_runner=run_turn,
    )
    attempts = AttemptStore(store)
    attempt, leased_execution = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="agent-owner",
    )
    active_attempt, running = attempts.activate(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=leased_execution.status_version,
    )

    async def run():
        binding = await driver.activate(active_attempt, activation=None)
        driver.activation_committed(binding)
        handle = binding.handle
        await handle.done
        return handle

    handle = asyncio.run(run())
    assert seen["record"].input_ref == "input:exec-agent-1"
    assert seen["request"].session_id == execution.session_id
    assert seen["request"].user_text == "continue the existing turn"
    assert handle.execution_id == execution.execution_id
    assert handle.attempt_id == active_attempt.attempt_id
    assert handle.generation == active_attempt.generation
    completed = store.get_execution(execution.execution_id)
    assert completed is not None
    assert completed.status is ExecutionStatus.COMPLETED



def test_activation_uses_the_durable_agent_turn_payload_by_default(tmp_path):
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver

    store, execution = _admitted(tmp_path)
    seen = {}

    def run_turn(*, request, cancel_event):
        seen["request"] = request
        assert cancel_event is not None
        return type("Result", (), {"failed": False, "error": None})()

    driver = AgentProductionDriver(executions=store, turn_runner=run_turn)
    attempts = AttemptStore(store)
    attempt, leased = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="agent-owner",
    )
    active, _running = attempts.activate(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=leased.status_version,
    )
    async def run():
        binding = await driver.activate(active, activation=None)
        driver.activation_committed(binding)
        await binding.handle.done

    asyncio.run(run())
    assert seen["request"].user_text == "durable agent turn"
    assert seen["request"].source == "web"



def test_canonical_entry_activates_goal_resume_without_mutating_frozen_input(tmp_path, monkeypatch):
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver, CanonicalAgentEntry

    store = ExecutionStore(tmp_path / "executions.sqlite3")
    seen = {}
    entered, release = threading.Event(), threading.Event()
    def run_tool(**kwargs):
        seen.update(kwargs)
        entered.set()
        assert release.wait(30)
        return SimpleNamespace(failed=False, error=None)
    monkeypatch.setattr("openprogram.runtime.conversation.turn_runtime.dispatch_forced_tool_call", run_tool)
    driver = AgentProductionDriver(executions=store)
    entry = CanonicalAgentEntry(store, driver)
    admission = entry.admit(
        session_id="goal-resume",
        turn_payload={"version": 1, "kind": "forced_tool", "tool_name": "goal",
                      "tool_input": {"prompt": "finish", "resume": True}},
        trusted_actor={"subject": "user-1"}, config_snapshot_ref="config:test",
        user_message_id="u", assistant_message_id="a",
    )
    async def run():
        try:
            active = await entry.activate(admission)
            assert await asyncio.to_thread(entered.wait, 2)
            handle = driver._handles[(admission.execution_id, active.attempt_id, active.generation)]
            release.set()
            await handle.done
        finally:
            release.set()
    asyncio.run(run())
    assert seen["tool_name"] == "goal"
    assert seen["tool_input"]["resume"] is True
    assert store.get_execution(admission.execution_id).status is ExecutionStatus.COMPLETED



def test_internal_canonical_entry_admits_before_activation_with_exact_identity(tmp_path):
    from openprogram.runtime.conversation.production_driver import (
        AgentProductionDriver,
        CanonicalAgentEntry,
    )

    store = ExecutionStore(tmp_path / "executions.sqlite3")
    seen = {}
    entered = threading.Event()
    release = threading.Event()

    def run_turn(*, request, cancel_event):
        seen["request"] = request
        assert cancel_event is not None
        entered.set()
        assert release.wait(30)
        return type("Result", (), {"failed": False, "error": None})()

    driver = AgentProductionDriver(executions=store, turn_runner=run_turn)
    entry = CanonicalAgentEntry(store, driver)
    admission = entry.admit(
        session_id="session-entry",
        turn_payload={
            "version": 1,
            "kind": "chat",
            "request": {
                "user_text": "canonical public turn",
                "agent_id": "default",
                "source": "web",
                "permission_mode": "ask",
            },
        },
        trusted_actor={"subject": "user-1"},
        user_message_id="msg-user",
        assistant_message_id="msg-assistant",
        config_snapshot_ref="config:entry",
    )
    queued = store.get_execution(admission.execution_id)
    assert queued is not None
    assert queued.status is ExecutionStatus.QUEUED
    assert admission.execution_id.startswith("exec_")
    assert admission.execution_id != "msg-user_reply"
    assert store.get_agent_turn_input(admission.execution_id)["request"]["user_text"] == "canonical public turn"

    async def run():
        active = await entry.activate(admission)
        assert await asyncio.to_thread(entered.wait, 2)
        handle = driver._handles[(active.admission.execution_id, active.attempt_id, active.generation)]
        release.set()
        await handle.done
        return active

    active = asyncio.run(run())
    assert active.admission.execution_id == admission.execution_id
    assert seen["request"].user_text == "canonical public turn"
    completed = store.get_execution(admission.execution_id)
    assert completed is not None
    assert completed.status is ExecutionStatus.COMPLETED


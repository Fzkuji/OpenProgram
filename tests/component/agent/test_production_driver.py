from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from openprogram.execution import AttemptStore, CapabilitySet, ExecutionStore
from openprogram.execution._schema import SCHEMA_VERSION
from openprogram.execution.model import CommandKind, CommandStatus, ExecutionStatus


def _admitted(tmp_path, *, execution_id="exec-agent-1"):
    store = ExecutionStore(tmp_path / "executions.sqlite3")
    attempts = AttemptStore(store)
    revision = store.create_revision(
        revision_id="revision-agent-1", manifest={"entrypoint": "agent"}
    )
    execution = store.admit_execution(
        execution_id=execution_id,
        run_id="run-agent-1",
        session_id="session-agent-1",
        revision_id=revision.revision_id,
        input_ref=f"input:{execution_id}",
        input_hash="input-hash-1",
        entrypoint="openprogram.agent.dispatcher:process_user_turn",
        trusted_actor={"subject": "user-1", "session_id": "session-agent-1"},
        config_snapshot_ref="config:agent-1",
        capabilities=CapabilitySet(
            pause=True,
            step=True,
            steer=True,
            safe_point_kinds=(
                "agent.provider.decision.after",
                "agent.tool.action.after",
                "agent.wait.before_tool",
            ),
            state_schema_version=1,
        ),
        agent_turn_payload={
            "version": 1,
            "kind": "chat",
            "request": {
                "user_text": "durable agent turn",
                "agent_id": "default",
                "source": "web",
                "permission_mode": "ask",
            },
        },
    )
    return store, execution


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


def test_v6_migration_adds_durable_agent_turn_inputs(tmp_path):
    store, execution = _admitted(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE execution_agent_turn_inputs")
        connection.execute("PRAGMA user_version = 6")
        connection.commit()

    migrated = ExecutionStore(store.path)

    assert migrated.get_agent_turn_input(execution.execution_id) is None
    with sqlite3.connect(migrated.path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert "execution_agent_turn_inputs" in tables


def test_v7_migration_backfills_agent_finish_slots_idempotently(tmp_path):
    store, execution = _admitted(tmp_path, execution_id="exec-v7-slot-backfill")
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE execution_finish_repair_slots")
        connection.execute("PRAGMA user_version = 7")
        connection.commit()

    migrated = ExecutionStore(store.path)
    with sqlite3.connect(migrated.path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        rows = connection.execute(
            "SELECT execution_id FROM execution_finish_repair_slots"
        ).fetchall()
    assert version == SCHEMA_VERSION
    assert rows == [(execution.execution_id,)]

    reopened = ExecutionStore(store.path)
    with sqlite3.connect(reopened.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_finish_repair_slots"
        ).fetchone()[0] == 1


def test_v7_migration_marks_overflow_nonterminal_agents_for_reconciliation(
    tmp_path, monkeypatch,
):
    from openprogram.execution import _schema

    monkeypatch.setattr(_schema, "_FINISH_REPAIR_SLOT_LIMIT", 1)
    store, first = _admitted(tmp_path, execution_id="exec-v7-overflow-1")
    revision = store.create_revision(
        revision_id="revision-v7-overflow-2", manifest={"entrypoint": "agent", "slot": 2}
    )
    second = store.admit_execution(
        execution_id="exec-v7-overflow-2",
        run_id="run-v7-overflow-2",
        session_id="session-v7-overflow-2",
        revision_id=revision.revision_id,
        input_ref="input:v7-overflow-2",
        input_hash="input-hash-v7-overflow-2",
        entrypoint="openprogram.agent.dispatcher:process_user_turn",
        trusted_actor={"subject": "test"},
        config_snapshot_ref="config:v7-overflow-2",
        agent_turn_payload={
            "version": 1,
            "kind": "chat",
            "request": {"user_text": "run", "agent_id": "default", "source": "test"},
        },
    )
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_finish_repair_slots"
        ).fetchone()[0] == 2
        connection.execute("PRAGMA user_version = 7")
        connection.commit()

    migrated = ExecutionStore(store.path)

    with sqlite3.connect(migrated.path) as connection:
        slots = connection.execute(
            "SELECT execution_id FROM execution_finish_repair_slots"
        ).fetchall()
    assert slots == [(first.execution_id,)]
    overflow = migrated.get_execution(second.execution_id)
    assert overflow is not None
    assert overflow.status is ExecutionStatus.RECONCILIATION_REQUIRED
    assert overflow.reason_code == "finish_repair_capacity_migration"
    assert migrated.get_agent_turn_input(second.execution_id) is not None


def test_queued_agent_cancel_releases_reserved_slot_immediately(tmp_path):
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry

    store, execution = _admitted(tmp_path, execution_id="exec-queued-slot-cancel")
    service = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    cancelled = asyncio.run(
        service.request_cancel(
            command_id="cancel-queued-slot",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
            reason_code="user_cancelled",
        )
    )
    assert cancelled.execution.status is ExecutionStatus.CANCELLED
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_finish_repair_slots"
        ).fetchone()[0] == 0


def test_agent_driver_declares_only_p0_safe_point_capabilities():
    from openprogram.agent.production_driver import AgentProductionDriver

    driver = AgentProductionDriver(
        executions=None,
        input_resolver=lambda _record: {},
        turn_runner=lambda **_kwargs: None,
    )

    assert driver.capabilities() == CapabilitySet(
        pause=True,
        step=True,
        steer=True,
        fork=True,
        retry=True,
        safe_point_kinds=(
            "agent.provider.decision.after",
            "agent.tool.action.after",
            "agent.wait.before_tool",
        ),
        state_schema_version=1,
    )


def test_gui_agent_safe_point_waits_before_tool_effect_and_resumes_once(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from openprogram import system_access
    from openprogram.agent.continuation import runtime_contract_snapshot
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry
    from openprogram.execution.effects import EffectStore
    from openprogram.execution.waits import DurableWaitStore, WaitStatus
    from openprogram.providers.types import Model

    store, execution = _admitted(tmp_path, execution_id="exec-system-public")
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id, expected_version=execution.status_version,
        owner_id="system-public", ttl_seconds=30,
    )
    active, running = attempts.activate(
        leased.attempt_id, generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    activations = []

    async def activate(next_attempt, activation):
        activations.append((next_attempt.execution_id, activation.checkpoint.checkpoint_id))

    control = RuntimeControlService(
        store, attempts, DriverRegistry(), activator=activate,
    )
    driver = AgentProductionDriver(store, control_service=control)
    frames = []
    monkeypatch.setattr("openprogram.events.emit_ws_frame", frames.append)
    request = TurnRequest(
        session_id=running.session_id, user_text="run GUI", agent_id="default",
        source="component", user_msg_id="user-system-public",
    )
    request._execution_revision_id = running.revision_id
    hook = driver._safe_point_hook(active, request, threading.Event())
    snapshot = runtime_contract_snapshot(
        model=Model(id="fake", name="fake", api="openai-completions", provider="openai", base_url="https://example.invalid/v1"),
        system_prompt="system", tools=[], request=request,
    )
    monkeypatch.setattr(system_access.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(system_access, "report", lambda: {
        "platform": "Darwin",
        "capabilities": [
            {"id": "screen_recording", "status": "not_granted", "can_request": True},
            {"id": "accessibility", "status": "not_granted", "can_request": True},
        ],
    })
    args = {"task": "Open the desktop app", "surface": "desktop"}
    manifest = system_access.access_manifest_for_tool("gui_agent", args)
    assert manifest is not None

    assert hook("provider.before", {
        "resolved_snapshot": snapshot, "context": {"messages": []},
        "supports_idempotency_key": True,
    }) is False
    assert hook("provider.after", {
        "message": {"role": "assistant", "content": [], "api": "fake",
                     "provider": "fake", "model": "fake"},
        "provider_request_id": "request-system-public", "usage": {},
    }) is False
    payload = {
        "tool_call_id": "gui-call", "tool_name": "gui_agent",
        "arguments": args, "next_tool_index": 0, "pre_wait": manifest,
    }
    assert hook("tool.before", payload) is True
    paused = store.get_execution(execution.execution_id)
    assert paused is not None and paused.status is ExecutionStatus.PAUSED
    assert paused.reason_code == "system_access_required"
    wait = DurableWaitStore(store).list_open(execution_id=execution.execution_id)[0]
    assert wait.kind == "system_access" and wait.expires_at == 0
    waiting = next(frame for frame in frames if frame["type"] == "system_access.waiting")
    assert waiting["data"]["live"] is True
    assert not [effect for effect in EffectStore(store).list_unresolved(execution.execution_id)
                if effect.metadata.get("kind") == "tool.before"]

    monkeypatch.setattr(system_access, "report", lambda: {
        "platform": "Darwin",
        "capabilities": [
            {"id": "screen_recording", "status": "granted"},
            {"id": "accessibility", "status": "granted"},
        ],
    })
    asyncio.run(control.recover_wait_outcomes())
    asyncio.run(control.recover_wait_outcomes())
    resumed = store.get_execution(execution.execution_id)
    assert resumed is not None and resumed.status is ExecutionStatus.RUNNING
    assert activations == [(execution.execution_id, wait.checkpoint_id)]
    assert DurableWaitStore(store).get_wait(wait.wait_id).status is WaitStatus.RESOLVED


def test_forced_gui_entry_uses_durable_system_wait_before_subprocess(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from openprogram import system_access
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry
    from openprogram.execution.waits import DurableWaitStore

    store = ExecutionStore(tmp_path / "forced-system.sqlite3")
    revision = store.create_revision(manifest={"entrypoint": "forced"})
    payload = {
        "version": 1, "kind": "forced_tool", "tool_name": "gui_agent",
        "tool_input": {"task": "Open desktop", "surface": "desktop"},
        "source": "web", "agent_id": "main",
    }
    execution = store.admit_execution(
        execution_id="exec-forced-system", run_id="run-forced-system",
        session_id="session-forced-system", revision_id=revision.revision_id,
        input_ref="input:forced-system", input_hash="forced-system-hash",
        entrypoint="openprogram.agent.production_driver:AgentProductionDriver",
        trusted_actor={"subject": "owner"}, config_snapshot_ref="config:forced-system",
        capabilities=AgentProductionDriver.capabilities_for_payload(payload),
        agent_turn_payload=payload,
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id, expected_version=execution.status_version,
        owner_id="forced-system", ttl_seconds=30,
    )
    active, running = attempts.activate(
        leased.attempt_id, generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    calls = []

    def fake_dispatch(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("openprogram.agent.dispatcher.dispatch_forced_tool_call", fake_dispatch)
    frames = []
    monkeypatch.setattr("openprogram.events.emit_ws_frame", frames.append)
    monkeypatch.setattr(system_access.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(system_access, "report", lambda: {
        "platform": "Darwin",
        "capabilities": [
            {"id": "screen_recording", "status": "not_granted", "can_request": True},
            {"id": "accessibility", "status": "not_granted", "can_request": True},
        ],
    })
    control = RuntimeControlService(store, attempts, DriverRegistry())
    driver = AgentProductionDriver(store, control_service=control)

    async def resume(attempt, activation):
        binding = await driver.activate(attempt, activation)
        driver.activation_committed(binding)
        await binding.handle.done

    control.activator = resume
    binding = asyncio.run(driver.activate(active, activation=None))
    driver.activation_committed(binding)
    async def wait_done():
        return await binding.handle.done
    asyncio.run(wait_done())
    wait = DurableWaitStore(store).list_open(execution_id=execution.execution_id)[0]
    assert wait.kind == "system_access"
    waiting = next(frame for frame in frames if frame["type"] == "system_access.waiting")
    assert waiting["data"]["live"] is True
    assert calls == []

    monkeypatch.setattr(system_access, "report", lambda: {
        "platform": "Darwin",
        "capabilities": [
            {"id": "screen_recording", "status": "granted"},
            {"id": "accessibility", "status": "granted"},
        ],
    })
    asyncio.run(control.recover_wait_outcomes())
    asyncio.run(control.recover_wait_outcomes())
    assert len(calls) == 1
    resumed = store.get_execution(execution.execution_id)
    assert resumed is not None and resumed.status is ExecutionStatus.COMPLETED


def test_ordinary_agent_and_job_advertise_branch_capabilities():
    from openprogram.agent.job.input import JobAgentInputV1
    from openprogram.agent.job.types import Job
    from openprogram.agent.production_driver import AgentProductionDriver

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

    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.execution import ExecutionStore
    from openprogram.webui.ws_actions.runtime import submit_execution_control

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
        entrypoint="openprogram.agent.production_driver:AgentProductionDriver",
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
    monkeypatch.setattr("openprogram.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.execution.default_control_service", lambda: service)
    monkeypatch.setattr("openprogram.agent.job.runner.runner_for_execution_store", lambda _store: None)

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


def test_production_driver_consumes_running_steer_fifo_at_provider_safe_point(tmp_path):
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.agent.continuation import runtime_contract_snapshot
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.providers.types import Model

    store, execution = _admitted(tmp_path, execution_id="exec-running-steer")
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="owner-steer",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    control = RuntimeControlService(store, attempts, DriverRegistry())
    first = control.request_steer(
        command_id="steer-first",
        execution_id=execution.execution_id,
        expected_version=running.status_version,
        actor={"surface": "test"},
        payload={"message": "first instruction"},
    )
    second = control.request_steer(
        command_id="steer-second",
        execution_id=execution.execution_id,
        expected_version=running.status_version,
        actor={"surface": "test"},
        payload={"message": "second instruction"},
    )
    assert first.command.status is CommandStatus.ACCEPTED
    assert second.command.status is CommandStatus.ACCEPTED

    queue: list[dict] = []
    consumed: set[str] = set()
    request = TurnRequest(
        session_id=running.session_id,
        user_text="durable agent turn",
        agent_id="default",
        source="component",
        user_msg_id="user-anchor",
    )
    request._execution_revision_id = running.revision_id
    hook = AgentProductionDriver(store, control_service=control)._safe_point_hook(
        active,
        request,
        threading.Event(),
        steer_queue=queue,
        steer_consumed_ids=consumed,
    )
    snapshot = runtime_contract_snapshot(
        model=Model(
            id="fake", name="fake", api="openai-completions", provider="openai",
            base_url="https://example.invalid/v1",
        ),
        system_prompt="system", tools=[],
        request=request,
    )
    assert hook("provider.before", {
        "resolved_snapshot": snapshot,
        "context": {"messages": []},
        "supports_idempotency_key": True,
    }) is False
    assert hook("provider.after", {
        "message": {
            "role": "assistant", "content": [],
            "api": "fake", "provider": "fake", "model": "fake",
        },
        "provider_request_id": "request-steer",
        "usage": {},
    }) is False

    # A later safe point must not enqueue the same pending delivery twice.
    assert hook("provider.before", {
        "resolved_snapshot": snapshot,
        "context": {"messages": ["next"]},
        "supports_idempotency_key": True,
    }) is False
    assert hook("provider.after", {
        "message": {
            "role": "assistant", "content": [],
            "api": "fake", "provider": "fake", "model": "fake",
        },
        "provider_request_id": "request-steer-next",
        "usage": {},
    }) is False

    assert [item["command_id"] for item in queue] == [
        "steer-first", "steer-second",
    ]
    assert [item["payload"]["message"] for item in queue] == [
        "first instruction", "second instruction",
    ]
    # This fixture exercises safe-point queueing only. Delivery becomes
    # APPLIED after the dispatcher persists the branch-linked user message.
    assert all(
        store.get_command(command_id).status is CommandStatus.APPLYING
        for command_id in ("steer-first", "steer-second")
    )
    current = store.get_execution(execution.execution_id)
    assert current is not None and current.status is ExecutionStatus.RUNNING
    assert current.current_attempt_id == active.attempt_id


def test_production_pause_precedes_steer_and_paused_steer_is_next_activation_input(tmp_path):
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.agent.continuation import runtime_contract_snapshot
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.providers.types import Model

    store, execution = _admitted(tmp_path, execution_id="exec-pause-steer")
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="owner-pause-steer",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    control = RuntimeControlService(store, attempts, DriverRegistry())
    request = TurnRequest(
        session_id=running.session_id,
        user_text="durable agent turn",
        agent_id="default",
        source="component",
        user_msg_id="user-anchor",
    )
    request._execution_revision_id = running.revision_id
    snapshot = runtime_contract_snapshot(
        model=Model(
            id="fake", name="fake", api="openai-completions", provider="openai",
            base_url="https://example.invalid/v1",
        ),
        system_prompt="system", tools=[], request=request,
    )
    queue: list[dict] = []
    hook = AgentProductionDriver(store, control_service=control)._safe_point_hook(
        active, request, threading.Event(), steer_queue=queue,
        steer_consumed_ids=set(),
    )
    hook("provider.before", {
        "resolved_snapshot": snapshot,
        "context": {"messages": []},
        "supports_idempotency_key": True,
    })
    steer = control.request_steer(
        command_id="steer-after-pause",
        execution_id=execution.execution_id,
        expected_version=running.status_version,
        actor={"surface": "test"},
        payload={"message": "continue with source B"},
    )
    asyncio.run(control.request_pause(
        command_id="pause-before-steer",
        execution_id=execution.execution_id,
        expected_version=running.status_version,
        actor={"surface": "test"},
    ))
    assert hook("provider.after", {
        "message": {
            "role": "assistant", "content": [],
            "api": "fake", "provider": "fake", "model": "fake",
        },
        "provider_request_id": "request-pause-steer",
        "usage": {},
    }) is True
    paused = store.get_execution(execution.execution_id)
    assert paused is not None and paused.status is ExecutionStatus.PAUSED
    assert store.get_command("pause-before-steer").status is CommandStatus.APPLIED
    assert store.get_command(steer.command.command_id).status is CommandStatus.ACCEPTED
    assert queue == []

    activation_inputs = []

    async def activate(_attempt, activation):
        activation_inputs.append(activation)

    continued = asyncio.run(control.request_continue(
        command_id="continue-after-pause-steer",
        execution_id=execution.execution_id,
        expected_version=paused.status_version,
        actor={"surface": "test"},
        activator=activate,
    ))
    assert continued.command.status is CommandStatus.APPLIED
    assert len(activation_inputs) == 1
    assert activation_inputs[0].steer_inputs[0]["command_id"] == steer.command.command_id


def test_activation_builds_existing_turn_from_immutable_input(tmp_path):
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.agent.production_driver import AgentProductionDriver

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
        ttl_seconds=30,
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
    from openprogram.agent.production_driver import AgentProductionDriver

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
        ttl_seconds=30,
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
    from openprogram.agent.production_driver import AgentProductionDriver, CanonicalAgentEntry

    store = ExecutionStore(tmp_path / "executions.sqlite3")
    seen = {}
    entered, release = threading.Event(), threading.Event()
    def run_tool(**kwargs):
        seen.update(kwargs)
        entered.set()
        assert release.wait(2)
        return SimpleNamespace(failed=False, error=None)
    monkeypatch.setattr("openprogram.agent.dispatcher.dispatch_forced_tool_call", run_tool)
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


def test_canonical_entry_renews_owner_until_long_work_finishes(tmp_path, monkeypatch):
    from openprogram.agent import production_driver as module

    # Use the real public admission/activation and persistence. Only shorten
    # the production lease duration; no direct lifecycle-row mutations.
    monkeypatch.setattr(module, "AGENT_LEASE_SECONDS", 0.3, raising=False)
    store = ExecutionStore(tmp_path / "executions.sqlite3")
    entered, release = threading.Event(), threading.Event()
    def run_turn(*, request, cancel_event):
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(failed=False)
    driver = module.AgentProductionDriver(store, turn_runner=run_turn)
    entry = module.CanonicalAgentEntry(store, driver)
    original_lease = entry.control.attempts.lease
    monkeypatch.setattr(entry.control.attempts, "lease", lambda *a, **kw: original_lease(
        *a, **{**kw, "ttl_seconds": 0.3},
    ))
    admission = entry.admit(
        session_id="renewal", turn_payload={"version": 1, "kind": "chat", "request": {
            "user_text": "long work", "agent_id": "default", "source": "test"}},
        trusted_actor={"subject": "test"}, config_snapshot_ref="config:test",
        user_message_id="u", assistant_message_id="a",
    )
    async def run():
        try:
            active = await entry.activate(admission)
            assert await asyncio.to_thread(entered.wait, 2)
            handle = driver._handles[(admission.execution_id, active.attempt_id, active.generation)]
            before = entry.control.attempts.get(active.attempt_id)
            await asyncio.sleep(0.65)
            renewed = entry.control.attempts.get(active.attempt_id)
            assert renewed.lease_expires_at > before.lease_expires_at
            release.set()
            await asyncio.wait_for(handle.done, 3)
            assert store.get_execution(admission.execution_id).status is ExecutionStatus.COMPLETED
            assert store.list_finish_repairs(limit=10) == []
        finally:
            release.set()
    asyncio.run(run())


@pytest.mark.parametrize("stale_observation", [False, True])
def test_second_controller_startup_preserves_a_live_canonical_agent(tmp_path, monkeypatch, stale_observation):
    from openprogram.agent.production_driver import AgentProductionDriver, CanonicalAgentEntry
    from openprogram.execution import DriverRegistry, RuntimeControlService

    store = ExecutionStore(tmp_path / "executions.sqlite3")
    entered, release = threading.Event(), threading.Event()

    def work(*, request, cancel_event):
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(failed=False)

    driver = AgentProductionDriver(store, turn_runner=work)
    entry = CanonicalAgentEntry(store, driver)
    admission = entry.admit(
        session_id="live-goal-owner",
        turn_payload={"version": 1, "kind": "chat", "request": {
            "user_text": "work", "agent_id": "default", "source": "test"}},
        trusted_actor={"subject": "test"}, config_snapshot_ref="config:test",
        user_message_id="u", assistant_message_id="a",
    )
    before_activation = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    assert before_activation.recover_startup() == ()
    original_activate = entry.control.attempts.activate
    def activate_after_startup_check(*args, **kwargs):
        # The lease exists, but the physical driver has not started yet.
        assert before_activation.recover_startup() == ()
        return original_activate(*args, **kwargs)
    monkeypatch.setattr(entry.control.attempts, "activate", activate_after_startup_check)

    async def run():
        active = await entry.activate(admission)
        handle = driver._handles[(admission.execution_id, active.attempt_id, active.generation)]
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            second_store = ExecutionStore(store.path)
            second = RuntimeControlService(second_store, AttemptStore(second_store), DriverRegistry())
            if stale_observation:
                from openprogram.execution import process_owner
                original = process_owner.process_owner_may_be_alive
                observations = []
                def stale_once(*args, **kwargs):
                    observations.append(True)
                    return False if len(observations) == 1 else original(*args, **kwargs)
                monkeypatch.setattr(process_owner, "process_owner_may_be_alive", stale_once)
            recovered = second.recover_startup()
            assert all(item.execution.execution_id != admission.execution_id for item in recovered)
            assert second_store.get_execution(admission.execution_id).status is ExecutionStatus.RUNNING
        finally:
            release.set()
            await asyncio.wait_for(handle.done, 3)
        assert store.get_execution(admission.execution_id).status is ExecutionStatus.COMPLETED

    asyncio.run(run())


def _hold_canonical_agent_process(db_path, ready):
    from openprogram.agent.production_driver import AgentProductionDriver, CanonicalAgentEntry

    store = ExecutionStore(db_path)
    def work(*, request, cancel_event):
        ready.send(admission.execution_id)
        threading.Event().wait(20)
        return SimpleNamespace(failed=False)
    driver = AgentProductionDriver(store, turn_runner=work)
    entry = CanonicalAgentEntry(store, driver)
    admission = entry.admit(
        session_id="process-owner", turn_payload={"version": 1, "kind": "chat", "request": {
            "user_text": "work", "agent_id": "default", "source": "test"}},
        trusted_actor={"subject": "test"}, config_snapshot_ref="config:test",
        user_message_id="u", assistant_message_id="a",
    )
    async def run():
        active = await entry.activate(admission)
        await driver._handles[(admission.execution_id, active.attempt_id, active.generation)].done
    asyncio.run(run())


def test_startup_distinguishes_live_and_exited_owner_process(tmp_path):
    import multiprocessing
    from openprogram.execution import DriverRegistry, RuntimeControlService

    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    db_path = tmp_path / "process.sqlite3"
    process = context.Process(target=_hold_canonical_agent_process, args=(db_path, send))
    process.start()
    send.close()
    try:
        assert receive.poll(10), "child Agent did not enter work"
        execution_id = receive.recv()
        store = ExecutionStore(db_path)
        control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
        assert control.recover_startup() == ()
        assert store.get_execution(execution_id).status is ExecutionStatus.RUNNING
        process.terminate()
        process.join(5)
        assert not process.is_alive()
        recovered = control.recover_startup()
        assert [item.execution.execution_id for item in recovered] == [execution_id]
        assert store.get_execution(execution_id).status is ExecutionStatus.PAUSED
        assert store.get_execution(execution_id).reason_code == "restart_pending"
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        receive.close()
        process.close()


def test_process_identity_rejects_pid_reuse_and_preserves_unknown(monkeypatch):
    from openprogram.execution import process_owner

    lease = {"process_owner": process_owner.current_process_owner()}
    monkeypatch.setattr(process_owner, "process_start_identity", lambda _pid: "different-start")
    assert not process_owner.process_owner_may_be_alive(lease, lease_expires_at=time.time() + 30)
    monkeypatch.setattr(process_owner, "process_start_identity", lambda _pid: None)
    assert process_owner.process_owner_may_be_alive(lease, lease_expires_at=time.time() + 30)
    assert not process_owner.process_owner_may_be_alive(lease, lease_expires_at=time.time() - 1)


@pytest.mark.parametrize("cause", ["heartbeat_error", "owner_fenced", "cancel"])
def test_canonical_owner_renewal_stops_on_loss_or_cancel(tmp_path, monkeypatch, cause):
    from openprogram.agent import production_driver as module

    monkeypatch.setattr(module, "AGENT_LEASE_SECONDS", 0.3)
    store = ExecutionStore(tmp_path / "executions.sqlite3")
    entered, observed_cancel, release = threading.Event(), threading.Event(), threading.Event()
    def run_turn(*, request, cancel_event):
        entered.set()
        try:
            if cancel_event.wait(2):
                observed_cancel.set()
            assert release.wait(2)
            return SimpleNamespace(failed=False)
        finally:
            release.set()
    driver = module.AgentProductionDriver(store, turn_runner=run_turn)
    entry = module.CanonicalAgentEntry(store, driver)
    admission = entry.admit(
        session_id="renewal-loss", turn_payload={"version": 1, "kind": "chat", "request": {
            "user_text": "cancel work", "agent_id": "default", "source": "test"}},
        trusted_actor={"subject": "test"}, config_snapshot_ref="config:test",
        user_message_id="u", assistant_message_id="a",
    )
    async def run():
        try:
            active = await entry.activate(admission)
            assert await asyncio.to_thread(entered.wait, 2)
            handle = driver._handles[(admission.execution_id, active.attempt_id, active.generation)]
            if cause == "heartbeat_error":
                def fail(*a, **kw):
                    raise OSError("storage unavailable")
                monkeypatch.setattr(entry.control.attempts, "heartbeat", fail)
            elif cause == "owner_fenced":
                entry.control.recover_owner_loss(admission.execution_id,
                    attempt_id=active.attempt_id, generation=active.generation)
            else:
                current = store.get_execution(admission.execution_id)
                await entry.control.request_cancel(command_id="cancel-renewal",
                    execution_id=admission.execution_id, expected_version=current.status_version,
                    actor={"surface": "test"}, reason_code="user_cancelled")
            assert await asyncio.to_thread(observed_cancel.wait, 2)
            release.set()
            await asyncio.wait_for(handle.done, 3)
            final = store.get_execution(admission.execution_id)
            assert final.status is not ExecutionStatus.COMPLETED
            assert final.status is not ExecutionStatus.RUNNING
        finally:
            release.set()
    asyncio.run(run())


def test_renewal_loss_never_terminates_replacement_by_execution_id(tmp_path, monkeypatch):
    from openprogram.agent import production_driver as module

    monkeypatch.setattr(module, "AGENT_LEASE_SECONDS", 0.03)
    store, execution = _admitted(tmp_path)
    driver = module.AgentProductionDriver(store)
    service = driver._control_service()
    attempt, leased = service.attempts.lease(execution.execution_id,
        expected_version=execution.status_version, owner_id="old", ttl_seconds=30)
    active, _ = service.attempts.activate(attempt.attempt_id, generation=attempt.generation,
        expected_execution_version=leased.status_version)
    handle = module.AgentDriverHandle(execution_id=execution.execution_id,
        attempt_id=active.attempt_id, generation=active.generation,
        session_id=execution.session_id, cancel_event=threading.Event(),
        done=module._ThreadResultFuture())
    physical_kills, recoveries = [], []
    def lose_lease(*a, **kw):
        raise OSError("renewal failed")
    monkeypatch.setattr(service.attempts, "heartbeat", lose_lease)
    def stale_read(_attempt_id):
        # The read can race a handoff. Its stale ACTIVE value must not
        # authorize a kill against the shared execution's replacement.
        return active
    monkeypatch.setattr(service.attempts, "get", stale_read)
    async def terminate(*a, **kw):
        physical_kills.append("replacement")
    monkeypatch.setattr(driver, "terminate", terminate)
    monkeypatch.setattr(service, "recover_owner_loss", lambda execution_id, **kw:
        recoveries.append((execution_id, kw)))
    driver._maintain_owner(active, handle)
    assert handle.cancel_event.is_set()
    assert not physical_kills
    assert recoveries == [(execution.execution_id, {
        "attempt_id": active.attempt_id, "generation": active.generation})]


def test_internal_canonical_entry_admits_before_activation_with_exact_identity(tmp_path):
    from openprogram.agent.production_driver import (
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
        assert release.wait(2)
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


def test_cancel_targets_exact_handle_and_releases_its_question_wait(tmp_path, monkeypatch):
    import openprogram.execution as execution_module
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.agent.questions import PendingQuestion, QuestionRegistry
    from openprogram.execution.waits import DurableWaitStore

    store, execution = _admitted(tmp_path)
    registry = QuestionRegistry()
    entered = threading.Event()
    released = threading.Event()

    def run_turn(*, request, cancel_event):
        del request
        question = PendingQuestion(
            id="q-agent-1",
            session_id=execution.session_id,
            execution_id=execution.execution_id,
            kind="ask",
            prompt="continue?",
        )
        question_event = registry.register(question)
        entered.set()
        while not cancel_event.is_set() and not question_event.wait(0.01):
            pass
        released.set()
        return type("Result", (), {"failed": False, "error": None})()

    driver = AgentProductionDriver(
        executions=store,
        input_resolver=lambda _record: {
            "version": 1,
            "kind": "chat",
            "request": {
                "user_text": "ask",
                "agent_id": "default",
                "source": "canonical-agent",
            },
        },
        turn_runner=run_turn,
        question_registry=registry,
    )
    attempts = AttemptStore(store)
    attempt, leased = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="agent-owner",
        ttl_seconds=30,
    )
    active, _running = attempts.activate(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=leased.status_version,
    )
    monkeypatch.setattr(execution_module, "default_store", lambda: store)
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry
    control = RuntimeControlService(store, attempts, DriverRegistry())
    wait = DurableWaitStore(store).open_wait(
        wait_id="q-agent-1", execution_id=execution.execution_id,
        attempt_id=active.attempt_id, generation=active.generation,
        kind="ask",
        request={
            "prompt": "continue?", "options": [], "multi": False,
            "allow_custom": True, "detail": "", "schema": {}, "questions": [],
        },
        policy_snapshot={"version": 1}, expires_at=time.time() + 60,
    )
    async def run():
        binding = await driver.activate(active, activation=None)
        control._bind_driver(binding)
        handle = binding.handle
        assert await asyncio.to_thread(entered.wait, 2)
        current = store.get_execution(execution.execution_id)
        assert current is not None
        dispatch = await control.request_cancel(
            command_id="cancel-agent-1",
            execution_id=execution.execution_id,
            expected_version=current.status_version,
            actor={"surface": "test"},
            reason_code="cancel.user",
        )
        await handle.done
        await asyncio.sleep(0)
        return dispatch

    dispatch = asyncio.run(run())
    assert dispatch.command.command_id == "cancel-agent-1"
    assert dispatch.ack is not None
    assert dispatch.ack.attempt_id == active.attempt_id
    assert released.is_set()
    assert registry.consume("q-agent-1") == ("cancelled", None)
    assert not driver._finished
    cancelled = store.get_execution(execution.execution_id)
    assert cancelled is not None
    assert cancelled.status is ExecutionStatus.CANCELLED
    command = store.get_command("cancel-agent-1")
    assert command is not None
    assert command.status is CommandStatus.APPLIED
    assert driver._cancel_commands == {}


def test_cancel_rejects_a_handle_from_another_attempt(tmp_path):
    from openprogram.agent.production_driver import AgentDriverError, AgentProductionDriver

    store, execution = _admitted(tmp_path)
    driver = AgentProductionDriver(
        executions=store,
        input_resolver=lambda _record: {
            "version": 1,
            "kind": "chat",
            "request": {
                "user_text": "run",
                "agent_id": "default",
                "source": "canonical-agent",
            },
        },
        turn_runner=lambda **_kwargs: type(
            "Result", (), {"failed": False, "error": None}
        )(),
    )
    attempts = AttemptStore(store)
    attempt, leased = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="agent-owner",
        ttl_seconds=30,
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
        return binding.handle

    handle = asyncio.run(run())
    with pytest.raises(AgentDriverError) as stale:
        asyncio.run(driver.request_cancel(handle, "late-cancel"))
    assert stale.value.code == "stale_handle"


def test_runner_exception_finishes_as_failed(tmp_path):
    from openprogram.agent.production_driver import AgentProductionDriver

    store, execution = _admitted(tmp_path)

    def run_turn(*, request, cancel_event):
        del request, cancel_event
        raise RuntimeError("owner process lost")

    driver = AgentProductionDriver(
        executions=store,
        input_resolver=lambda _record: {
            "version": 1,
            "kind": "chat",
            "request": {
                "user_text": "run",
                "agent_id": "default",
                "source": "canonical-agent",
            },
        },
        turn_runner=run_turn,
    )
    attempts = AttemptStore(store)
    attempt, leased = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="agent-owner",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=leased.status_version,
    )

    async def run():
        binding = await driver.activate(active, activation=None)
        driver.activation_committed(binding)
        handle = binding.handle
        await handle.done
        return handle

    asyncio.run(run())
    recovered = store.get_execution(execution.execution_id)
    assert recovered is not None
    assert recovered.status is ExecutionStatus.FAILED
    assert recovered.reason_code == "agent_runner_error"


def test_finish_transient_failure_retries_after_handle_release(tmp_path):
    from openprogram.agent.production_driver import AgentProductionDriver

    store, execution = _admitted(tmp_path, execution_id="exec-finish-retry")
    driver = AgentProductionDriver(
        executions=store,
        input_resolver=lambda _record: {
            "version": 1,
            "kind": "chat",
            "request": {"user_text": "run", "agent_id": "default", "source": "test"},
        },
        turn_runner=lambda **_kwargs: None,
    )
    attempts = AttemptStore(store)
    attempt, leased = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="agent-owner",
        ttl_seconds=30,
    )
    active, _running = attempts.activate(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=leased.status_version,
    )
    control = driver._control_service()
    real_finish = control.finish_attempt
    calls = 0

    def flaky_finish(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("temporary sqlite failure")
        return real_finish(*args, **kwargs)

    control.finish_attempt = flaky_finish
    driver._finish_attempt(
        active,
        type("Result", (), {"failed": False, "error": None})(),
        threading.Event(),
    )
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = store.get_execution(execution.execution_id)
        if current is not None and current.status is ExecutionStatus.COMPLETED:
            break
        time.sleep(0.02)
    current = store.get_execution(execution.execution_id)
    assert current is not None
    assert current.status is ExecutionStatus.COMPLETED
    assert calls >= 2
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and driver._pending_finishes:
        time.sleep(0.01)
    assert driver._pending_finishes == {}


def test_finish_retry_re_reads_cancellation_state(tmp_path):
    from openprogram.agent.production_driver import AgentProductionDriver

    store, execution = _admitted(tmp_path, execution_id="exec-finish-cancel-race")
    driver = AgentProductionDriver(
        executions=store,
        input_resolver=lambda _record: {
            "version": 1,
            "kind": "chat",
            "request": {"user_text": "run", "agent_id": "default", "source": "test"},
        },
        turn_runner=lambda **_kwargs: None,
    )
    attempts = AttemptStore(store)
    attempt, leased = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="agent-owner",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=leased.status_version,
    )
    control = driver._control_service()
    real_finish = control.finish_attempt
    first_called = threading.Event()
    release = threading.Event()
    calls = 0

    def flaky_finish(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_called.set()
            assert release.wait(3)
            raise OSError("temporary sqlite failure")
        return real_finish(*args, **kwargs)

    control.finish_attempt = flaky_finish
    worker = threading.Thread(
        target=driver._finish_attempt,
        args=(active, type("Result", (), {"failed": False, "error": None})(), threading.Event()),
        daemon=True,
    )
    worker.start()
    assert first_called.wait(3)
    store.accept_command_with_transition(
        command_id="cancel-finish-race",
        execution_id=execution.execution_id,
        expected_version=running.status_version,
        kind=CommandKind.CANCEL,
        target=ExecutionStatus.CANCELLING,
        payload={"reason_code": "cancel.user"},
        actor={"surface": "test"},
        reason_code="cancel.user",
    )
    release.set()
    worker.join(3)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = store.get_execution(execution.execution_id)
        if current is not None and current.status is ExecutionStatus.CANCELLED:
            break
        time.sleep(0.02)
    current = store.get_execution(execution.execution_id)
    assert current is not None
    assert current.status is ExecutionStatus.CANCELLED
    assert current.reason_code == "cancel.user"
    command = store.get_command("cancel-finish-race")
    assert command is not None
    assert command.status is CommandStatus.APPLIED
    assert calls >= 2
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and driver._pending_finishes:
        time.sleep(0.01)
    assert driver._pending_finishes == {}


def test_finish_repair_intent_replays_after_driver_restart(tmp_path):
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry

    store, execution = _admitted(tmp_path, execution_id="exec-finish-replay")
    attempts = AttemptStore(store)
    attempt, leased = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="agent-owner",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=leased.status_version,
    )
    store.upsert_finish_repair(
        execution_id=execution.execution_id,
        attempt_id=active.attempt_id,
        generation=active.generation,
        expected_version=running.status_version,
        target=ExecutionStatus.COMPLETED.value,
        outcome="completed",
        reason_code=None,
    )

    # A fresh process's startup control service replays the durable repair
    # intent before handling any new turn.
    driver = AgentProductionDriver(
        executions=store,
        input_resolver=lambda _record: {},
        turn_runner=lambda **_kwargs: None,
    )
    service = RuntimeControlService(store, attempts, DriverRegistry())
    assert driver._pending_finishes == {}
    assert service.replay_finish_repairs() == 1
    current = store.get_execution(execution.execution_id)
    assert current is not None
    assert current.status is ExecutionStatus.COMPLETED
    assert store.list_finish_repairs() == []


def test_finish_repair_replay_binds_current_cancel_command(tmp_path):
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry

    store, execution = _admitted(tmp_path, execution_id="exec-finish-cancel-replay")
    attempts = AttemptStore(store)
    attempt, leased = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="agent-owner",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=leased.status_version,
    )
    _command, cancelling, duplicate = store.accept_command_with_transition(
        command_id="cancel-replay",
        execution_id=execution.execution_id,
        expected_version=running.status_version,
        kind=CommandKind.CANCEL,
        target=ExecutionStatus.CANCELLING,
        payload={"reason_code": "cancel.user"},
        actor={"surface": "test"},
        reason_code="cancel.user",
    )
    assert not duplicate
    store.upsert_finish_repair(
        execution_id=execution.execution_id,
        attempt_id=active.attempt_id,
        generation=active.generation,
        expected_version=cancelling.status_version,
        target=ExecutionStatus.COMPLETED.value,
        outcome="completed",
        reason_code=None,
    )

    service = RuntimeControlService(store, attempts, DriverRegistry())
    assert service.replay_finish_repairs() == 1
    current = store.get_execution(execution.execution_id)
    command = store.get_command("cancel-replay")
    assert current is not None and current.status is ExecutionStatus.CANCELLED
    assert command is not None and command.status is CommandStatus.APPLIED
    assert store.list_finish_repairs() == []


def test_finish_repair_capacity_preserves_actionable_rows(tmp_path, monkeypatch):
    from openprogram.execution.store import ExecutionConflict

    monkeypatch.setattr("openprogram.execution.store._FINISH_REPAIR_HIGH_WATERMARK", 1)
    store, first = _admitted(tmp_path, execution_id="exec-repair-capacity-1")
    attempts = AttemptStore(store)
    first_attempt, first_leased = attempts.lease(
        first.execution_id,
        expected_version=first.status_version,
        owner_id="owner-1",
        ttl_seconds=30,
    )
    first_active, first_running = attempts.activate(
        first_attempt.attempt_id,
        generation=first_attempt.generation,
        expected_execution_version=first_leased.status_version,
    )
    store.upsert_finish_repair(
        execution_id=first.execution_id,
        attempt_id=first_active.attempt_id,
        generation=first_active.generation,
        expected_version=first_running.status_version,
        target=ExecutionStatus.COMPLETED.value,
        outcome="completed",
        reason_code=None,
    )
    with pytest.raises(ExecutionConflict) as rejected:
        store.admit_execution(
            execution_id="exec-repair-capacity-agent",
            run_id="run-repair-capacity-agent",
            session_id="session-repair-capacity-agent",
            revision_id=first.revision_id,
            input_ref="input:repair-capacity-agent",
            input_hash="input-hash-agent",
            entrypoint="openprogram.agent.dispatcher:process_user_turn",
            trusted_actor={"subject": "user-2"},
            config_snapshot_ref="config:repair-capacity-agent",
            agent_turn_payload={
                "version": 1,
                "kind": "chat",
                "request": {"user_text": "run", "agent_id": "default", "source": "test"},
            },
        )
    assert rejected.value.code == "finish_repair_capacity"
    revision = store.create_revision(
        revision_id="revision-repair-capacity-2", manifest={"entrypoint": "agent", "slot": 2}
    )
    second = store.admit_execution(
        execution_id="exec-repair-capacity-2",
        run_id="run-repair-capacity-2",
        session_id="session-repair-capacity-2",
        revision_id=revision.revision_id,
        input_ref="input:repair-capacity-2",
        input_hash="input-hash-2",
        entrypoint="openprogram.agent.dispatcher:process_user_turn",
        trusted_actor={"subject": "user-2"},
        config_snapshot_ref="config:repair-capacity-2",
        agent_turn_payload=None,
    )
    second_attempt, second_leased = attempts.lease(
        second.execution_id,
        expected_version=second.status_version,
        owner_id="owner-2",
        ttl_seconds=30,
    )
    second_active, second_running = attempts.activate(
        second_attempt.attempt_id,
        generation=second_attempt.generation,
        expected_execution_version=second_leased.status_version,
    )
    store.upsert_finish_repair(
        execution_id=second.execution_id,
        attempt_id=second_active.attempt_id,
        generation=second_active.generation,
        expected_version=second_running.status_version,
        target=ExecutionStatus.COMPLETED.value,
        outcome="completed",
        reason_code=None,
    )
    rows = store.list_finish_repairs(limit=2)
    assert {row["execution_id"] for row in rows} == {
        first.execution_id, second.execution_id,
    }
    attempts.finish(
        first_active.attempt_id,
        generation=first_active.generation,
        expected_execution_version=first_running.status_version,
        target=ExecutionStatus.COMPLETED,
        outcome="completed",
    )
    attempts.finish(
        second_active.attempt_id,
        generation=second_active.generation,
        expected_execution_version=second_running.status_version,
        target=ExecutionStatus.COMPLETED,
        outcome="completed",
    )
    recovered = store.admit_execution(
        execution_id="exec-repair-capacity-recovered",
        run_id="run-repair-capacity-recovered",
        session_id="session-repair-capacity-recovered",
        revision_id=first.revision_id,
        input_ref="input:repair-capacity-recovered",
        input_hash="input-hash-recovered",
        entrypoint="openprogram.agent.dispatcher:process_user_turn",
        trusted_actor={"subject": "user-3"},
        config_snapshot_ref="config:repair-capacity-recovered",
        agent_turn_payload={
            "version": 1,
            "kind": "chat",
            "request": {"user_text": "run", "agent_id": "default", "source": "test"},
        },
    )
    assert recovered.status is ExecutionStatus.QUEUED


def test_finish_repair_replay_processes_more_than_one_page(tmp_path):
    from openprogram.execution.attempts import AttemptConflict
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry

    store = ExecutionStore(tmp_path / "executions.sqlite3")
    revision = store.create_revision(
        revision_id="revision-many-repairs", manifest={"entrypoint": "agent"}
    )
    attempts = AttemptStore(store)
    blocked_attempt_ids = set()
    for index in range(260):
        execution = store.admit_execution(
            execution_id=f"exec-many-repairs-{index:03d}",
            run_id=f"run-many-repairs-{index:03d}",
            session_id=f"session-many-repairs-{index:03d}",
            revision_id=revision.revision_id,
            input_ref=f"input:many-repairs-{index}",
            input_hash=f"input-hash-{index}",
            entrypoint="openprogram.agent.dispatcher:process_user_turn",
            trusted_actor={"subject": "test"},
            config_snapshot_ref="config:many-repairs",
            agent_turn_payload={
                "version": 1,
                "kind": "chat",
                "request": {"user_text": "run", "agent_id": "default", "source": "test"},
            },
        )
        leased_attempt, leased_execution = attempts.lease(
            execution.execution_id,
            expected_version=execution.status_version,
            owner_id=f"owner-{index}",
            ttl_seconds=30,
        )
        active_attempt, running_execution = attempts.activate(
            leased_attempt.attempt_id,
            generation=leased_attempt.generation,
            expected_execution_version=leased_execution.status_version,
        )
        if index < 256:
            blocked_attempt_ids.add(active_attempt.attempt_id)
        store.upsert_finish_repair(
            execution_id=execution.execution_id,
            attempt_id=active_attempt.attempt_id,
            generation=active_attempt.generation,
            expected_version=running_execution.status_version,
            target=ExecutionStatus.COMPLETED.value,
            outcome="completed",
            reason_code=None,
        )
    service = RuntimeControlService(store, attempts, DriverRegistry())
    original_finish = service.finish_attempt

    def block_first_page(*args, **kwargs):
        if kwargs["attempt_id"] in blocked_attempt_ids:
            raise AttemptConflict("blocked", "test blocked head")
        return original_finish(*args, **kwargs)

    service.finish_attempt = block_first_page
    assert service.replay_finish_repairs() == 4
    assert len(store.list_finish_repairs()) == 256


def test_finish_repair_stalls_after_bounded_attempts_until_manual_reconcile(
    tmp_path, monkeypatch,
):
    from openprogram.agent.production_driver import AgentProductionDriver

    monkeypatch.setattr(
        "openprogram.agent.production_driver.FINISH_RETRY_LIMIT", 0,
    )
    store, execution = _admitted(tmp_path, execution_id="exec-repair-stalled")
    attempts = AttemptStore(store)
    attempt, leased = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="owner-stalled",
        ttl_seconds=30,
    )
    active, _running = attempts.activate(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=leased.status_version,
    )
    driver = AgentProductionDriver(
        executions=store,
        input_resolver=lambda _record: {},
        turn_runner=lambda **_kwargs: None,
    )
    service = driver._control_service()
    original_finish = service.finish_attempt

    def fail_finish(*_args, **_kwargs):
        raise OSError("persistent failure")

    service.finish_attempt = fail_finish
    driver._finish_attempt(
        active,
        type("Result", (), {"failed": False, "error": None})(),
        threading.Event(),
    )
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        rows = store.list_finish_repairs()
        if (
            rows
            and rows[0]["reason_code"] == "finish_repair_stalled"
            and driver._finish_retry_timer is not None
        ):
            break
        time.sleep(0.01)
    rows = store.list_finish_repairs()
    assert rows and rows[0]["reason_code"] == "finish_repair_stalled"
    assert driver._pending_finishes == {}
    assert driver._finish_retry_timer is not None
    driver._finish_retry_timer.cancel()
    service.finish_attempt = original_finish
    assert service.replay_finish_repairs(include_stalled=True) == 1
    assert store.get_execution(execution.execution_id).status is ExecutionStatus.COMPLETED
    assert store.list_finish_repairs() == []


def test_startup_resumes_admitted_agent_without_attempt(tmp_path):
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry

    store, execution = _admitted(tmp_path, execution_id="exec-unstarted-agent")
    service = RuntimeControlService(store, AttemptStore(store), DriverRegistry())

    recoveries = service.recover_startup()

    current = store.get_execution(execution.execution_id)
    assert current is not None
    assert current.status is ExecutionStatus.PAUSED
    assert current.reason_code == "restart_pending"
    assert [item.execution.execution_id for item in recoveries] == [execution.execution_id]


def test_startup_recovers_active_agent_owner_loss_retains_completion_repair_capacity(
    tmp_path,
):
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry

    store, execution = _admitted(tmp_path, execution_id="exec-active-owner-loss")
    attempts = AttemptStore(store)
    leased, leased_execution = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="crashed-agent",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=leased_execution.status_version,
    )
    service = RuntimeControlService(store, attempts, DriverRegistry())

    recoveries = service.recover_startup()

    current = store.get_execution(execution.execution_id)
    ended = attempts.get(active.attempt_id)
    assert current is not None
    assert current.status is ExecutionStatus.PAUSED
    assert current.reason_code == "restart_pending"
    assert current.current_attempt_id is None
    assert ended is not None and ended.status.value == "ended"
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_finish_repair_slots "
            "WHERE execution_id = ?",
            (execution.execution_id,),
        ).fetchone()[0] == 1
    assert [item.execution.execution_id for item in recoveries] == [execution.execution_id]


def test_startup_recovery_reloads_after_concurrent_transition_conflict(
    tmp_path, monkeypatch,
):
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry
    from openprogram.execution.store import ExecutionConflict

    store, execution = _admitted(tmp_path, execution_id="exec-recovery-race")
    service = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    original = store._transition_execution
    calls = 0

    def concurrent_transition(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ExecutionConflict("status_conflict", "another recovery won")
        return original(*args, **kwargs)

    # Recovery checks owner evidence and transitions in the same transaction.
    monkeypatch.setattr(store, "_transition_execution", concurrent_transition)
    recoveries = service.recover_startup()

    assert calls == 1
    current = store.get_execution(execution.execution_id)
    assert current is not None
    assert current.status is ExecutionStatus.QUEUED
    assert [item.execution.execution_id for item in recoveries] == [execution.execution_id]


def test_late_owner_loss_cannot_recover_a_new_attempt(tmp_path):
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry
    from openprogram.execution.attempts import AttemptConflict

    store, execution = _admitted(tmp_path)
    attempts = AttemptStore(store)
    attempt_a, _recovered_before_activation = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="owner-a",
        ttl_seconds=30,
    )
    service = RuntimeControlService(store, attempts, DriverRegistry())

    # A loses ownership before activation. Recovery keeps the queued
    # execution reusable, but clears A's exact lease.
    recovered = service.recover_owner_loss(
        execution.execution_id,
        attempt_id=attempt_a.attempt_id,
        generation=attempt_a.generation,
    )
    attempt_b, running = attempts.lease(
        execution.execution_id,
        expected_version=recovered.execution.status_version,
        owner_id="owner-b",
        ttl_seconds=30,
    )
    assert attempt_b.generation > attempt_a.generation
    before = store.get_execution(execution.execution_id)
    assert before is not None

    with pytest.raises(AttemptConflict) as stale:
        service.recover_owner_loss(
            execution.execution_id,
            attempt_id=attempt_a.attempt_id,
            generation=attempt_a.generation,
        )

    after = store.get_execution(execution.execution_id)
    assert stale.value.code == "stale_owner"
    assert after == before
    assert after.current_attempt_id == attempt_b.attempt_id
    assert after.status is ExecutionStatus.QUEUED


LONG_TURN_DECISIONS = 70


def _long_turn_snapshot(request):
    from openprogram.agent.continuation import runtime_contract_snapshot
    from openprogram.providers.types import Model

    return runtime_contract_snapshot(
        model=Model(
            id="fake", name="fake", api="openai-completions", provider="openai",
            base_url="https://example.invalid/v1",
        ),
        system_prompt="system",
        tools=[],
        request=request,
    )


def _native_observe_result(index) -> str:
    return json.dumps(
        {
            "frame_id": f"frame_{index}_a02ffb17",
            "url": "http://127.0.0.1:62147/page/1?acceptance=release",
            "origin": "http://127.0.0.1:62147",
            "title": "Resource test 1",
            "text": f"Counter: {index}\n" + ("visible-text " * 20),
            "aria_snapshot": "- document\n" + ("- button: Test note\n" * 12) + f"- counter: {index}\n" + ("x" * 900),
            "elements": [{"role": "button", "name": "Test note", "index": index}],
            "backend": "open_claude_chrome",
        },
        ensure_ascii=False,
    )


def _provider_tool_decision(hook, snapshot, index, *, result_text=None):
    tool_call_id = f"call-{index}"
    assistant = {
        "role": "assistant",
        "content": [{
            "type": "toolCall",
            "id": tool_call_id,
            "name": "web_use",
            "arguments": {"n": index},
        }],
        "api": "fake",
        "provider": "fake",
        "model": "fake",
        "timestamp": 1,
    }
    assert hook("provider.before", {
        "resolved_snapshot": snapshot,
        "context": {"messages": [f"round-{index}"]},
        "supports_idempotency_key": True,
    }) is False
    assert hook("provider.after", {
        "message": assistant,
        "provider_request_id": f"request-{index}",
        "usage": {},
        "tool_call_ids": [tool_call_id],
        "next_tool_index": 0,
    }) is False
    assert hook("tool.before", {
        "tool_call_id": tool_call_id,
        "arguments": {"n": index},
    }) is False
    assert hook("tool.after", {
        "tool_call_id": tool_call_id,
        "is_error": False,
        "result": {
            "role": "toolResult",
            "tool_call_id": tool_call_id,
            "content": [{"type": "text", "text": result_text if result_text is not None else f"clicked-{index}"}],
        },
        "tool_call_ids": [tool_call_id],
        "next_tool_index": 1,
    }) is False
    return tool_call_id


def _committed_tool_ids(store, execution_id):
    from openprogram.execution.effects import EffectStatus

    with sqlite3.connect(store.path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT action_id, status, metadata_json FROM effects "
            "WHERE execution_id = ? ORDER BY created_at, effect_id",
            (execution_id,),
        ).fetchall()
    ids = []
    for row in rows:
        metadata = json.loads(row["metadata_json"])
        if metadata.get("kind") != "tool.before":
            continue
        if row["status"] != EffectStatus.COMMITTED.value:
            continue
        ids.append(row["action_id"])
    return ids


def _prepare_long_turn(tmp_path, execution_id):
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry

    store, execution = _admitted(tmp_path, execution_id=execution_id)
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id=f"owner-{execution_id}",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    control = RuntimeControlService(store, attempts, DriverRegistry())
    request = TurnRequest(
        session_id=running.session_id,
        user_text="durable agent turn",
        agent_id="default",
        source="component",
        user_msg_id="user-anchor",
    )
    request._execution_revision_id = running.revision_id
    driver = AgentProductionDriver(store, control_service=control)
    hook = driver._safe_point_hook(active, request, threading.Event())
    snapshot = _long_turn_snapshot(request)
    return store, attempts, control, driver, request, snapshot, execution, active, hook


def test_long_turn_pause_continue_uses_current_decision_cursor(tmp_path):
    from openprogram.agent.continuation import AgentContinuation
    from openprogram.execution.checkpoints import ExecutionCheckpointStore
    from openprogram.execution.effects import EffectStatus
    from openprogram.execution.public import execution_snapshot

    store, attempts, control, driver, request, snapshot, execution, active, hook = (
        _prepare_long_turn(tmp_path, "exec-long-pause")
    )
    for index in range(LONG_TURN_DECISIONS):
        _provider_tool_decision(hook, snapshot, index)
    first_tools = _committed_tool_ids(store, execution.execution_id)
    assert len(first_tools) == LONG_TURN_DECISIONS
    assert len(set(first_tools)) == LONG_TURN_DECISIONS

    assert hook("provider.before", {
        "resolved_snapshot": snapshot,
        "context": {"messages": ["pause-1"]},
        "supports_idempotency_key": True,
    }) is False
    asyncio.run(control.request_pause(
        command_id="pause-long-1",
        execution_id=execution.execution_id,
        expected_version=store.get_execution(execution.execution_id).status_version,
        actor={"surface": "browser-resource"},
    ))
    assert hook("provider.after", {
        "message": {
            "role": "assistant", "content": [{"type": "text", "text": "paused-1"}],
            "api": "fake", "provider": "fake", "model": "fake",
            "timestamp": 1,
        },
        "provider_request_id": "request-pause-1",
        "usage": {},
        "tool_call_ids": [],
        "next_tool_index": 0,
    }) is True
    paused = store.get_execution(execution.execution_id)
    assert paused is not None and paused.status is ExecutionStatus.PAUSED
    assert paused.checkpoint_head_id is not None
    assert store.get_command("pause-long-1").status is CommandStatus.APPLIED
    assert _committed_tool_ids(store, execution.execution_id) == first_tools
    snapshot_one = execution_snapshot(paused, store=store)
    assert snapshot_one.can_continue is True
    unresolved = control.effects.list_unresolved(execution.execution_id)
    assert unresolved == []
    from openprogram.agent.continuation import AgentCheckpointV1
    from openprogram.execution.projections import ExecutionProjectionReadModel

    state = AgentCheckpointV1.load(store, ExecutionCheckpointStore(store).get(paused.checkpoint_head_id))
    assert "turn_display_ref" in state.payload
    display = state.read_json_ref(
        store, execution.execution_id, state.payload["turn_display_ref"],
    )
    assert display[0]["tool_call_id"] == "call-0"
    assert display[0]["result"] == "clicked-0"
    assert display[LONG_TURN_DECISIONS - 1]["tool_call_id"] == f"call-{LONG_TURN_DECISIONS - 1}"
    assert display[LONG_TURN_DECISIONS - 1]["result"] == f"clicked-{LONG_TURN_DECISIONS - 1}"
    assert len(state.payload["terminal_effect_receipts"]) <= 2
    projected = ExecutionProjectionReadModel(store)._checkpoint_blocks(
        paused, state.payload["turn"]["assistant_message_id"],
    )
    assert any(
        block.get("tool_call_id") == "call-0" and block.get("result") == "clicked-0"
        for block in projected
    )
    assert any(
        block.get("tool_call_id") == f"call-{LONG_TURN_DECISIONS - 1}"
        and block.get("result") == f"clicked-{LONG_TURN_DECISIONS - 1}"
        for block in projected
    )

    captured = {}

    async def activate(attempt, activation):
        captured["attempt"] = attempt
        captured["activation"] = activation

    continued = asyncio.run(control.request_continue(
        command_id="continue-long-1",
        execution_id=execution.execution_id,
        expected_version=paused.status_version,
        actor={"surface": "test"},
        activator=activate,
    ))
    assert continued.command.status is CommandStatus.APPLIED
    assert continued.execution.status is ExecutionStatus.RUNNING
    checkpoint = ExecutionCheckpointStore(store).get(paused.checkpoint_head_id)
    continuation = AgentContinuation.from_checkpoint(
        store=store, checkpoint=checkpoint, request=request,
    )
    assert len(continuation.state.payload["terminal_effect_receipts"]) <= 2
    hook2 = driver._safe_point_hook(
        captured["attempt"], request, threading.Event(), continuation=continuation,
    )
    for index in range(LONG_TURN_DECISIONS, LONG_TURN_DECISIONS * 2):
        _provider_tool_decision(hook2, snapshot, index)
    second_tools = _committed_tool_ids(store, execution.execution_id)
    assert second_tools[:LONG_TURN_DECISIONS] == first_tools
    assert len(second_tools) == LONG_TURN_DECISIONS * 2
    assert len(set(second_tools)) == LONG_TURN_DECISIONS * 2

    assert hook2("provider.before", {
        "resolved_snapshot": snapshot,
        "context": {"messages": ["pause-2"]},
        "supports_idempotency_key": True,
    }) is False
    asyncio.run(control.request_pause(
        command_id="pause-long-2",
        execution_id=execution.execution_id,
        expected_version=store.get_execution(execution.execution_id).status_version,
        actor={"surface": "browser-resource"},
    ))
    assert hook2("provider.after", {
        "message": {
            "role": "assistant", "content": [{"type": "text", "text": "paused-2"}],
            "api": "fake", "provider": "fake", "model": "fake",
            "timestamp": 1,
        },
        "provider_request_id": "request-pause-2",
        "usage": {},
        "tool_call_ids": [],
        "next_tool_index": 0,
    }) is True
    paused_again = store.get_execution(execution.execution_id)
    assert paused_again is not None and paused_again.status is ExecutionStatus.PAUSED
    assert paused_again.checkpoint_head_id is not None
    assert paused_again.checkpoint_head_id != paused.checkpoint_head_id
    assert store.get_command("pause-long-2").status is CommandStatus.APPLIED
    assert _committed_tool_ids(store, execution.execution_id) == second_tools
    second_state = AgentCheckpointV1.load(
        store, ExecutionCheckpointStore(store).get(paused_again.checkpoint_head_id),
    )
    second_display = second_state.read_json_ref(
        store, execution.execution_id, second_state.payload["turn_display_ref"],
    )
    assert any(
        block.get("tool_call_id") == "call-0" and block.get("result") == "clicked-0"
        for block in second_display
    )
    assert any(
        block.get("tool_call_id") == f"call-{LONG_TURN_DECISIONS}"
        and block.get("result") == f"clicked-{LONG_TURN_DECISIONS}"
        for block in second_display
    )
    snapshot_two = execution_snapshot(paused_again, store=store)
    assert snapshot_two.can_continue is True

    async def activate_again(attempt, activation):
        captured["second_activation"] = activation

    resumed = asyncio.run(control.request_continue(
        command_id="continue-long-2",
        execution_id=execution.execution_id,
        expected_version=paused_again.status_version,
        actor={"surface": "test"},
        activator=activate_again,
    ))
    assert resumed.command.status is CommandStatus.APPLIED
    assert resumed.execution.status is ExecutionStatus.RUNNING
    assert _committed_tool_ids(store, execution.execution_id) == second_tools


def test_pause_during_dispatched_nonrepeatable_tool_stays_reconciliation(tmp_path):
    from openprogram.execution.effects import EffectStatus

    store, attempts, control, driver, request, snapshot, execution, active, hook = (
        _prepare_long_turn(tmp_path, "exec-uncertain-tool")
    )
    _provider_tool_decision(hook, snapshot, 0)
    assert hook("provider.before", {
        "resolved_snapshot": snapshot,
        "context": {"messages": ["in-flight-tool"]},
        "supports_idempotency_key": True,
    }) is False
    assert hook("provider.after", {
        "message": {
            "role": "assistant",
            "content": [{
                "type": "toolCall", "id": "call-inflight",
                "name": "web_use", "arguments": {"n": "inflight"},
            }],
            "api": "fake", "provider": "fake", "model": "fake",
        },
        "provider_request_id": "request-inflight",
        "usage": {},
        "tool_call_ids": ["call-inflight"],
        "next_tool_index": 0,
    }) is False
    assert hook("tool.before", {
        "tool_call_id": "call-inflight",
        "arguments": {"n": "inflight"},
    }) is False
    unresolved = control.effects.list_unresolved(execution.execution_id)
    assert len(unresolved) == 1
    assert unresolved[0].metadata.get("kind") == "tool.before"
    assert unresolved[0].status is EffectStatus.DISPATCHED
    pausing = asyncio.run(control.request_pause(
        command_id="pause-uncertain-tool",
        execution_id=execution.execution_id,
        expected_version=store.get_execution(execution.execution_id).status_version,
        actor={"surface": "test"},
    ))
    finished = control.finish_attempt(
        attempt_id=active.attempt_id,
        generation=active.generation,
        expected_execution_version=pausing.execution.status_version,
        target=ExecutionStatus.COMPLETED,
        outcome="completed",
        command_id="pause-uncertain-tool",
    )
    assert finished.execution.status is ExecutionStatus.RECONCILIATION_REQUIRED
    assert finished.command is not None
    assert finished.command.status is CommandStatus.APPLYING
    still = control.effects.get(unresolved[0].effect_id)
    assert still is not None and still.status is EffectStatus.DISPATCHED


def test_cancel_during_dispatched_nonrepeatable_tool_stays_reconciliation(tmp_path):
    from openprogram.execution.effects import EffectStatus

    store, attempts, control, driver, request, snapshot, execution, active, hook = (
        _prepare_long_turn(tmp_path, "exec-cancel-tool")
    )
    assert hook("provider.before", {
        "resolved_snapshot": snapshot,
        "context": {"messages": ["cancel-tool"]},
        "supports_idempotency_key": True,
    }) is False
    assert hook("provider.after", {
        "message": {
            "role": "assistant",
            "content": [{
                "type": "toolCall", "id": "call-cancel",
                "name": "web_use", "arguments": {"n": "cancel"},
            }],
            "api": "fake", "provider": "fake", "model": "fake",
        },
        "provider_request_id": "request-cancel",
        "usage": {},
        "tool_call_ids": ["call-cancel"],
        "next_tool_index": 0,
    }) is False
    assert hook("tool.before", {
        "tool_call_id": "call-cancel",
        "arguments": {"n": "cancel"},
    }) is False
    unresolved = control.effects.list_unresolved(execution.execution_id)
    assert unresolved and unresolved[0].status is EffectStatus.DISPATCHED
    cancelling = asyncio.run(control.request_cancel(
        command_id="cancel-uncertain-tool",
        execution_id=execution.execution_id,
        expected_version=store.get_execution(execution.execution_id).status_version,
        actor={"surface": "test"},
        reason_code="user_cancelled",
    ))
    finished = control.finish_attempt(
        attempt_id=active.attempt_id,
        generation=active.generation,
        expected_execution_version=cancelling.execution.status_version,
        target=ExecutionStatus.CANCELLED,
        outcome="cooperative_cancel",
        command_id="cancel-uncertain-tool",
        reason_code="user_cancelled",
    )
    assert finished.execution.status is ExecutionStatus.RECONCILIATION_REQUIRED
    assert finished.command is not None
    assert finished.command.status is CommandStatus.APPLYING
    still = control.effects.get(unresolved[0].effect_id)
    assert still is not None and still.status is EffectStatus.DISPATCHED


def test_legacy_checkpoint_continue_keeps_completed_tool_history(tmp_path):
    from openprogram.agent.continuation import (
        AgentCheckpointV1,
        AgentContinuation,
        canonical_json_bytes,
        decode_turn_display,
    )
    from openprogram.execution.checkpoints import ExecutionCheckpointStore
    from openprogram.execution.effects import (
        EffectClassification,
        EffectStatus,
        EffectStore,
    )
    from openprogram.execution.projections import ExecutionProjectionReadModel
    from openprogram.providers.types import (
        AssistantMessage,
        TextContent,
        ToolCall,
        ToolResultMessage,
    )

    store, _attempts, control, driver, request, snapshot, execution, active, _hook = (
        _prepare_long_turn(tmp_path, "exec-legacy-display")
    )
    decision = AssistantMessage(
        content=[
            ToolCall(id="call-finished", name="web_use", arguments={"n": "old"}),
            ToolCall(id="call-pending", name="web_use", arguments={"n": "suffix"}),
        ],
        api="fake", provider="fake", model="fake", timestamp=1, stop_reason="toolUse",
    )
    finished_result = ToolResultMessage(
        tool_call_id="call-finished", tool_name="web_use",
        content=[TextContent(text="second:ok")], timestamp=1,
    )
    effects = EffectStore(store)
    provider = effects.register(
        effect_id="effect_legacy_provider",
        execution_id=execution.execution_id,
        attempt_id=active.attempt_id,
        action_id="provider-legacy",
        classification=EffectClassification.IDEMPOTENT,
        idempotency_key="provider-legacy",
        metadata={"kind": "provider.before"},
    )
    effects.mark_dispatched(provider.effect_id, expected_status=EffectStatus.PLANNED)
    effects.resolve(
        provider.effect_id, expected_status=EffectStatus.DISPATCHED,
        outcome=EffectStatus.COMMITTED,
        receipt={"provider_request_id": "legacy-provider"},
        attempt_id=active.attempt_id, generation=active.generation,
    )
    tool_effect = effects.register(
        effect_id="effect_legacy_tool",
        execution_id=execution.execution_id,
        attempt_id=active.attempt_id,
        action_id="tool-finished-legacy",
        classification=EffectClassification.NONREPEATABLE,
        idempotency_key=None,
        metadata={"kind": "tool.before"},
    )
    effects.mark_dispatched(tool_effect.effect_id, expected_status=EffectStatus.PLANNED)
    pausing = asyncio.run(control.request_pause(
        command_id="pause-legacy-display",
        execution_id=execution.execution_id,
        expected_version=store.get_execution(execution.execution_id).status_version,
        actor={"surface": "test"},
    ))
    terminal_receipt = {
        "tool_call_id": "call-finished", "is_error": False, "result_hash": "legacy",
    }
    legacy = AgentCheckpointV1.build(
        safe_point={
            "kind": "agent.tool.action.after",
            "step_id": "after_tool:tool-finished-legacy",
            "phase": "after_tool",
            "sentinel": "resume-from-checkpoint",
        },
        frontier=[{
            "step_id": "after_tool:tool-finished-legacy",
            "phase": "after_tool",
            "branch_id": "main",
        }],
        turn={
            "user_message_id": "user-anchor",
            "assistant_message_id": "user-anchor_reply",
            "base_history_head_id": "user-anchor",
        },
        assistant_message=decision.model_dump(mode="json"),
        tool_results=[finished_result.model_dump(mode="json")],
        resolved_snapshot=snapshot,
        provider_action_id="provider-legacy",
        tool_call_ids=["call-finished", "call-pending"],
        next_tool_index=1,
        repeat_failures={},
        completed_actions=[
            {
                "action_id": "provider-legacy",
                "input_hash": "legacy-context",
                "result": decision.model_dump(mode="json"),
            },
            {
                "action_id": "tool-finished-legacy",
                "input_hash": "legacy-tool",
                "result": finished_result.model_dump(mode="json"),
            },
        ],
        terminal_effect_receipts=[
            {
                "effect_id": "effect_legacy_provider",
                "frontier_step_id": "after_provider:provider-legacy",
                "action_id": "provider-legacy",
                "outcome": "committed",
                "receipt": {"provider_request_id": "legacy-provider"},
            },
            {
                "effect_id": "effect_legacy_tool",
                "frontier_step_id": "after_tool:tool-finished-legacy",
                "action_id": "tool-finished-legacy",
                "outcome": "committed",
                "receipt": dict(terminal_receipt),
            },
        ],
    )
    assert "turn_display_ref" not in legacy.payload
    control.commit_agent_safe_point(
        execution_id=execution.execution_id,
        attempt_id=active.attempt_id,
        generation=active.generation,
        expected_version=pausing.execution.status_version,
        safe_point_kind="agent.tool.action.after",
        frontier=tuple(legacy.payload["frontier"]),
        state_refs={},
        effect_id=tool_effect.effect_id,
        terminal_receipt=terminal_receipt,
        receipt_blob=canonical_json_bytes(terminal_receipt),
        agent_checkpoint=legacy,
        command_id="pause-legacy-display",
        managed_action_id="tool-finished-legacy",
    )
    paused = store.get_execution(execution.execution_id)
    assert paused is not None and paused.status is ExecutionStatus.PAUSED
    seeded = decode_turn_display(
        AgentCheckpointV1.load(store, ExecutionCheckpointStore(store).get(paused.checkpoint_head_id)),
        store=store,
        execution_id=execution.execution_id,
    )
    finished_card = next(block for block in seeded if block.get("tool_call_id") == "call-finished")
    pending_card = next(block for block in seeded if block.get("tool_call_id") == "call-pending")
    assert finished_card.get("result") == "second:ok"
    assert "result" not in pending_card
    assert "declined" not in str(pending_card)
    assert "expired" not in str(pending_card)

    captured = {}

    async def activate(attempt, activation):
        captured["attempt"] = attempt
        captured["activation"] = activation

    continued = asyncio.run(control.request_continue(
        command_id="continue-legacy-display",
        execution_id=execution.execution_id,
        expected_version=paused.status_version,
        actor={"surface": "test"},
        activator=activate,
    ))
    assert continued.command.status is CommandStatus.APPLIED
    continuation = AgentContinuation.from_checkpoint(
        store=store, checkpoint=captured["activation"].checkpoint, request=request,
    )
    assert "turn_display_ref" not in continuation.state.payload
    hook = driver._safe_point_hook(
        captured["attempt"], request, threading.Event(), continuation=continuation,
    )
    assert hook("tool.before", {
        "tool_call_id": "call-pending",
        "arguments": {"n": "suffix"},
    }) is False
    assert hook("tool.after", {
        "tool_call_id": "call-pending",
        "is_error": False,
        "result": {
            "role": "toolResult",
            "tool_call_id": "call-pending",
            "content": [{"type": "text", "text": "suffix-ok"}],
        },
        "tool_call_ids": ["call-finished", "call-pending"],
        "next_tool_index": 2,
    }) is False
    _provider_tool_decision(hook, snapshot, "new")
    assert hook("provider.before", {
        "resolved_snapshot": snapshot,
        "context": {"messages": ["legacy-pause"]},
        "supports_idempotency_key": True,
    }) is False
    asyncio.run(control.request_pause(
        command_id="pause-after-legacy-continue",
        execution_id=execution.execution_id,
        expected_version=store.get_execution(execution.execution_id).status_version,
        actor={"surface": "test"},
    ))
    assert hook("provider.after", {
        "message": {
            "role": "assistant", "content": [{"type": "text", "text": "done"}],
            "api": "fake", "provider": "fake", "model": "fake", "timestamp": 1,
        },
        "provider_request_id": "after-legacy",
        "usage": {},
        "tool_call_ids": [],
        "next_tool_index": 0,
    }) is True
    paused_again = store.get_execution(execution.execution_id)
    assert paused_again is not None and paused_again.status is ExecutionStatus.PAUSED
    new_state = AgentCheckpointV1.load(
        store, ExecutionCheckpointStore(store).get(paused_again.checkpoint_head_id),
    )
    assert "turn_display_ref" in new_state.payload
    display = new_state.read_json_ref(
        store, execution.execution_id, new_state.payload["turn_display_ref"],
    )
    assert any(
        block.get("tool_call_id") == "call-finished" and block.get("result") == "second:ok"
        for block in display
    )
    assert any(
        block.get("tool_call_id") == "call-pending" and block.get("result") == "suffix-ok"
        for block in display
    )
    assert any(
        block.get("tool_call_id") == "call-new" and block.get("result") == "clicked-new"
        for block in display
    )
    projected = ExecutionProjectionReadModel(store)._checkpoint_blocks(
        paused_again, new_state.payload["turn"]["assistant_message_id"],
    )
    assert any(
        block.get("tool_call_id") == "call-finished" and block.get("result") == "second:ok"
        for block in projected
    )
    assert any(
        block.get("tool_call_id") == "call-new" and block.get("result") == "clicked-new"
        for block in projected
    )
    tool_ids = _committed_tool_ids(store, execution.execution_id)
    assert tool_ids.count("tool-finished-legacy") == 1
    assert len(tool_ids) == len(set(tool_ids))


NATIVE_DISPLAY_ROUNDS = 50


def test_native_sized_web_use_display_pause_continue(tmp_path):
    from openprogram.agent.continuation import (
        MAX_AGENT_DELTA_BYTES,
        AgentCheckpointV1,
        AgentContinuation,
    )
    from openprogram.execution.checkpoints import ExecutionCheckpointStore
    from openprogram.execution.public import execution_snapshot

    sample = [
        {
            "type": "tool",
            "tool": "web_use",
            "tool_call_id": f"call-{index}",
            "result": _native_observe_result(index),
        }
        for index in range(NATIVE_DISPLAY_ROUNDS)
    ]
    assert len(json.dumps(sample, ensure_ascii=False).encode("utf-8")) > MAX_AGENT_DELTA_BYTES

    store, _attempts, control, driver, request, snapshot, execution, active, hook = (
        _prepare_long_turn(tmp_path, "exec-native-display")
    )
    for index in range(NATIVE_DISPLAY_ROUNDS):
        _provider_tool_decision(
            hook, snapshot, index, result_text=_native_observe_result(index),
        )
    assert hook("provider.before", {
        "resolved_snapshot": snapshot,
        "context": {"messages": ["native-pause-1"]},
        "supports_idempotency_key": True,
    }) is False
    asyncio.run(control.request_pause(
        command_id="pause-native-display-1",
        execution_id=execution.execution_id,
        expected_version=store.get_execution(execution.execution_id).status_version,
        actor={"surface": "browser-resource"},
    ))
    assert hook("provider.after", {
        "message": {
            "role": "assistant", "content": [{"type": "text", "text": "paused-native-1"}],
            "api": "fake", "provider": "fake", "model": "fake", "timestamp": 1,
        },
        "provider_request_id": "native-pause-1",
        "usage": {},
        "tool_call_ids": [],
        "next_tool_index": 0,
    }) is True
    paused = store.get_execution(execution.execution_id)
    assert paused is not None and paused.status is ExecutionStatus.PAUSED
    assert paused.checkpoint_head_id is not None
    assert store.get_command("pause-native-display-1").status is CommandStatus.APPLIED
    assert execution_snapshot(paused, store=store).can_continue is True
    state = AgentCheckpointV1.load(
        store, ExecutionCheckpointStore(store).get(paused.checkpoint_head_id),
    )
    display = state.read_json_ref(store, execution.execution_id, state.payload["turn_display_ref"])
    assert display[0]["tool_call_id"] == "call-0"
    assert f"Counter: 0" in display[0]["result"]
    assert display[NATIVE_DISPLAY_ROUNDS - 1]["tool_call_id"] == f"call-{NATIVE_DISPLAY_ROUNDS - 1}"
    first_tools = _committed_tool_ids(store, execution.execution_id)
    assert len(first_tools) == NATIVE_DISPLAY_ROUNDS

    captured = {}

    async def activate(attempt, activation):
        captured["attempt"] = attempt
        captured["activation"] = activation

    continued = asyncio.run(control.request_continue(
        command_id="continue-native-display-1",
        execution_id=execution.execution_id,
        expected_version=paused.status_version,
        actor={"surface": "test"},
        activator=activate,
    ))
    assert continued.command.status is CommandStatus.APPLIED
    continuation = AgentContinuation.from_checkpoint(
        store=store, checkpoint=captured["activation"].checkpoint, request=request,
    )
    hook2 = driver._safe_point_hook(
        captured["attempt"], request, threading.Event(), continuation=continuation,
    )
    second_start = NATIVE_DISPLAY_ROUNDS
    second_end = NATIVE_DISPLAY_ROUNDS + 20
    for index in range(second_start, second_end):
        _provider_tool_decision(
            hook2, snapshot, index, result_text=_native_observe_result(index),
        )
    assert hook2("provider.before", {
        "resolved_snapshot": snapshot,
        "context": {"messages": ["native-pause-2"]},
        "supports_idempotency_key": True,
    }) is False
    asyncio.run(control.request_pause(
        command_id="pause-native-display-2",
        execution_id=execution.execution_id,
        expected_version=store.get_execution(execution.execution_id).status_version,
        actor={"surface": "test"},
    ))
    assert hook2("provider.after", {
        "message": {
            "role": "assistant", "content": [{"type": "text", "text": "paused-native-2"}],
            "api": "fake", "provider": "fake", "model": "fake", "timestamp": 1,
        },
        "provider_request_id": "native-pause-2",
        "usage": {},
        "tool_call_ids": [],
        "next_tool_index": 0,
    }) is True
    paused_again = store.get_execution(execution.execution_id)
    assert paused_again is not None and paused_again.status is ExecutionStatus.PAUSED
    second_state = AgentCheckpointV1.load(
        store, ExecutionCheckpointStore(store).get(paused_again.checkpoint_head_id),
    )
    second_display = second_state.read_json_ref(
        store, execution.execution_id, second_state.payload["turn_display_ref"],
    )
    assert any(block.get("tool_call_id") == "call-0" and "Counter: 0" in block.get("result", "") for block in second_display)
    assert any(
        block.get("tool_call_id") == f"call-{second_start}"
        and f"Counter: {second_start}" in block.get("result", "")
        for block in second_display
    )
    second_tools = _committed_tool_ids(store, execution.execution_id)
    assert second_tools[:NATIVE_DISPLAY_ROUNDS] == first_tools
    assert len(set(second_tools)) == len(second_tools)
    resumed = asyncio.run(control.request_continue(
        command_id="continue-native-display-2",
        execution_id=execution.execution_id,
        expected_version=paused_again.status_version,
        actor={"surface": "test"},
        activator=activate,
    ))
    assert resumed.command.status is CommandStatus.APPLIED
    assert _committed_tool_ids(store, execution.execution_id) == second_tools


def _public_display_checkpoint(
    tmp_path,
    *,
    turn_display,
    receipt_count=1,
    pending_messages=None,
    execution_id="exec-display-cap",
):
    from openprogram.agent.continuation import AgentCheckpointV1
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.providers.types import AssistantMessage, TextContent

    store, execution = _admitted(tmp_path, execution_id=execution_id)
    request = TurnRequest(
        session_id=execution.session_id,
        user_text="durable agent turn",
        agent_id="default",
        source="component",
        user_msg_id="user-anchor",
    )
    request._execution_revision_id = execution.revision_id
    snapshot = _long_turn_snapshot(request)
    decision = AssistantMessage(
        content=[TextContent(text="saved")],
        api="fake", provider="fake", model="fake", timestamp=1, stop_reason="stop",
    )
    assistant_dump = decision.model_dump(mode="json")
    completed_actions = [{"action_id": "provider-action", "input_hash": "context-hash"}]
    receipts = [{
        "effect_id": "effect-provider", "frontier_step_id": "provider:p",
        "action_id": "provider-action", "outcome": "committed",
        "receipt": {"provider_request_id": "saved-request"},
    }]
    while len(receipts) < receipt_count:
        index = len(receipts)
        action_id = f"history-action-{index}"
        completed_actions.append({
            "action_id": action_id,
            "input_hash": f"history-hash-{index}",
            "result": assistant_dump,
        })
        receipts.append({
            "effect_id": f"effect-history-{index}",
            "frontier_step_id": f"after:{index}",
            "action_id": action_id,
            "outcome": "committed",
            "receipt": {"n": index},
        })
    state = AgentCheckpointV1.build(
        safe_point={
            "kind": "agent.provider.decision.after",
            "step_id": "after_provider:p",
            "phase": "after_provider",
            "sentinel": "resume-from-checkpoint",
        },
        frontier=[{"step_id": "after_provider:p", "phase": "after_provider", "branch_id": "main"}],
        turn={
            "user_message_id": "user-anchor",
            "assistant_message_id": "user-anchor_reply",
            "base_history_head_id": "user-anchor",
        },
        assistant_message=assistant_dump,
        tool_results=[],
        resolved_snapshot=snapshot,
        provider_action_id="provider-action",
        tool_call_ids=[],
        next_tool_index=0,
        repeat_failures={},
        completed_actions=completed_actions,
        terminal_effect_receipts=receipts,
        pending_messages=pending_messages,
        turn_display=turn_display,
    )
    for raw in state.blob_payloads.values():
        store.put_state_blob(execution.execution_id, raw)
    return store, execution, state


def test_display_ref_overflow_keeps_fitting_pages_in_temp_store(tmp_path):
    from openprogram.agent.continuation import (
        MAX_AGENT_STATE_REFS,
        decode_turn_display,
    )

    display = [
        {
            "type": "tool",
            "tool": "web_use",
            "tool_call_id": f"call-{index}",
            "result": f"{index}:" + ("P" * (600 * 1024)),
        }
        for index in range(30)
    ]
    store, execution, state = _public_display_checkpoint(
        tmp_path, turn_display=display, execution_id="exec-display-overflow",
    )
    state.validate()
    assert len(state.payload["turn_display_refs"]) == 29
    assert len(state.payload["state_refs"]) == MAX_AGENT_STATE_REFS
    decoded = decode_turn_display(
        state, store=store, execution_id=execution.execution_id,
    )
    assert decoded[0]["tool_call_id"] == "call-0"
    assert decoded[-1]["tool_call_id"] == "call-28"
    assert not any(block.get("tool_call_id") == "call-29" for block in decoded)


def test_shared_pending_display_digest_survives_ref_budget_in_temp_store(tmp_path):
    from openprogram.agent.continuation import (
        MAX_AGENT_STATE_REFS,
        decode_turn_display,
    )

    display = [{"type": "text", "text": "kept-card"}]
    store, execution, state = _public_display_checkpoint(
        tmp_path,
        turn_display=display,
        receipt_count=29,
        pending_messages=[{
            "message_id": "pending-1",
            "sequence": 0,
            "input_hash": "pending-hash",
            "status": "pending",
            "content": display,
        }],
        execution_id="exec-display-alias",
    )
    state.validate()
    payload = state.payload
    assert len(payload["state_refs"]) == MAX_AGENT_STATE_REFS
    pending_ref = payload["pending_messages"][0]["content_ref"]
    blob = store.get_state_blob(execution.execution_id, pending_ref["ref"])
    assert blob is not None
    assert json.loads(blob["payload"].decode("utf-8")) == display
    assert "turn_display_ref" not in payload
    assert "turn_display" not in payload["state_refs"]
    decoded = decode_turn_display(
        state, store=store, execution_id=execution.execution_id,
    )
    assert not any(block.get("text") == "kept-card" for block in decoded)


def test_paged_turn_display_decode_uses_temp_store(tmp_path):
    from openprogram.agent.continuation import (
        MAX_AGENT_STATE_BLOB_BYTES,
        decode_turn_display,
    )

    huge = "H" * (MAX_AGENT_STATE_BLOB_BYTES - 32)
    display = [
        {"type": "tool", "tool": "web_use", "tool_call_id": "call-huge", "result": huge},
        {"type": "tool", "tool": "web_use", "tool_call_id": "call-small", "result": "small"},
        {
            "type": "tool",
            "tool": "web_use",
            "tool_call_id": "call-a",
            "result": "A:" + ("A" * (600 * 1024)),
        },
        {
            "type": "tool",
            "tool": "web_use",
            "tool_call_id": "call-b",
            "result": "B:" + ("B" * (600 * 1024)),
        },
    ]
    store, execution, state = _public_display_checkpoint(
        tmp_path, turn_display=display, execution_id="exec-display-pages",
    )
    state.validate()
    page_refs = state.payload["turn_display_refs"]
    assert len(page_refs) >= 2
    assert all(
        state.payload["state_refs"][f"turn_display.{index}"] == descriptor
        for index, descriptor in enumerate(page_refs)
    )
    assert "turn_display_result.0" in state.payload["state_refs"]
    decoded = decode_turn_display(
        state, store=store, execution_id=execution.execution_id,
    )
    assert [block["tool_call_id"] for block in decoded] == [
        "call-huge", "call-small", "call-a", "call-b",
    ]
    assert decoded[0]["result"] == huge
    assert "result_ref" not in decoded[0]
    assert decoded[1]["result"] == "small"
    assert decoded[2]["result"].startswith("A:")
    assert decoded[3]["result"].startswith("B:")


def test_iteration_exhaustion_finishes_canonical_execution_as_failed(tmp_path):
    from openprogram.agent.agent_loop import agent_loop
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.agent.types import AgentContext, AgentLoopConfig, AgentTool, AgentToolResult
    from openprogram.providers.types import AssistantMessage, EventDone, Model, TextContent, ToolCall, UserMessage

    store, execution = _admitted(tmp_path)
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(execution.execution_id,
        expected_version=execution.status_version, owner_id="limit-owner", ttl_seconds=30)
    active, _running = attempts.activate(leased.attempt_id,
        generation=leased.generation, expected_execution_version=reserved.status_version)

    def run_turn(**kwargs):
        async def run():
            message = AssistantMessage(content=[ToolCall(id="again", name="again", arguments={})],
                api="fake", provider="fake", model="fake", stop_reason="toolUse", timestamp=1)
            async def stream(*_):
                yield EventDone(reason="toolUse", message=message)
            async def execute(*_):
                return AgentToolResult(content=[TextContent(text="pending")])
            tool = AgentTool(name="again", label="again", description="again",
                parameters={"type": "object", "properties": {}}, execute=execute)
            events = agent_loop([UserMessage(content="continue", timestamp=0)],
                AgentContext(tools=[tool]), AgentLoopConfig(
                    model=Model(id="fake", name="fake", api="fake", provider="fake", base_url="https://example.invalid"),
                    convert_to_llm=lambda messages: messages, max_iterations=1),
                stream_fn=stream)
            return await events.result()
        return asyncio.run(run())

    driver = AgentProductionDriver(store, turn_runner=run_turn)
    result = asyncio.run(driver._run_attempt(active,
        TurnRequest(session_id=execution.session_id, agent_id="default",
            user_text="continue", source="component"), threading.Event()))
    assert result.failed
    assert "iteration limit" in result.error
    assert store.get_execution(execution.execution_id).status is ExecutionStatus.FAILED


def test_function_suspension_retains_pending_agent_tool_slot(tmp_path, monkeypatch):
    import importlib
    function_module = importlib.import_module("openprogram.agentic_programming.function")
    monkeypatch.setattr(function_module, "_registry", dict(function_module._registry))
    from openprogram.agent.continuation import AgentContinuation
    from openprogram.execution.checkpoints import ExecutionCheckpointStore

    store, attempts, control, driver, request, snapshot, execution, active, hook = _prepare_long_turn(tmp_path, "function-slot")
    hook("provider.before", {"resolved_snapshot": snapshot, "context": {"messages": []}})
    hook("provider.after", {
        "message": {"role": "assistant", "content": [{"type": "toolCall", "id": "function-call", "name": "web_use", "arguments": {}}], "api": "fake", "provider": "fake", "model": "fake", "timestamp": 1},
        "tool_call_ids": ["function-call"], "next_tool_index": 0,
    })
    hook("tool.before", {"tool_call_id": "function-call", "tool_name": "web_use", "arguments": {}})
    from openprogram.agentic_programming.function import agentic_function
    from openprogram.agentic_programming.continuation import function_execution

    def completed_function():
        return "saved result"

    durable = agentic_function(completed_function, name="web_use", as_tool=False, resumable=True)
    with function_execution(store, attempt_id=active.attempt_id, generation=active.generation, call_key="function-call", checkpoint_root=False):
        durable()
    asyncio.run(control.request_pause(command_id="pause-function", execution_id=execution.execution_id, expected_version=store.get_execution(execution.execution_id).status_version, actor={"surface": "test"}))
    assert hook("tool.suspended", {"tool_call_id": "function-call", "tool_name": "web_use", "next_tool_index": 0, "tool_call_ids": ["function-call"]}) is True
    paused = store.get_execution(execution.execution_id)
    assert paused.status is ExecutionStatus.PAUSED
    checkpoint = ExecutionCheckpointStore(store).get(paused.checkpoint_head_id)
    continuation = AgentContinuation.from_checkpoint(store=store, checkpoint=checkpoint, request=request)
    assert continuation.tool_results == ()
    assert continuation.next_tool_index == 0
    captured = {}

    async def activate(attempt, activation):
        captured["attempt"] = attempt

    asyncio.run(control.request_continue(command_id="resume-function", execution_id=execution.execution_id, expected_version=paused.status_version, actor={"surface": "test"}, activator=activate))
    resumed_hook = driver._safe_point_hook(captured["attempt"], request, threading.Event(), continuation=continuation)
    assert resumed_hook("tool.before", {"tool_call_id": "function-call", "tool_name": "web_use", "arguments": {}}) is False

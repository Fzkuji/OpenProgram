from __future__ import annotations

import asyncio
import sqlite3
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
        safe_point_kinds=(
            "agent.provider.decision.after",
            "agent.tool.action.after",
        ),
        state_schema_version=1,
    )


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

    # A later safe point must not re-apply the already applied commands.
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
    assert all(
        store.get_command(command_id).status is CommandStatus.APPLIED
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


def test_cancel_targets_exact_handle_and_releases_its_question_wait(tmp_path):
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.agent.questions import PendingQuestion, QuestionRegistry

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
    async def run():
        binding = await driver.activate(active, activation=None)
        driver.activation_committed(binding)
        handle = binding.handle
        assert await asyncio.to_thread(entered.wait, 2)
        current = store.get_execution(execution.execution_id)
        assert current is not None
        store.accept_command_with_transition(
            command_id="cancel-agent-1",
            execution_id=execution.execution_id,
            expected_version=current.status_version,
            kind=CommandKind.CANCEL,
            target=ExecutionStatus.CANCELLING,
            payload={"reason_code": "cancel.user"},
            actor={"surface": "test"},
            reason_code="cancel.user",
        )
        ack = await driver.request_cancel(handle, "cancel-agent-1")
        await handle.done
        await asyncio.sleep(0)
        return ack

    ack = asyncio.run(run())
    assert ack.command_id == "cancel-agent-1"
    assert ack.attempt_id == active.attempt_id
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
        if rows and rows[0]["reason_code"] == "finish_repair_stalled":
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


def test_startup_terminalizes_admitted_agent_without_attempt(tmp_path):
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry

    store, execution = _admitted(tmp_path, execution_id="exec-unstarted-agent")
    service = RuntimeControlService(store, AttemptStore(store), DriverRegistry())

    recoveries = service.recover_startup()

    current = store.get_execution(execution.execution_id)
    assert current is not None
    assert current.status is ExecutionStatus.FAILED
    assert current.reason_code == "owner_lost_before_activation"
    assert [item.execution.execution_id for item in recoveries] == [execution.execution_id]


def test_startup_recovers_active_agent_owner_loss_without_reserved_slot_reconciliation(
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
    assert current.status is ExecutionStatus.INTERRUPTED
    assert current.reason_code == "owner_lost"
    assert current.current_attempt_id is None
    assert ended is not None and ended.status.value == "ended"
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_finish_repair_slots "
            "WHERE execution_id = ?",
            (execution.execution_id,),
        ).fetchone()[0] == 0
    assert [item.execution.execution_id for item in recoveries] == [execution.execution_id]


def test_startup_recovery_reloads_after_concurrent_transition_conflict(
    tmp_path, monkeypatch,
):
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry
    from openprogram.execution.store import ExecutionConflict

    store, execution = _admitted(tmp_path, execution_id="exec-recovery-race")
    service = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    original = store.transition_execution
    calls = 0

    def concurrent_transition(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ExecutionConflict("status_conflict", "another recovery won")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "transition_execution", concurrent_transition)
    recoveries = service.recover_startup()

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

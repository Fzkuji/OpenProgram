"""driver finalization tests."""
from __future__ import annotations
import pytest
import time
from openprogram.runtime.execution.model import CommandKind, CommandStatus, ExecutionStatus
from openprogram.runtime.execution._schema import SCHEMA_VERSION
from ._support import (
    AttemptStore,
    ExecutionStore,
    SimpleNamespace,
    _admitted,
    asyncio,
    sqlite3,
    threading,
)


def test_runner_exception_finishes_as_failed(tmp_path):
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver

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
    [ended] = attempts.list_for_execution(execution.execution_id)
    assert ended.failure_text == "RuntimeError: owner process lost"


def _failed_turn_attempt(tmp_path, result):
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver

    store, execution = _admitted(tmp_path)
    driver = AgentProductionDriver(
        executions=store,
        input_resolver=lambda _record: {
            "version": 1, "kind": "chat",
            "request": {"user_text": "run", "agent_id": "default", "source": "canonical-agent"},
        },
        turn_runner=lambda *, request, cancel_event: result,
    )
    attempts = AttemptStore(store)
    attempt, leased = attempts.lease(
        execution.execution_id, expected_version=execution.status_version, owner_id="agent-owner",
    )
    active, _running = attempts.activate(
        attempt.attempt_id, generation=attempt.generation, expected_execution_version=leased.status_version,
    )

    async def run():
        binding = await driver.activate(active, activation=None)
        driver.activation_committed(binding)
        await binding.handle.done

    asyncio.run(run())
    return attempts.list_for_execution(execution.execution_id)[0]


def test_a_failed_result_leaves_its_error_on_the_attempt(tmp_path):
    ended = _failed_turn_attempt(tmp_path, SimpleNamespace(failed=True, error="provider said no"))
    assert (ended.outcome, ended.failure_text) == ("failed", "provider said no")


def test_a_failed_mapping_result_leaves_its_error_on_the_attempt(tmp_path):
    assert _failed_turn_attempt(tmp_path, {"error": "tool blew up"}).failure_text == "tool blew up"


def test_a_completed_result_stores_no_failure_text(tmp_path):
    ended = _failed_turn_attempt(tmp_path, SimpleNamespace(failed=False, error="ignored"))
    assert (ended.outcome, ended.failure_text) == ("completed", None)


def test_failed_admission_keeps_the_text_its_caller_held(tmp_path):
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver

    store, execution = _admitted(tmp_path)
    driver = AgentProductionDriver(executions=store)
    driver.fail_admission(
        SimpleNamespace(execution_id=execution.execution_id, status_version=execution.status_version),
        reason_code="agent_runner_error", failure_text="OSError: thread could not start",
    )
    [ended] = AttemptStore(store).list_for_execution(execution.execution_id)
    assert (ended.outcome, ended.failure_text) == ("failed", "OSError: thread could not start")



def test_finish_transient_failure_retries_after_handle_release(tmp_path):
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver

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
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        current = store.get_execution(execution.execution_id)
        if current is not None and current.status is ExecutionStatus.COMPLETED:
            break
        time.sleep(0.02)
    current = store.get_execution(execution.execution_id)
    assert current is not None
    assert current.status is ExecutionStatus.COMPLETED
    assert calls >= 2



def test_finish_retry_re_reads_cancellation_state(tmp_path):
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver

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
            assert release.wait(30)
            raise OSError("temporary sqlite failure")
        return real_finish(*args, **kwargs)

    control.finish_attempt = flaky_finish
    worker = threading.Thread(
        target=driver._finish_attempt,
        args=(active, type("Result", (), {"failed": False, "error": None})(), threading.Event()),
        daemon=True,
    )
    worker.start()
    assert first_called.wait(30)
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
    deadline = time.monotonic() + 30
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



def test_startup_recovers_active_agent_owner_loss(
    tmp_path,
):
    from openprogram.runtime.execution.control import RuntimeControlService
    from openprogram.runtime.execution.driver import DriverRegistry

    store, execution = _admitted(tmp_path, execution_id="exec-active-owner-loss")
    attempts = AttemptStore(store)
    leased, leased_execution = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="crashed-agent",
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
    assert [item.execution.execution_id for item in recoveries] == [execution.execution_id]



def test_iteration_exhaustion_finishes_canonical_execution_as_failed(tmp_path):
    from openprogram.runtime.agent.loop import agent_loop
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.runtime.agent.loop.types import AgentContext, AgentLoopConfig
    from openprogram.models.providers.types import AgentTool, AgentToolResult
    from openprogram.models.providers.types import AssistantMessage, EventDone, Model, TextContent, ToolCall, UserMessage

    store, execution = _admitted(tmp_path)
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(execution.execution_id,
        expected_version=execution.status_version, owner_id="limit-owner")
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


def test_goal_lock_contention_retries_the_completion_notice(tmp_path, monkeypatch, caplog):
    """Another admission holding the Goal lock is ordinary contention."""
    import logging
    from openprogram.runtime.conversation import production_driver as module
    from openprogram.programs.tools.planning.goal import chat
    from openprogram.models.usage.goal_port import GoalConflictError

    store = ExecutionStore(tmp_path / "goal-lock.sqlite3")
    driver = module.AgentProductionDriver(store, turn_runner=lambda **kw: SimpleNamespace(failed=False))
    entry = module.CanonicalAgentEntry(store, driver)
    calls = []

    def contended(*args):
        calls.append("conflict" if len(calls) < 2 else "delivered")
        if calls[-1] == "conflict":
            raise GoalConflictError("Goal admission or mutation is already in progress")

    monkeypatch.setattr(chat, "after_terminal", contended)
    admission = entry.admit(
        session_id="goal-lock", turn_payload={"version": 1, "kind": "chat", "request": {
            "user_text": "work", "agent_id": "default", "source": "test"}},
        trusted_actor={"subject": "test"}, config_snapshot_ref="config:test",
        user_message_id="u", assistant_message_id="a",
    )

    async def run():
        active = await entry.activate(admission)
        await driver._handles[(admission.execution_id, active.attempt_id, active.generation)].done

    with caplog.at_level(logging.WARNING, logger=module.shared._log.name):
        asyncio.run(run())
    assert store.get_execution(admission.execution_id).status is ExecutionStatus.COMPLETED
    assert calls == ["conflict", "conflict", "delivered"]
    assert not [record for record in caplog.records if "Goal completion notice" in record.getMessage()]

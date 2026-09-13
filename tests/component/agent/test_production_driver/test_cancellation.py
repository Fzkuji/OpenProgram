"""driver cancellation tests."""
from __future__ import annotations
from ._support import (
    AttemptStore,
    CommandStatus,
    ExecutionStatus,
    _admitted,
    _prepare_long_turn,
    asyncio,
    pytest,
    sqlite3,
    threading,
    time,
)


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


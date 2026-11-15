"""driver cancellation tests."""
from __future__ import annotations
import pytest
import time
from openprogram.runtime.execution.model import CommandStatus, ExecutionStatus
from types import SimpleNamespace

from openprogram.runtime.execution import ExecutionStore

from ._support import AttemptStore, _admitted, _prepare_long_turn, asyncio, sqlite3, threading


def test_queued_agent_cancel_releases_reserved_slot_immediately(tmp_path):
    from openprogram.runtime.execution.control import RuntimeControlService
    from openprogram.runtime.execution.driver import DriverRegistry

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



def test_cancel_targets_exact_handle_and_releases_its_question_wait(tmp_path, monkeypatch):
    import openprogram.runtime.execution as execution_module
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.runtime.agent.questions import PendingQuestion, QuestionRegistry
    from openprogram.runtime.execution.waits import DurableWaitStore

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
    )
    active, _running = attempts.activate(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=leased.status_version,
    )
    monkeypatch.setattr(execution_module, "default_store", lambda: store)
    from openprogram.runtime.execution.control import RuntimeControlService
    from openprogram.runtime.execution.driver import DriverRegistry
    control = RuntimeControlService(store, attempts, DriverRegistry())
    DurableWaitStore(store).open_wait(
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
    from openprogram.runtime.conversation.production_driver import AgentDriverError, AgentProductionDriver

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



def test_cancel_during_a_running_tool_is_final(tmp_path):
    from openprogram.runtime.execution.state_machine import InvalidCommand

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
                "name": "page_tool", "arguments": {"n": "cancel"},
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
    assert hook("tool.started", {"tool_call_id": "call-cancel"}) is False
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
    assert finished.execution.status is ExecutionStatus.CANCELLED
    assert finished.execution.current_attempt_id is None
    assert finished.execution.owner_lease == {}
    assert finished.command is not None
    assert finished.command.status is CommandStatus.APPLIED
    with pytest.raises(InvalidCommand, match="invalid while execution is cancelled"):
        asyncio.run(control.request_continue(
            command_id="must-not-replay-uncertain-tool",
            execution_id=execution.execution_id,
            expected_version=finished.execution.status_version,
            actor={"surface": "test"},
        ))


def test_cancel_between_lease_and_activation_ends_the_turn_cancelled(tmp_path):
    """A stop that lands after the attempt is leased, before it runs, ends the turn.

    The Worker acks a chat before its run thread leases and activates the
    attempt. A cancel in between moves the execution to cancelling under that
    lease, where no driver is bound to hear it; activation then finds a newer
    version. The turn is settled as cancelled instead of staying cancelling.
    """
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver, CanonicalAgentEntry

    store = ExecutionStore(tmp_path / "executions.sqlite3")
    ran = threading.Event()

    def run_turn(*, request, cancel_event):
        ran.set()
        return SimpleNamespace(failed=False, error=None)

    driver = AgentProductionDriver(executions=store, turn_runner=run_turn)
    entry = CanonicalAgentEntry(store, driver)
    admission = entry.admit(
        session_id="cancel-before-activation",
        turn_payload={"version": 1, "kind": "chat", "request": {
            "user_text": "work", "agent_id": "default", "source": "web"}},
        trusted_actor={"subject": "user-1"}, config_snapshot_ref="config:test",
        user_message_id="u", assistant_message_id="a",
    )
    lease = entry.control.attempts.lease

    def lease_then_cancel(*args, **kwargs):
        leased = lease(*args, **kwargs)
        current = store.get_execution(admission.execution_id)
        outcome = []
        stopper = threading.Thread(target=lambda: outcome.append(asyncio.run(entry.control.request_cancel(
            command_id="cancel-before-activation",
            execution_id=admission.execution_id,
            expected_version=current.status_version,
            actor={"surface": "test"},
            reason_code="cancel.user",
        ))))
        stopper.start()
        stopper.join()
        assert outcome[0].execution.status is ExecutionStatus.CANCELLING
        return leased

    entry.control.attempts.lease = lease_then_cancel
    assert asyncio.run(entry.activate(admission)) is None
    assert not ran.is_set()
    ended = store.get_execution(admission.execution_id)
    assert ended.status is ExecutionStatus.CANCELLED
    assert ended.reason_code == "cancel.user"
    assert ended.current_attempt_id is None
    assert store.get_command("cancel-before-activation").status is CommandStatus.APPLIED


def test_cancel_before_the_lease_ends_activation_quietly(tmp_path):
    """A turn cancelled while queued is not leased: activation returns nothing to run."""
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver, CanonicalAgentEntry

    store = ExecutionStore(tmp_path / "executions.sqlite3")
    ran = threading.Event()
    driver = AgentProductionDriver(
        executions=store, turn_runner=lambda **_kw: ran.set() or SimpleNamespace(failed=False, error=None))
    entry = CanonicalAgentEntry(store, driver)
    admission = entry.admit(
        session_id="cancel-before-lease",
        turn_payload={"version": 1, "kind": "chat", "request": {
            "user_text": "work", "agent_id": "default", "source": "web"}},
        trusted_actor={"subject": "user-1"}, config_snapshot_ref="config:test",
        user_message_id="u", assistant_message_id="a",
    )
    cancelled = asyncio.run(entry.control.request_cancel(
        command_id="cancel-before-lease", execution_id=admission.execution_id,
        expected_version=admission.status_version, actor={"surface": "test"},
        reason_code="cancel.user",
    ))
    assert cancelled.execution.status is ExecutionStatus.CANCELLED
    assert asyncio.run(entry.activate(admission)) is None
    assert not ran.is_set()
    assert store.get_execution(admission.execution_id).status is ExecutionStatus.CANCELLED


def _entry(tmp_path, session_id: str):
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver, CanonicalAgentEntry

    store = ExecutionStore(tmp_path / "executions.sqlite3")
    ran = threading.Event()
    driver = AgentProductionDriver(
        executions=store, turn_runner=lambda **_kw: ran.set() or SimpleNamespace(failed=False, error=None))
    entry = CanonicalAgentEntry(store, driver)
    admission = entry.admit(
        session_id=session_id,
        turn_payload={"version": 1, "kind": "chat", "request": {
            "user_text": "work", "agent_id": "default", "source": "web"}},
        trusted_actor={"subject": "user-1"}, config_snapshot_ref="config:test",
        user_message_id="u", assistant_message_id="a",
    )
    return store, entry, admission, ran


def test_a_pause_before_activation_gives_way_through_the_one_yield_check(tmp_path, monkeypatch):
    """The starter's lease loses to a pause of the queued turn; the starter
    reads the execution again and gives way, writing nothing."""
    store, entry, admission, ran = _entry(tmp_path, "pause-before-lease")
    paused = asyncio.run(entry.control.request_pause(
        command_id="pause-before-lease", execution_id=admission.execution_id,
        expected_version=admission.status_version, actor={"surface": "test"},
    ))
    assert paused.execution.status is ExecutionStatus.PAUSED
    checked = []
    original = entry._yields_to_control_intent
    monkeypatch.setattr(entry, "_yields_to_control_intent", lambda *a: checked.append(a) or original(*a))

    assert asyncio.run(entry.activate(admission)) is None

    assert not ran.is_set()
    assert len(checked) == 1
    after = store.get_execution(admission.execution_id)
    assert after.status is ExecutionStatus.PAUSED
    assert after.status_version == paused.execution.status_version
    assert after.current_attempt_id is None


def test_a_driver_refusal_under_a_stop_gives_way_through_the_one_yield_check(tmp_path, monkeypatch):
    """The stop lands after the attempt is active, before a driver is bound:
    the driver refuses, the starter reads the execution again and the
    execution ends cancelled with the stop applied."""
    store, entry, admission, ran = _entry(tmp_path, "stop-at-driver")
    checked = []
    original = entry._yields_to_control_intent
    monkeypatch.setattr(entry, "_yields_to_control_intent", lambda *a: checked.append(a) or original(*a))
    real_activate = entry.control._activate

    async def stop_then_activate(active, *args, **kwargs):
        await entry.control.request_cancel(
            command_id="stop-at-driver", execution_id=active.execution_id,
            actor={"surface": "test"}, reason_code="cancel.user",
        )
        return await real_activate(active, *args, **kwargs)

    monkeypatch.setattr(entry.control, "_activate", stop_then_activate)

    assert asyncio.run(entry.activate(admission)) is None

    assert not ran.is_set()
    assert len(checked) == 1 and checked[0][1] is not None
    ended = store.get_execution(admission.execution_id)
    assert ended.status is ExecutionStatus.CANCELLED
    assert ended.current_attempt_id is None
    assert store.get_command("stop-at-driver").status is CommandStatus.APPLIED

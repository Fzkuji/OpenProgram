"""A chat turn a stop reaches before it starts ends as a stopped reply.

The stop goes through the real control service at each point before the
turn's run begins: while it is queued, inside the queued cancel's two writes
(cancelling, then cancelled), between the lease and the activation, and after
the activation but before a driver is bound to hear the stop. The
turn never opens its reply, so the stop's projection writes it as stopped;
nothing is raised in the run thread and nothing is logged as an error.

See docs/reference/design/runtime/execution/control.html.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import threading

import pytest

from openprogram.runtime.execution.model import ExecutionStatus
from tests.component.agent.execution import test_agent_continuation_real as _real
from tests.component.agent.execution.test_agent_continuation_real import _wait

# Shared pytest fixture, bound at module level so pytest collects it here.
real_agent_chat = _real.real_agent_chat


def _cancel(control, execution_id: str, version: int) -> None:
    asyncio.run(control.request_cancel(
        command_id=f"stop-{execution_id}", execution_id=execution_id,
        expected_version=version, actor={"surface": "test"}, reason_code="cancel.user",
    ))


def _stop_while_queued(h, monkeypatch) -> list[str]:
    from tests.support.stop_before_start import stop_every_turn_before_it_starts

    return stop_every_turn_before_it_starts(monkeypatch)


def _lease_inside_the_queued_cancel(h, monkeypatch) -> list[str]:
    """The lease runs after the cancel wrote cancelling and before it wrote cancelled."""
    from openprogram.runtime.conversation.production_driver.shared import CanonicalAgentEntry

    activate = CanonicalAgentEntry.activate
    transition = type(h.store).transition_execution
    lease_tried = threading.Event()
    stopped: list[str] = []

    def held(store, execution_id, *args, **kwargs):
        if kwargs.get("target") is ExecutionStatus.CANCELLED:
            lease_tried.wait(10)
        return transition(store, execution_id, *args, **kwargs)

    async def lease_inside(entry, admission):
        canceller = threading.Thread(
            target=_cancel, args=(entry.control, admission.execution_id, admission.status_version))
        canceller.start()
        _wait(lambda: entry.store.get_execution(admission.execution_id).status is ExecutionStatus.CANCELLING)
        try:
            return await activate(entry, admission)
        finally:
            lease_tried.set()
            canceller.join(10)
            stopped.append(admission.execution_id)

    monkeypatch.setattr(type(h.store), "transition_execution", held)
    monkeypatch.setattr(CanonicalAgentEntry, "activate", lease_inside)
    return stopped


def _stop_between_lease_and_activation(h, monkeypatch) -> list[str]:
    from openprogram.runtime.execution.attempts import AttemptStore

    lease = AttemptStore.lease
    stopped: list[str] = []

    def lease_then_stop(store, execution_id, **kwargs):
        record, execution = lease(store, execution_id, **kwargs)
        canceller = threading.Thread(target=_cancel, args=(h.control, execution_id, execution.status_version))
        canceller.start()
        canceller.join(10)
        stopped.append(execution_id)
        return record, execution

    monkeypatch.setattr(AttemptStore, "lease", lease_then_stop)
    return stopped


def _stop_between_activation_and_binding(h, monkeypatch) -> list[str]:
    """The attempt is active, but no driver is bound yet to hear the stop."""
    from openprogram.runtime.execution.attempts import AttemptStore

    activate = AttemptStore.activate
    stopped: list[str] = []

    def activate_then_stop(store, attempt_id, **kwargs):
        active, running = activate(store, attempt_id, **kwargs)
        canceller = threading.Thread(
            target=_cancel, args=(h.control, running.execution_id, running.status_version))
        canceller.start()
        canceller.join(10)
        stopped.append(running.execution_id)
        return active, running

    monkeypatch.setattr(AttemptStore, "activate", activate_then_stop)
    return stopped


def _send(h) -> None:
    from openprogram.interfaces.server.ws_actions.chat import handle_chat

    asyncio.run(handle_chat(_real._WebSocket(), {
        "text": "stop me before you start", "session_id": h.session_id, "permission_mode": "bypass",
    }))


@pytest.mark.parametrize("stop", [
    _stop_while_queued, _lease_inside_the_queued_cancel, _stop_between_lease_and_activation,
    _stop_between_activation_and_binding,
])
def test_a_turn_stopped_before_it_started_ends_as_a_stopped_reply(real_agent_chat, monkeypatch, caplog, stop):
    from openprogram.runtime.execution import event_effects

    h = real_agent_chat
    effects = event_effects.ExecutionEventEffects(h.store, after=0)
    monkeypatch.setattr(event_effects, "_RETRY_DELAY_SECONDS", 0.0)
    unhandled: list[str] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: unhandled.append(repr(args.exc_value)))
    stopped = stop(h, monkeypatch)

    with caplog.at_level(logging.WARNING):
        _send(h)
        _wait(lambda: stopped)
        _wait(lambda: h.store.get_execution(stopped[0]).status is ExecutionStatus.CANCELLED, timeout=10)
        _wait(lambda: not h.server._is_run_active(h.session_id), timeout=10)
        # The event effect follows every event, each as often as it would retry.
        for _ in range(event_effects._RETRY_LIMIT + 1):
            effects.drain()

    assert unhandled == []
    assert [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR] == []
    assert effects._retries == {}
    assert h.outcomes == []  # the turn never ran
    user, reply = h.sessions.get_branch(h.session_id)
    assert (reply["id"], reply["role"], reply["status"], reply.get("content") or "") == (
        f"{user['id']}_reply", "assistant", "cancelled", "")
    session = h.sessions.get_session(h.session_id)
    assert (session["head_id"], session["status"]) == (reply["id"], "idle")


def test_a_turn_that_ran_and_left_no_reply_is_still_reported(real_agent_chat, monkeypatch):
    """A started turn opens its reply itself; a cancel finding none has lost it."""
    from openprogram.runtime.execution.event_effects import ExecutionEventEffects
    import openprogram.runtime.conversation.run_control as run_control

    h = real_agent_chat
    claim = run_control.claim_cancel_event
    stopped: list[str] = []

    def stop_then_claim(session_id, cancel_event, *, execution_id):
        version = h.store.get_execution(execution_id).status_version
        canceller = threading.Thread(target=_cancel, args=(h.control, execution_id, version))
        canceller.start()
        canceller.join(10)
        stopped.append(execution_id)
        return claim(session_id, cancel_event, execution_id=execution_id)

    monkeypatch.setattr(run_control, "claim_cancel_event", stop_then_claim)
    _send(h)
    _wait(lambda: stopped)
    _wait(lambda: h.store.get_execution(stopped[0]).status is ExecutionStatus.CANCELLED, timeout=10)
    _wait(lambda: not h.server._is_run_active(h.session_id), timeout=10)
    execution = h.store.get_execution(stopped[0])
    source = h.store.get_execution_input(execution.execution_id)
    get_input = type(h.store).get_execution_input
    monkeypatch.setattr(type(h.store), "get_execution_input", lambda store, execution_id: (
        dataclasses.replace(source, assistant_message_id="lost_reply")
        if execution_id == execution.execution_id else get_input(store, execution_id)))

    with pytest.raises(RuntimeError, match="waiting for its assistant node"):
        ExecutionEventEffects(h.store).project_cancelled_assistant(execution)


def test_a_turn_that_writes_its_message_when_it_starts_leaves_nothing_to_close(
    real_agent_chat, monkeypatch, caplog,
):
    """A sub-agent, merge or check-in writes its message as it starts: stopped
    first, it showed nothing, so the stop writes nothing and reports nothing."""
    from openprogram.runtime.conversation.production_driver.shared import CanonicalAgentAdapter

    h = real_agent_chat
    payload_for = CanonicalAgentAdapter.payload_for

    def written_at_start(request):
        payload = payload_for(request)
        payload["request"]["user_already_persisted"] = False
        return payload

    monkeypatch.setattr(CanonicalAgentAdapter, "payload_for", staticmethod(written_at_start))
    stopped = _stop_while_queued(h, monkeypatch)
    with caplog.at_level(logging.WARNING):
        _send(h)
        _wait(lambda: stopped)
        _wait(lambda: not h.server._is_run_active(h.session_id), timeout=10)

    assert h.store.get_execution(stopped[0]).status is ExecutionStatus.CANCELLED
    assert [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR] == []
    assert [row["role"] for row in h.sessions.get_branch(h.session_id)] == ["user"]

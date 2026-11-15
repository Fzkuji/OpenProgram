"""Durable question relay between an agent subprocess and its parent."""
from __future__ import annotations

import asyncio
import multiprocessing as mp
import threading
import time

import pytest

import openprogram.runtime.agent.questions as Q
import openprogram.runtime.execution as execution_module
from openprogram.runtime.agent.questions import PendingQuestion, QuestionRegistry, QueueTransport, ask_blocking
from openprogram.runtime.execution.run_control import (
    reset_current_execution_id,
    set_current_execution_id,
)
from openprogram.runtime.execution.attempts import AttemptStore
from openprogram.runtime.execution.control import RuntimeControlService
from openprogram.runtime.execution.driver import DriverRegistry
from openprogram.runtime.execution.model import CapabilitySet
from openprogram.runtime.execution.store import ExecutionStore
from openprogram.runtime.execution.waits import DurableWaitStore


@pytest.fixture(autouse=True)
def _durable_execution(monkeypatch, tmp_path):
    store = ExecutionStore(tmp_path / "bridge.db")
    revision = store.create_revision(manifest={"entrypoint": "bridge"})
    execution = store.create_execution(
        execution_id="exec_bridge", run_id="run_bridge", session_id="session_bridge",
        revision_id=revision.revision_id, capabilities=CapabilitySet(pause=True),
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id, expected_version=execution.status_version,
        owner_id="bridge-worker",
    )
    _attempt, running = attempts.activate(
        leased.attempt_id, generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    monkeypatch.setattr(execution_module, "default_store", lambda: store)
    monkeypatch.setattr(Q, "_registry", QuestionRegistry())
    token = set_current_execution_id(running.execution_id)
    yield store, attempts, running
    reset_current_execution_id(token)


def _open_wait(store, execution, *, wait_id: str, kind: str = "ask", **request):
    return DurableWaitStore(store).open_wait(
        wait_id=wait_id, execution_id=execution.execution_id,
        attempt_id=execution.current_attempt_id,
        generation=execution.owner_lease["generation"], kind=kind,
        request={"prompt": "?", **request}, policy_snapshot={"version": 1},
        expires_at=time.time() + 60,
    )


def _answer(store, attempts, execution, wait, value):
    service = RuntimeControlService(store, attempts, DriverRegistry())
    return asyncio.run(service.request_wait_answer(
        command_id=f"answer_{wait.wait_id}", execution_id=execution.execution_id,
        expected_version=execution.status_version, actor={"surface": "test"},
        wait_id=wait.wait_id, generation=wait.claim_generation, answer=value,
    ))


def test_queue_transport_pushes_tagged_envelope() -> None:
    queue: mp.Queue = mp.get_context("spawn").Queue()
    QueueTransport(queue).publish({"id": "q1", "prompt": "lib?"})
    assert queue.get(timeout=2) == {
        "__op_question__": True, "data": {"id": "q1", "prompt": "lib?"},
    }


def test_registry_wake_does_not_write_lifecycle(_durable_execution) -> None:
    store, _attempts, execution = _durable_execution
    wait = _open_wait(store, execution, wait_id="wait_local")
    registry = Q.get_question_registry()
    event = registry.register(PendingQuestion(
        id=wait.wait_id, session_id=execution.session_id, kind="ask", prompt="?",
        execution_id=execution.execution_id,
    ))
    registry.wake(wait.wait_id)
    assert event.is_set()
    assert DurableWaitStore(store).get_wait(wait.wait_id).status.value == "open"


def _live_question(execution, **fields):
    return {
        "id": "wait_bridge", "kind": "ask", "session_id": execution.session_id,
        "expires_at": time.time() + 60,
        "request": {"prompt": "Choose library", "options": ["dayjs", "luxon"], "allow_custom": True},
        **fields,
    }


def _owner(execution):
    return {"execution_id": execution.execution_id, "attempt_id": execution.current_attempt_id,
            "generation": execution.owner_lease["generation"], "producer_id": "process:1"}


def test_parent_opens_the_wait_and_forwards_the_answer(_durable_execution, monkeypatch) -> None:
    from openprogram.runtime.conversation.process_runner import _bridge_question_to_parent

    store, attempts, execution = _durable_execution
    frames = []
    monkeypatch.setattr(Q, "emit_question_asked", lambda data: frames.append(data))
    answer_queue: mp.Queue = mp.get_context("spawn").Queue()
    pending, lock = set(), threading.Lock()

    _bridge_question_to_parent(
        _live_question(execution), answer_queue, pending, lock,
        parent_session_id=execution.session_id, **_owner(execution),
    )
    assert frames[0]["prompt"] == "Choose library"
    assert frames[0]["execution_id"] == execution.execution_id
    assert frames[0]["wait_generation"] == 0
    wait = DurableWaitStore(store).get_wait("wait_bridge")
    assert wait.policy_snapshot["mode"] == "live"
    _answer(store, attempts, store.get_execution(execution.execution_id), wait, "luxon")
    assert answer_queue.get(timeout=2) == {
        "id": "wait_bridge", "outcome": "answered", "value": "luxon",
    }


def test_parent_refuses_a_question_for_another_session(_durable_execution, monkeypatch) -> None:
    from openprogram.runtime.conversation.process_runner import _bridge_question_to_parent

    store, _attempts, execution = _durable_execution
    frames = []
    monkeypatch.setattr(Q, "emit_question_asked", lambda data: frames.append(data))
    answer_queue: mp.Queue = mp.get_context("spawn").Queue()
    pending, lock = set(), threading.Lock()
    _bridge_question_to_parent(
        _live_question(execution, session_id="another"), answer_queue, pending, lock,
        parent_session_id=execution.session_id, **_owner(execution),
    )
    assert frames == [] and pending == set()
    assert DurableWaitStore(store).get_wait("wait_bridge") is None
    assert answer_queue.get(timeout=2)["outcome"] == "cancelled"


def test_a_subprocess_question_gets_its_outcome_from_the_parent(monkeypatch) -> None:
    sent = []
    registry = QuestionRegistry()
    monkeypatch.setattr(Q, "_registry", registry)
    monkeypatch.setattr(Q, "_send_live_question_to_parent", sent.append)
    monkeypatch.setattr(Q, "get_question_registry", lambda: registry)

    def answer_when_asked():
        while not sent:
            time.sleep(0.01)
        registry.deliver(sent[0]["id"], "answered", "luxon")

    threading.Thread(target=answer_when_asked, daemon=True).start()
    with Q.live_workflow_questions(execution_id="e", attempt_id="a", generation=1, producer_id="p"):
        outcome = Q.ask_blocking(session_id="s", kind="ask", prompt="Which?", timeout=30,
                                 on_asked=lambda q: None)
    assert outcome == ("answered", "luxon")
    assert sent[0]["attempt_id"] == "a" and sent[0]["request"]["prompt"] == "Which?"


def test_child_blocking_wait_requires_declared_safe_point(_durable_execution) -> None:
    """A subprocess cannot create an unfenced wait from a live call stack."""
    _store, _attempts, execution = _durable_execution
    token = set_current_execution_id(execution.execution_id)
    try:
        with pytest.raises(Q.DurableWaitSafePointRequired):
            ask_blocking(
                session_id=execution.session_id, kind="ask", prompt="lib?", timeout=5,
                on_asked=lambda _q: None,
            )
    finally:
        reset_current_execution_id(token)

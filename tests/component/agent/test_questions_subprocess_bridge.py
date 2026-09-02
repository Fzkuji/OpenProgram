"""Durable question relay between an agent subprocess and its parent."""
from __future__ import annotations

import asyncio
import multiprocessing as mp
import threading
import time

import pytest

import openprogram.agent.questions as Q
import openprogram.execution as execution_module
from openprogram.agent.questions import PendingQuestion, QuestionRegistry, QueueTransport, ask_blocking
from openprogram.agent.run_control import reset_current_execution_id, set_current_execution_id
from openprogram.execution.attempts import AttemptStore
from openprogram.execution.control import RuntimeControlService
from openprogram.execution.driver import DriverRegistry
from openprogram.execution.model import CapabilitySet
from openprogram.execution.store import ExecutionStore
from openprogram.execution.waits import DurableWaitStore


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
        owner_id="bridge-worker", ttl_seconds=30,
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


def test_parent_projects_durable_request_and_forwards_canonical_answer(
    _durable_execution, monkeypatch,
) -> None:
    from openprogram.agent.process_runner import _bridge_question_to_parent

    store, attempts, execution = _durable_execution
    wait = _open_wait(
        store, execution, wait_id="wait_bridge", prompt="Choose library",
        options=["dayjs", "luxon"], allow_custom=True,
    )
    frames = []
    monkeypatch.setattr(Q, "emit_question_asked", lambda data: frames.append(data))
    answer_queue: mp.Queue = mp.get_context("spawn").Queue()
    pending, lock = set(), threading.Lock()

    _bridge_question_to_parent(
        {"id": wait.wait_id, "prompt": "forged"}, answer_queue, pending, lock,
        parent_session_id=execution.session_id, execution_id=execution.execution_id,
    )
    assert frames[0]["prompt"] == "Choose library"
    assert frames[0]["execution_id"] == execution.execution_id
    assert frames[0]["wait_generation"] == 0
    _answer(store, attempts, execution, wait, "luxon")
    assert answer_queue.get(timeout=2) == {
        "id": wait.wait_id, "outcome": "answered", "value": "luxon",
    }


def test_parent_rejects_forged_wait_owner(_durable_execution, monkeypatch) -> None:
    from openprogram.agent.process_runner import _bridge_question_to_parent

    store, _attempts, execution = _durable_execution
    wait = _open_wait(store, execution, wait_id="wait_owner")
    frames = []
    monkeypatch.setattr(Q, "emit_question_asked", lambda data: frames.append(data))
    answer_queue: mp.Queue = mp.get_context("spawn").Queue()
    pending, lock = set(), threading.Lock()
    _bridge_question_to_parent(
        {"id": wait.wait_id}, answer_queue, pending, lock,
        parent_session_id=execution.session_id, execution_id="exec_forged",
    )
    assert frames == []
    assert pending == set()
    assert DurableWaitStore(store).get_wait(wait.wait_id).status.value == "open"


def test_child_blocking_wait_reads_parent_command_after_wake(_durable_execution) -> None:
    """The parent only relays the terminal projection; child reads SQLite."""
    from openprogram.agent.process_runner import _bridge_question_to_parent

    store, attempts, execution = _durable_execution
    event_queue: mp.Queue = mp.get_context("spawn").Queue()
    answer_queue: mp.Queue = mp.get_context("spawn").Queue()
    pending, lock = set(), threading.Lock()
    captured = {}

    def child() -> None:
        token = set_current_execution_id(execution.execution_id)
        try:
            captured["result"] = ask_blocking(
                session_id=execution.session_id, kind="ask", prompt="lib?", timeout=5,
                on_asked=lambda q: QueueTransport(event_queue).publish({
                    "id": q.id, "prompt": q.prompt,
                }),
            )
        finally:
            reset_current_execution_id(token)

    child_thread = threading.Thread(target=child, daemon=True)
    child_thread.start()
    envelope = event_queue.get(timeout=2)
    wait_id = envelope["data"]["id"]
    _bridge_question_to_parent(
        envelope["data"], answer_queue, pending, lock,
        parent_session_id=execution.session_id, execution_id=execution.execution_id,
    )
    wait = DurableWaitStore(store).get_wait(wait_id)
    _answer(store, attempts, execution, wait, "luxon")
    # The child-side process pump only wakes its local event after the parent
    # has observed the durable terminal state.
    message = answer_queue.get(timeout=2)
    Q.get_question_registry().wake(message["id"])
    child_thread.join(timeout=2)
    assert captured["result"] == ("answered", "luxon")

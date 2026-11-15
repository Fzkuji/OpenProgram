"""Workflow questions use the current call, never restart its Python frame."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import asyncio
import threading

import pytest

from openprogram.runtime.agent.call_state import _current_runtime
from openprogram.runtime.agent.runtime import Runtime
from openprogram.runtime.agent.questions import DurableWaitSafePointRequired
from openprogram.programs.tools.interaction.ask_user import ask_user


def test_ask_user_does_not_hide_runtime_interaction_failure():
    runtime = object.__new__(Runtime)
    runtime._question_transport = object()
    runtime._ui_session_id = lambda: "unbound"
    token = _current_runtime.set(runtime)
    try:
        with pytest.raises(DurableWaitSafePointRequired):
            ask_user("Who wrote this report?")
    finally:
        _current_runtime.reset(token)


@pytest.fixture
def owner(tmp_path, monkeypatch):
    import openprogram.runtime.execution as execution
    import openprogram.runtime.agent.questions as questions
    from openprogram.runtime.execution.store import ExecutionStore
    from openprogram.runtime.execution.attempts import AttemptStore
    from openprogram.runtime.execution.model import CapabilitySet

    store = ExecutionStore(tmp_path / "live.db")
    revision = store.create_revision(manifest={"entrypoint": "workflow"})
    initial = store.create_execution(
        execution_id="workflow",
        run_id="run",
        session_id="session",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(),
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        initial.execution_id,
        expected_version=initial.status_version,
        owner_id="test",
    )
    attempt, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    monkeypatch.setattr(execution, "default_store", lambda: store)
    monkeypatch.setattr(questions, "_registry", questions.QuestionRegistry())
    return store, attempts, attempt, running


@contextmanager
def bound_runtime(owner, publish, *, producer="test-process", session="session"):
    from openprogram.runtime.agent.questions import live_workflow_questions

    store, attempts, attempt, running = owner

    class Transport:
        def publish(self, data):
            publish(data)

        def retract(self, qid):
            pass

    runtime = object.__new__(Runtime)
    runtime._question_transport = Transport()
    runtime._ui_session_id = lambda: session
    token = _current_runtime.set(runtime)
    try:
        with live_workflow_questions(
            execution_id=running.execution_id,
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            producer_id=producer,
        ):
            yield runtime
    finally:
        _current_runtime.reset(token)


def answer(owner, frame, value, *, command="answer"):
    from openprogram.runtime.execution.control import RuntimeControlService
    from openprogram.runtime.execution.driver import DriverRegistry

    store, attempts, attempt, running = owner
    service = RuntimeControlService(store, attempts, DriverRegistry())
    current = store.get_execution(running.execution_id)
    return asyncio.run(
        service.request_wait_answer(
            command_id=command,
            execution_id=running.execution_id,
            expected_version=current.status_version,
            actor={"surface": "test"},
            wait_id=frame["id"],
            generation=frame["wait_generation"],
            answer=value,
        )
    )


def test_real_ask_user_publishes_and_continues_same_call(owner):
    from openprogram.runtime.agent.questions import get_question_registry
    from openprogram.runtime.execution.waits import DurableWaitStore

    frames, effects = [], []
    ready = threading.Event()

    def publish(frame):
        frames.append(frame)
        ready.set()

    def workflow():
        with bound_runtime(owner, publish):
            effects.append("before")
            value = ask_user("Which author?")
            effects.append("after")
            return value

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(workflow)
        try:
            assert ready.wait(30), "no question was emitted"
            pending = get_question_registry().list_pending("session")
            assert len(pending) == 1 and pending[0].prompt == "Which author?"
            assert effects == ["before"] and not future.done()
            wait = DurableWaitStore(owner[0]).get_wait(frames[0]["id"])
            assert wait.checkpoint_id is None and wait.policy_snapshot["mode"] == "live"
            answer(owner, frames[0], "Alice")
            assert future.result(timeout=30) == "Alice"
            assert effects == ["before", "after"]
            assert (
                owner[0].get_execution("workflow").current_attempt_id
                == owner[2].attempt_id
            )
            assert get_question_registry().list_pending("session") == []
        finally:
            DurableWaitStore(owner[0]).cancel_execution("workflow")


def test_live_question_cancellation_unblocks_original_call(owner):
    from openprogram.host.cancellation import CancelledError
    from openprogram.runtime.execution.control import RuntimeControlService
    from openprogram.runtime.execution.driver import DriverRegistry
    from openprogram.runtime.execution.waits import DurableWaitStore

    ready = threading.Event()

    def workflow():
        with bound_runtime(owner, lambda frame: ready.set()):
            return ask_user("Cancel this")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(workflow)
        try:
            assert ready.wait(30)
            store, attempts, attempt, running = owner
            asyncio.run(
                RuntimeControlService(store, attempts, DriverRegistry()).request_cancel(
                    command_id="cancel",
                    execution_id=running.execution_id,
                    reason_code="user_cancel",
                    expected_version=store.get_execution("workflow").status_version,
                    actor={"surface": "test"},
                )
            )
            with pytest.raises(CancelledError):
                future.result(timeout=3)
        finally:
            DurableWaitStore(owner[0]).cancel_execution("workflow")


def test_publish_failure_is_not_missing_answer_and_closes_wait(owner):
    from openprogram.runtime.execution.waits import DurableWaitStore

    def fail(frame):
        raise OSError("question transport unavailable")

    with bound_runtime(owner, fail):
        with pytest.raises(OSError, match="question transport unavailable"):
            ask_user("Question")
    assert DurableWaitStore(owner[0]).list_open() == []


def test_live_owner_must_match_session(owner):
    with bound_runtime(owner, lambda frame: None, session="foreign"):
        with pytest.raises(DurableWaitSafePointRequired):
            ask_user("Question")


def test_live_owner_loss_rejects_answer_and_cleans_only_live_wait(owner):
    from openprogram.runtime.agent.questions import open_question
    from openprogram.runtime.execution.waits import DurableWaitStore
    from openprogram.runtime.execution.store import ExecutionConflict

    store, attempts, attempt, running = owner
    with bound_runtime(owner, lambda frame: None):
        question, _ = open_question(
            session_id="session", kind="ask", prompt="Q", on_asked=lambda q: None
        )
    waits = DurableWaitStore(store)
    durable = waits.open_wait(
        execution_id="workflow",
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        kind="ask",
        request={"prompt": "Durable"},
        policy_snapshot={"version": 1},
        expires_at=0,
    )
    with store._transaction() as connection:
        connection.update("attempts", {"status": "ended"}, attempt_id=attempt.attempt_id)
    with pytest.raises(ExecutionConflict, match="owner has ended"):
        answer(owner, {"id": question.id, "wait_generation": 0}, "late")
    assert waits.cancel_live_waits() == (question.id,)
    assert waits.get_wait(durable.wait_id).status.value == "open"
    with bound_runtime(owner, lambda frame: None):
        with pytest.raises(ExecutionConflict, match="no longer active"):
            ask_user("Cannot open")


def test_producer_cleanup_does_not_cancel_sibling_questions(owner):
    from openprogram.runtime.agent.questions import open_question
    from openprogram.runtime.execution.waits import DurableWaitStore

    questions = []
    for producer in ["one", "two"]:
        with bound_runtime(owner, lambda frame: None, producer=producer):
            question, _ = open_question(
                session_id="session",
                kind="ask",
                prompt=producer,
                on_asked=lambda q: None,
            )
            questions.append(question)
    store, attempts, attempt, running = owner
    closed = DurableWaitStore(store).cancel_live_waits(
        execution_id="workflow",
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        producer_id="one",
    )
    assert closed == (questions[0].id,)
    assert [w.wait_id for w in DurableWaitStore(store).list_open()] == [questions[1].id]


def test_queue_publish_failure_propagates():
    from openprogram.runtime.agent.questions import QueueTransport

    class BrokenQueue:
        def put(self, *args, **kwargs):
            raise OSError("queue is closed")

    with pytest.raises(OSError, match="queue is closed"):
        QueueTransport(BrokenQueue()).publish({"prompt": "Q"})


def test_failed_publication_does_not_cancel_another_question_in_same_process(owner):
    from openprogram.runtime.agent.questions import open_question
    from openprogram.runtime.execution.waits import DurableWaitStore

    with bound_runtime(owner, lambda frame: None):
        first, _ = open_question(
            session_id="session", kind="ask", prompt="first", on_asked=lambda q: None
        )

        def fail(question):
            raise OSError("second publish failed")

        with pytest.raises(OSError, match="second publish failed"):
            open_question(
                session_id="session", kind="ask", prompt="second", on_asked=fail
            )
    assert [wait.wait_id for wait in DurableWaitStore(owner[0]).list_open()] == [
        first.id
    ]
    answer(owner, {"id": first.id, "wait_generation": 0}, "still answerable")
    assert DurableWaitStore(owner[0]).get_wait(first.id).answer == "still answerable"

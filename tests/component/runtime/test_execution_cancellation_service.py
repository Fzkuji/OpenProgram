"""Public execution-cancellation contract from the runtime design."""

from __future__ import annotations

import inspect
import threading
from typing import Any

import pytest

from openprogram.agent import run_control
from openprogram.context.nodes import Call, ROLE_CODE
from openprogram.store import SessionNodeWriter
from openprogram.store.session.session_store import SessionStore


@pytest.fixture
def store(tmp_path, monkeypatch) -> SessionStore:
    value = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: value)
    import openprogram.store.session.session_store as store_module

    monkeypatch.setattr(store_module, "_default_store", value)
    return value


@pytest.fixture(autouse=True)
def clean_runtime_control():
    with run_control._cancel_flags_lock:
        run_control._current_tokens.clear()
        run_control._cancel_cleanup_leases.clear()
    run_control._owners.clear()
    run_control._session_index.clear()
    run_control._grace_threads.clear()
    run_control._finalizing.clear()
    run_control.set_after_intent_hook(None)
    run_control.set_execution_update_hook(None)
    run_control.CANCEL_GRACE_S = 4.0
    run_control.clear_turn_context()
    yield
    with run_control._cancel_flags_lock:
        run_control._current_tokens.clear()
        run_control._cancel_cleanup_leases.clear()
    run_control._owners.clear()
    run_control._session_index.clear()
    run_control._grace_threads.clear()
    run_control._finalizing.clear()
    run_control.set_after_intent_hook(None)
    run_control.set_execution_update_hook(None)
    run_control.CANCEL_GRACE_S = 4.0
    run_control.clear_turn_context()


def _append_execution(
    store: SessionStore,
    session_id: str,
    execution_id: str,
    *,
    status: str,
    caller: str = "",
    predecessor: str = "",
) -> None:
    SessionNodeWriter(store, session_id).append(Call(
        id=execution_id,
        role=ROLE_CODE,
        name="cancellation_probe",
        output="partial output",
        caller=caller,
        predecessor=predecessor,
        metadata={"status": status, "execution_kind": "agentic_function"},
    ))


def _node(store: SessionStore, session_id: str, execution_id: str) -> Call:
    return next(
        node for node in store.get_nodes(session_id)
        if node.id == execution_id
    )


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value[name]
    return getattr(value, name)


def test_public_contract_has_one_exact_identifier_and_compatibility_alias():
    cancel_execution = getattr(run_control, "cancel_execution")

    assert list(inspect.signature(cancel_execution).parameters) == [
        "execution_id",
    ]
    assert run_control.CancellationToken is run_control.CancelToken
    token = run_control.CancellationToken("session-1", "execution-1")
    assert token.execution_id == "execution-1"
    assert not hasattr(token, "turn_id")
    assert issubclass(run_control.ExecutionNotFound, Exception)
    assert issubclass(run_control.ExecutionNotCancellable, Exception)


def test_cancel_execution_targets_one_execution_and_persists_intent(store):
    session_id = "exact-target"
    store.create_session(session_id, "main")
    _append_execution(store, session_id, "exec-1", status="running")
    _append_execution(
        store, session_id, "exec-2", status="running", predecessor="exec-1",
    )
    first = threading.Event()
    second = threading.Event()
    run_control.register_cancel_event(
        session_id, first, execution_id="exec-1",
    )
    run_control.register_cancel_event(
        session_id, second, execution_id="exec-2",
    )

    result = run_control.cancel_execution("exec-1")

    assert _field(result, "execution_id") == "exec-1"
    assert _field(result, "status") == "cancelling"
    assert first.is_set()
    assert not second.is_set()
    first_node = _node(store, session_id, "exec-1")
    second_node = _node(store, session_id, "exec-2")
    assert first_node.metadata["status"] == "cancelling"
    assert first_node.metadata["reason_code"] == "cancel.user"
    assert second_node.metadata["status"] == "running"


def test_cancel_execution_never_overwrites_a_terminal_result(store):
    session_id = "terminal-cas"
    store.create_session(session_id, "main")
    _append_execution(store, session_id, "completed", status="completed")

    with pytest.raises(run_control.ExecutionNotCancellable):
        run_control.cancel_execution("completed")

    assert _node(store, session_id, "completed").metadata["status"] == (
        "completed"
    )


def test_cancel_execution_is_idempotent_after_cancelled(store):
    session_id = "cancel-idempotent"
    store.create_session(session_id, "main")
    _append_execution(store, session_id, "cancelled", status="cancelled")

    first = run_control.cancel_execution("cancelled")
    second = run_control.cancel_execution("cancelled")

    assert _field(first, "status") == "cancelled"
    assert _field(second, "status") == "cancelled"
    assert _node(store, session_id, "cancelled").metadata["status"] == (
        "cancelled"
    )


def test_parent_cancel_uses_caller_edges_without_duplicate_parent_storage(store):
    session_id = "cancel-tree"
    store.create_session(session_id, "main")
    _append_execution(store, session_id, "root", status="running")
    _append_execution(
        store, session_id, "child", status="running", caller="root",
    )
    _append_execution(
        store, session_id, "grandchild", status="running", caller="child",
    )
    _append_execution(
        store, session_id, "unrelated", status="running", predecessor="root",
    )
    events = {
        execution_id: threading.Event()
        for execution_id in ("root", "child", "grandchild", "unrelated")
    }
    for execution_id, event in events.items():
        run_control.register_cancel_event(
            session_id, event, execution_id=execution_id,
        )

    run_control.cancel_execution("root")

    assert events["root"].is_set()
    assert events["child"].is_set()
    assert events["grandchild"].is_set()
    assert not events["unrelated"].is_set()
    assert _node(store, session_id, "root").metadata["reason_code"] == (
        "cancel.user"
    )
    for execution_id in ("child", "grandchild"):
        node = _node(store, session_id, execution_id)
        assert node.metadata["status"] == "cancelling"
        assert node.metadata["reason_code"] == "cancel.parent"
        assert "parent_execution_id" not in node.metadata
    assert _node(store, session_id, "unrelated").metadata["status"] == (
        "running"
    )


def test_queued_execution_without_an_owner_finishes_cancelled(store):
    session_id = "queued-cancel"
    store.create_session(session_id, "main")
    _append_execution(store, session_id, "queued", status="queued")

    result = run_control.cancel_execution("queued")

    assert _field(result, "status") == "cancelled"
    node = _node(store, session_id, "queued")
    assert node.metadata["status"] == "cancelled"
    assert node.metadata["reason_code"] == "cancel.user"
    assert node.output == "partial output"


def test_unknown_execution_does_not_expose_or_mutate_another_session(store):
    store.create_session("private-session", "main")
    _append_execution(
        store, "private-session", "private-exec", status="running",
    )

    with pytest.raises(run_control.ExecutionNotFound):
        run_control.cancel_execution("missing-exec")

    assert _node(
        store, "private-session", "private-exec",
    ).metadata["status"] == "running"


def test_persist_cancelling_before_the_token_is_tripped(store):
    session_id = "intent-before-signal"
    store.create_session(session_id, "main")
    _append_execution(store, session_id, "exec-1", status="running")
    event = threading.Event()
    run_control.register_cancel_event(
        session_id, event, execution_id="exec-1",
    )
    entered = threading.Event()
    release = threading.Event()

    def hook(_execution_id: str) -> None:
        entered.set()
        release.wait(2)

    run_control.set_after_intent_hook(hook)
    result: list[object] = []

    def worker() -> None:
        result.append(run_control.cancel_execution("exec-1"))

    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(2)
    node = _node(store, session_id, "exec-1")
    assert node.metadata["status"] == "cancelling"
    assert node.metadata["reason_code"] == "cancel.user"
    assert not event.is_set()
    release.set()
    thread.join(2)
    assert event.is_set()
    assert _field(result[0], "status") == "cancelling"


def test_failed_and_interrupted_are_not_overwritten(store):
    session_id = "non-cancel-terminal"
    store.create_session(session_id, "main")
    _append_execution(store, session_id, "failed", status="failed")
    _append_execution(store, session_id, "interrupted", status="interrupted")

    with pytest.raises(run_control.ExecutionNotCancellable):
        run_control.cancel_execution("failed")
    with pytest.raises(run_control.ExecutionNotCancellable):
        run_control.cancel_execution("interrupted")

    assert _node(store, session_id, "failed").metadata["status"] == "failed"
    assert _node(store, session_id, "interrupted").metadata["status"] == (
        "interrupted"
    )


def test_repeat_cancel_on_cancelling_does_not_reset_grace(store):
    session_id = "grace-idempotent"
    store.create_session(session_id, "main")
    _append_execution(store, session_id, "exec-1", status="running")
    run_control.register_execution_owner(
        "exec-1",
        session_id,
        is_alive=lambda: True,
        terminate=lambda: False,
    )
    run_control.CANCEL_GRACE_S = 10.0

    first = run_control.cancel_execution("exec-1")
    deadline = run_control._owners["exec-1"].grace_deadline
    second = run_control.cancel_execution("exec-1")

    assert _field(first, "status") == "cancelling"
    assert _field(second, "status") == "cancelling"
    assert run_control._owners["exec-1"].grace_deadline == deadline


def test_late_cancel_of_retired_execution_does_not_trip_the_next(store):
    session_id = "retire-then-next"
    store.create_session(session_id, "main")
    _append_execution(store, session_id, "exec-a", status="running")
    event_a = threading.Event()
    run_control.register_cancel_event(
        session_id, event_a, execution_id="exec-a",
    )
    run_control.cancel_execution("exec-a")
    run_control.unregister_cancel_event(
        session_id, event_a, execution_id="exec-a",
    )

    _append_execution(store, session_id, "exec-b", status="running")
    event_b = threading.Event()
    run_control.register_cancel_event(
        session_id, event_b, execution_id="exec-b",
    )
    again = run_control.cancel_execution("exec-a")

    assert _field(again, "status") in {"cancelling", "cancelled"}
    assert not event_b.is_set()
    assert _node(store, session_id, "exec-b").metadata["status"] == "running"


def test_spawn_after_ancestor_cas_is_refused_with_cancel_parent(store):
    session_id = "admission-gate"
    store.create_session(session_id, "main")
    _append_execution(store, session_id, "parent", status="running")
    event = threading.Event()
    run_control.register_cancel_event(
        session_id, event, execution_id="parent",
    )
    entered = threading.Event()
    release = threading.Event()

    def hook(_execution_id: str) -> None:
        entered.set()
        release.wait(2)

    run_control.set_after_intent_hook(hook)

    def worker() -> None:
        run_control.cancel_execution("parent")

    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(2)
    with pytest.raises(run_control.ExecutionSpawnRefused) as caught:
        run_control.admit_child_execution(session_id, "parent")
    assert caught.value.reason_code == "cancel.parent"
    release.set()
    thread.join(2)


def test_child_entry_commit_is_atomic_with_parent_cancel(store):
    from openprogram.agentic_programming.function import (
        _append_function_call_entry,
        _call_id,
    )
    from openprogram.store import _store as store_context

    session_id = "atomic-child-admission"
    store.create_session(session_id, "main")
    _append_execution(store, session_id, "parent", status="running")
    writer = SessionNodeWriter(store, session_id)
    original_append = writer.append
    append_entered = threading.Event()
    release_append = threading.Event()

    def blocked_append(node):
        append_entered.set()
        release_append.wait(2)
        return original_append(node)

    writer.append = blocked_append

    def create_child() -> None:
        store_token = store_context.set(writer)
        call_token = _call_id.set("parent")
        try:
            _append_function_call_entry(
                pending_id="child",
                function_name="child",
                arguments={},
                expose="io",
                render_range=None,
                started_at=None,
            )
        finally:
            _call_id.reset(call_token)
            store_context.reset(store_token)

    child_thread = threading.Thread(target=create_child)
    child_thread.start()
    assert append_entered.wait(2)

    cancel_thread = threading.Thread(
        target=run_control.cancel_execution,
        args=("parent",),
    )
    cancel_thread.start()
    assert cancel_thread.is_alive()
    release_append.set()
    child_thread.join(2)
    cancel_thread.join(2)

    assert _node(store, session_id, "parent").metadata["status"] == "cancelled"
    assert _node(store, session_id, "child").metadata["status"] == "cancelled"


def test_complete_versus_cancel_has_one_terminal_transition(store):
    session_id = "complete-cancel-race"
    store.create_session(session_id, "main")
    _append_execution(store, session_id, "exec-1", status="running")
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def do_cancel() -> None:
        barrier.wait()
        try:
            record = run_control.cancel_execution("exec-1")
            outcomes.append(f"cancel:{_field(record, 'status')}")
        except run_control.ExecutionNotCancellable:
            outcomes.append("cancel:not_cancellable")

    def do_complete() -> None:
        barrier.wait()
        won = run_control.mark_execution_terminal("exec-1", "completed")
        outcomes.append("complete:won" if won else "complete:lost")

    first = threading.Thread(target=do_cancel)
    second = threading.Thread(target=do_complete)
    first.start()
    second.start()
    first.join(2)
    second.join(2)

    status = _node(store, session_id, "exec-1").metadata["status"]
    assert status in {"cancelling", "cancelled", "completed"}
    if status == "completed":
        assert "cancel:not_cancellable" in outcomes or any(
            item.startswith("cancel:") for item in outcomes
        )
        assert "complete:won" in outcomes
    else:
        assert "complete:lost" in outcomes
        assert any(item.startswith("cancel:") for item in outcomes)
    assert not (
        status == "completed"
        and _node(store, session_id, "exec-1").metadata.get("reason_code")
        == "cancel.user"
        and "complete:won" in outcomes
        and any(item == "cancel:cancelled" for item in outcomes)
    )


def test_persist_assistant_message_does_not_overwrite_cancelling(store):
    from openprogram.agent.dispatcher.persistence import persist_assistant_message
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.context.nodes import ROLE_LLM, ROLE_USER

    session_id = "persist-cas"
    store.create_session(session_id, "main")
    SessionNodeWriter(store, session_id).append(Call(
        id="user-1",
        role=ROLE_USER,
        output="run",
        predecessor="ROOT",
    ))
    SessionNodeWriter(store, session_id).append(Call(
        id="user-1_reply",
        role=ROLE_LLM,
        output="",
        predecessor="user-1",
        metadata={"status": "running", "execution_kind": "agent"},
    ))
    event = threading.Event()
    run_control.register_cancel_event(
        session_id, event, execution_id="user-1_reply",
    )
    run_control.cancel_execution("user-1_reply")
    assert _node(store, session_id, "user-1_reply").metadata["status"] == (
        "cancelling"
    )

    req = TurnRequest(
        session_id=session_id,
        user_text="run",
        agent_id="main",
        source="test",
    )
    persist_assistant_message(
        db=store,
        req=req,
        session={"id": session_id},
        usage={},
        final_text="partial reply",
        history=[],
        tool_calls=[],
        _ordered_blocks=[],
        _agentic_tool_names=set(),
        _placeholder_inserted=True,
        cancel_event=threading.Event(),
        assistant_msg_id="user-1_reply",
        user_msg_id="user-1",
    )

    node = _node(store, session_id, "user-1_reply")
    assert node.metadata["status"] == "cancelling"
    assert node.output == "partial reply"


def test_append_function_call_refuses_spawn_after_parent_cancelling(store):
    from openprogram.agentic_programming import function as fnmod
    from openprogram.store import _store

    session_id = "spawn-real-path"
    store.create_session(session_id, "main")
    _append_execution(store, session_id, "parent", status="running")
    event = threading.Event()
    run_control.register_cancel_event(
        session_id, event, execution_id="parent",
    )
    run_control.cancel_execution("parent")
    assert _node(store, session_id, "parent").metadata["status"] == "cancelling"

    writer = SessionNodeWriter(store, session_id)
    store_token = _store.set(writer)
    call_token = fnmod._call_id.set("parent")
    try:
        with pytest.raises(run_control.ExecutionSpawnRefused) as caught:
            fnmod._append_function_call_entry(
                pending_id="child-1",
                function_name="nested_probe",
                arguments={},
                expose="io",
                render_range=None,
                started_at=0.0,
            )
        assert caught.value.reason_code == "cancel.parent"
        with pytest.raises(run_control.ExecutionSpawnRefused):
            fnmod.create_pending_call_node(
                pending_id="child-2",
                function_name="nested_probe",
                arguments={},
                expose="io",
                caller="parent",
                store=writer,
            )
    finally:
        fnmod._call_id.reset(call_token)
        _store.reset(store_token)

    ids = {node.id for node in store.get_nodes(session_id)}
    assert "child-1" not in ids
    assert "child-2" not in ids


def test_cancel_execution_withdraws_a_queued_job(store, monkeypatch):
    from openprogram.agent.job.runner import shutdown_runner
    from openprogram.agent.job.store import load_job, save_job
    from openprogram.agent.job.types import Job, JobStatus

    shutdown_runner()
    session_id = "job-session"
    store.create_session(session_id, "main")
    job = Job(
        id="j_queuedcancel",
        parent_session_id=session_id,
        prompt="queued work",
        agent_id="main",
        status=JobStatus.QUEUED,
    )
    from openprogram.agent.job import get_runner
    save_job(session_id, job)
    # Startup migration marks a projection without canonical identity as
    # unavailable before any public control call can observe it.
    get_runner()

    with pytest.raises(run_control.ExecutionNotFound):
        run_control.cancel_execution("j_queuedcancel")
    persisted = load_job(session_id, "j_queuedcancel")
    assert persisted is not None
    assert persisted.status == JobStatus.ERRORED
    assert persisted.reason_code == "error.worker_lost"
    shutdown_runner()


def test_cancel_execution_signals_a_running_job_with_a_live_owner(store):
    from openprogram.agent.job import get_runner
    from openprogram.agent.job.runner import shutdown_runner
    from openprogram.agent.job.store import load_job, save_job
    from openprogram.agent.job.types import Job, JobStatus

    shutdown_runner()
    session_id = "job-running-session"
    child_session_id = "job-child-session"
    store.create_session(session_id, "main")
    store.create_session(child_session_id, "main")
    job = Job(
        id="j_runningcancel",
        parent_session_id=session_id,
        prompt="running work",
        agent_id="main",
        status=JobStatus.RUNNING,
    )
    runner = get_runner()
    save_job(session_id, job)
    child = Job(
        id="j_runningchild",
        parent_session_id=child_session_id,
        parent_job_id=job.id,
        prompt="child work",
        agent_id="main",
        status=JobStatus.RUNNING,
    )
    save_job(child_session_id, child)
    updates: list[dict] = []
    run_control.set_execution_update_hook(updates.append)
    event = threading.Event()
    assert run_control.claim_cancel_event(
        session_id, event, execution_id="j_runningcancel",
    )
    child_event = threading.Event()
    assert run_control.claim_cancel_event(
        child_session_id, child_event, execution_id="j_runningchild",
        foreground=False,
    )
    run_control.CANCEL_GRACE_S = 0.05
    try:
        with pytest.raises(run_control.ExecutionNotFound):
            run_control.cancel_execution("j_runningcancel")
        persisted = load_job(session_id, "j_runningcancel")
        assert persisted is not None
        assert persisted.status == JobStatus.RUNNING
        assert not event.is_set()
        assert not child_event.is_set()
    finally:
        run_control.unregister_cancel_event(
            session_id, event, execution_id="j_runningcancel",
        )
        run_control.unregister_cancel_event(
            child_session_id, child_event, execution_id="j_runningchild",
        )
        for owner in list(run_control._owners.values()):
            owner.retired = True
        for thread in list(run_control._grace_threads.values()):
            thread.join(1)
        run_control._grace_threads.clear()
        run_control.CANCEL_GRACE_S = 4.0
        shutdown_runner()

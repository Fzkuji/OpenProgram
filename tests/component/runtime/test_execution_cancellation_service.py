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
    yield
    with run_control._cancel_flags_lock:
        run_control._current_tokens.clear()
        run_control._cancel_cleanup_leases.clear()


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

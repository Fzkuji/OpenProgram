"""Restart recovery for durable cancel intent."""

from __future__ import annotations

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
    yield
    with run_control._cancel_flags_lock:
        run_control._current_tokens.clear()
        run_control._cancel_cleanup_leases.clear()
    run_control._owners.clear()
    run_control._grace_threads.clear()
    run_control._finalizing.clear()


def test_restart_with_cancel_intent_and_no_owner_finishes_cancelled(store):
    from openprogram.webui import _exec_dag

    store.create_session("s1", "main")
    SessionNodeWriter(store, "s1").append(Call(
        id="execution-cancelling",
        role=ROLE_CODE,
        name="cancellation_probe",
        output="partial output",
        metadata={
            "status": "cancelling",
            "reason_code": "cancel.user",
            "execution_kind": "agentic_function",
        },
    ))
    store.update_session("s1", status="cancelling")

    _exec_dag.reconcile_interrupted_runs()

    node = next(
        node for node in store.get_nodes("s1")
        if node.id == "execution-cancelling"
    )
    assert node.metadata["status"] == "cancelled"
    assert node.metadata["reason_code"] == "cancel.user"
    assert node.output == "partial output"


def test_restart_with_cancel_intent_keeps_cancelling_if_owner_alive(store):
    from openprogram.webui import _exec_dag

    store.create_session("s1", "main")
    SessionNodeWriter(store, "s1").append(Call(
        id="execution-cancelling",
        role=ROLE_CODE,
        name="cancellation_probe",
        output="partial output",
        metadata={
            "status": "cancelling",
            "reason_code": "cancel.user",
            "execution_kind": "agentic_function",
        },
    ))
    run_control.register_execution_owner(
        "execution-cancelling",
        "s1",
        is_alive=lambda: True,
        terminate=lambda: False,
    )

    _exec_dag.reconcile_interrupted_runs()

    node = next(
        node for node in store.get_nodes("s1")
        if node.id == "execution-cancelling"
    )
    assert node.metadata["status"] == "cancelling"
    assert node.metadata["status"] not in {
        "interrupted", "failed", "running", "cancelled",
    }


def test_restart_without_cancel_intent_becomes_interrupted(store):
    from openprogram.webui import _exec_dag

    store.create_session("s1", "main")
    SessionNodeWriter(store, "s1").append(Call(
        id="execution-running",
        role=ROLE_CODE,
        name="cancellation_probe",
        output="",
        metadata={"status": "running", "execution_kind": "agentic_function"},
    ))

    _exec_dag.reconcile_interrupted_runs()

    node = next(
        node for node in store.get_nodes("s1")
        if node.id == "execution-running"
    )
    assert node.metadata["status"] == "interrupted"

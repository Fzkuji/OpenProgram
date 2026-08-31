"""Live-owner cancellation: tokens, waiters, process stand-ins, finalizers."""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

from openprogram.agent import run_control
from openprogram.agent.questions import (
    PendingQuestion,
    get_question_registry,
)
from openprogram.agentic_programming.function import CancelledError
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
    run_control.clear_turn_context()
    run_control.CANCEL_GRACE_S = 0.05
    registry = get_question_registry()
    registry._pending.clear()
    registry._events.clear()
    registry._results.clear()
    yield
    run_control.CANCEL_GRACE_S = 4.0
    with run_control._cancel_flags_lock:
        run_control._current_tokens.clear()
        run_control._cancel_cleanup_leases.clear()
    run_control._owners.clear()
    run_control._grace_threads.clear()
    run_control._finalizing.clear()
    run_control.set_after_intent_hook(None)
    run_control.set_execution_update_hook(None)
    run_control.clear_turn_context()


def _append_execution(store, session_id, execution_id, *, status="running"):
    store.create_session(session_id, "main")
    SessionNodeWriter(store, session_id).append(Call(
        id=execution_id,
        role=ROLE_CODE,
        name="cancellation_probe",
        output="partial output",
        metadata={"status": status, "execution_kind": "agentic_function"},
    ))


def _node(store, session_id, execution_id):
    return next(
        node for node in store.get_nodes(session_id)
        if node.id == execution_id
    )


def _wait_status(store, session_id, execution_id, wanted, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = _node(store, session_id, execution_id).metadata["status"]
        if status == wanted:
            return status
        time.sleep(0.02)
    return _node(store, session_id, execution_id).metadata["status"]


def test_token_trips_and_question_wait_is_cancelled_not_denied(store):
    session_id = "question-cancel"
    _append_execution(store, session_id, "exec-1")
    event = threading.Event()
    run_control.register_cancel_event(
        session_id, event, execution_id="exec-1",
    )
    run_control.set_current_session_id(session_id)
    run_control.set_current_execution_id("exec-1")
    question = PendingQuestion(
        id="q1",
        session_id=session_id,
        kind="ask",
        prompt="continue?",
        execution_id="exec-1",
    )
    waiter = get_question_registry().register(question)
    outcomes: list[str] = []

    def blocked() -> None:
        waiter.wait(2)
        result = get_question_registry().consume("q1")
        outcomes.append(result[0] if result else "missing")

    thread = threading.Thread(target=blocked)
    thread.start()
    record = run_control.cancel_execution("exec-1")
    thread.join(2)

    assert event.is_set()
    assert outcomes == ["cancelled"]
    assert record["status"] in {"cancelling", "cancelled"}
    assert record["reason_code"] == "cancel.user"


def test_ask_raises_cancelled_error_instead_of_declined(store, monkeypatch):
    from openprogram.agentic_programming.runtime import Runtime

    session_id = "ask-cancelled"
    _append_execution(store, session_id, "exec-1")
    event = threading.Event()
    run_control.register_cancel_event(
        session_id, event, execution_id="exec-1",
    )
    run_control.set_current_session_id(session_id)
    run_control.set_current_execution_id("exec-1")
    runtime = Runtime()

    def cancel_soon() -> None:
        time.sleep(0.05)
        run_control.cancel_execution("exec-1")

    thread = threading.Thread(target=cancel_soon)
    thread.start()
    with pytest.raises(CancelledError):
        runtime.ask("continue?", timeout=2)
    thread.join(2)
    from openprogram.agent.questions import UserDeclined
    assert not isinstance(CancelledError, UserDeclined)


def test_grace_terminates_only_that_execution_owner(store):
    session_id = "grace-kill"
    store.create_session(session_id, "main")
    SessionNodeWriter(store, session_id).append(Call(
        id="exec-a",
        role=ROLE_CODE,
        name="cancellation_probe",
        output="a",
        metadata={"status": "running", "execution_kind": "agentic_function"},
    ))
    SessionNodeWriter(store, session_id).append(Call(
        id="exec-b",
        role=ROLE_CODE,
        name="cancellation_probe",
        output="b",
        predecessor="exec-a",
        metadata={"status": "running", "execution_kind": "agentic_function"},
    ))
    proc_a = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    proc_b = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    terminated: list[str] = []

    def terminate_a() -> bool:
        terminated.append("a")
        proc_a.kill()
        proc_a.wait(timeout=2)
        return proc_a.poll() is not None

    run_control.register_execution_owner(
        "exec-a",
        session_id,
        is_alive=lambda: proc_a.poll() is None,
        terminate=terminate_a,
        process=proc_a,
    )
    run_control.register_execution_owner(
        "exec-b",
        session_id,
        is_alive=lambda: proc_b.poll() is None,
        terminate=lambda: False,
        process=proc_b,
    )
    run_control.CANCEL_GRACE_S = 0.05
    run_control.cancel_execution("exec-a")
    assert _wait_status(store, session_id, "exec-a", "cancelled") == "cancelled"
    assert "a" in terminated
    assert proc_a.poll() is not None
    assert proc_b.poll() is None
    assert _node(store, session_id, "exec-b").metadata["status"] == "running"
    proc_b.kill()
    proc_b.wait(timeout=2)


def test_parent_finalizer_writes_cancelled_when_child_never_runs_finally(store):
    session_id = "parent-finalize"
    _append_execution(store, session_id, "exec-1")
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    finally_ran: list[bool] = []

    def terminate() -> bool:
        proc.kill()
        proc.wait(timeout=2)
        return proc.poll() is not None

    def finalize() -> None:
        finally_ran.append(True)

    run_control.register_execution_owner(
        "exec-1",
        session_id,
        is_alive=lambda: proc.poll() is None,
        terminate=terminate,
        finalize=finalize,
        process=proc,
    )
    run_control.CANCEL_GRACE_S = 0.05
    run_control.cancel_execution("exec-1")
    assert _wait_status(store, session_id, "exec-1", "cancelled") == "cancelled"
    assert proc.poll() is not None
    assert finally_ran == [True]
    assert _node(store, session_id, "exec-1").output == "partial output"


def test_unkillable_owner_stays_cancelling(store):
    session_id = "unkillable"
    _append_execution(store, session_id, "exec-1")
    diagnostics_before = []

    def terminate() -> bool:
        return False

    run_control.register_execution_owner(
        "exec-1",
        session_id,
        is_alive=lambda: True,
        terminate=terminate,
    )
    run_control.CANCEL_GRACE_S = 0.05
    record = run_control.cancel_execution("exec-1")
    time.sleep(0.2)
    node = _node(store, session_id, "exec-1")
    assert record["status"] == "cancelling"
    assert node.metadata["status"] == "cancelling"
    assert node.metadata["status"] != "running"
    owner = run_control._owners["exec-1"]
    assert owner.diagnostics
    assert not owner.retired
    diagnostics_before.append(list(owner.diagnostics))
    assert diagnostics_before[0]


def test_cooperative_owner_exit_writes_cancelled(store):
    session_id = "coop-exit"
    _append_execution(store, session_id, "exec-1")
    live = True

    run_control.register_execution_owner(
        "exec-1",
        session_id,
        is_alive=lambda: live,
        token=run_control.CancellationToken(session_id, "exec-1"),
    )
    record = run_control.cancel_execution("exec-1")
    assert record["status"] == "cancelling"
    assert _node(store, session_id, "exec-1").metadata["status"] == "cancelling"

    live = False
    run_control.retire_execution_owner("exec-1")
    for thread in list(run_control._grace_threads.values()):
        thread.join(1)

    assert _node(store, session_id, "exec-1").metadata["status"] == "cancelled"
    assert "exec-1" not in run_control._owners
    assert not run_control.owner_is_alive("exec-1")


def test_root_waits_for_live_descendant_before_cancelled(store):
    session_id = "descendant-finalize"
    store.create_session(session_id, "main")
    writer = SessionNodeWriter(store, session_id)
    writer.append(Call(
        id="root",
        role=ROLE_CODE,
        name="root",
        metadata={"status": "running", "execution_kind": "agentic_function"},
    ))
    writer.append(Call(
        id="child",
        role=ROLE_CODE,
        name="child",
        caller="root",
        metadata={"status": "running", "execution_kind": "agentic_function"},
    ))
    child_live = True
    updates: list[dict] = []
    run_control.set_execution_update_hook(updates.append)
    run_control.register_execution_owner(
        "child", session_id, is_alive=lambda: child_live,
    )

    record = run_control.cancel_execution("root")

    assert record["status"] == "cancelling"
    assert _node(store, session_id, "root").metadata["status"] == "cancelling"
    assert _node(store, session_id, "child").metadata["status"] == "cancelling"

    child_live = False
    run_control.retire_execution_owner("child")

    assert _wait_status(store, session_id, "child", "cancelled") == "cancelled"
    assert _wait_status(store, session_id, "root", "cancelled") == "cancelled"
    assert any(
        update["execution_id"] == "root" and update["status"] == "cancelled"
        for update in updates
    )


def test_root_finalizes_after_live_grandchild_retires(store):
    session_id = "deep-descendant-finalize"
    store.create_session(session_id, "main")
    writer = SessionNodeWriter(store, session_id)
    writer.append(Call(
        id="root",
        role=ROLE_CODE,
        name="root",
        metadata={"status": "running", "execution_kind": "agentic_function"},
    ))
    writer.append(Call(
        id="child",
        role=ROLE_CODE,
        name="child",
        caller="root",
        metadata={"status": "running", "execution_kind": "agentic_function"},
    ))
    writer.append(Call(
        id="grandchild",
        role=ROLE_CODE,
        name="grandchild",
        caller="child",
        metadata={"status": "running", "execution_kind": "agentic_function"},
    ))
    grandchild_live = True
    run_control.register_execution_owner(
        "grandchild", session_id, is_alive=lambda: grandchild_live,
    )

    record = run_control.cancel_execution("root")

    assert record["status"] == "cancelling"
    assert _node(store, session_id, "root").metadata["status"] == "cancelling"
    assert _node(store, session_id, "grandchild").metadata["status"] == "cancelling"

    grandchild_live = False
    run_control.retire_execution_owner("grandchild")

    assert _wait_status(
        store, session_id, "grandchild", "cancelled",
    ) == "cancelled"
    assert _wait_status(store, session_id, "root", "cancelled") == "cancelled"


def test_grace_retries_until_owner_terminates(store):
    session_id = "retry-terminate"
    _append_execution(store, session_id, "exec-1")
    live = True
    attempts = 0

    def terminate() -> bool:
        nonlocal attempts, live
        attempts += 1
        if attempts < 3:
            return False
        live = False
        return True

    run_control.register_execution_owner(
        "exec-1",
        session_id,
        is_alive=lambda: live,
        terminate=terminate,
    )
    run_control.CANCEL_GRACE_S = 0.02

    run_control.cancel_execution("exec-1")

    assert _wait_status(store, session_id, "exec-1", "cancelled") == "cancelled"
    assert attempts >= 3


def test_grace_retries_after_transient_finalize_failure(
    store, monkeypatch,
):
    session_id = "retry-finalize"
    _append_execution(store, session_id, "exec-1")
    live = True
    original_update = store.update_node
    failures = 0

    def flaky_update(session_id, node_id, *, metadata=None, **kwargs):
        nonlocal failures
        if (metadata or {}).get("status") == "cancelled" and failures == 0:
            failures += 1
            raise OSError("transient persistence failure")
        return original_update(
            session_id, node_id, metadata=metadata, **kwargs,
        )

    monkeypatch.setattr(store, "update_node", flaky_update)

    def terminate() -> bool:
        nonlocal live
        live = False
        return True

    run_control.register_execution_owner(
        "exec-1",
        session_id,
        is_alive=lambda: live,
        terminate=terminate,
    )
    run_control.CANCEL_GRACE_S = 0.02

    run_control.cancel_execution("exec-1")

    assert _wait_status(store, session_id, "exec-1", "cancelled") == "cancelled"
    assert failures == 1


def test_late_owner_registration_reconciles_persisted_cancel(store):
    session_id = "late-owner"
    _append_execution(store, session_id, "exec-1")
    assert run_control.cancel_execution("exec-1")["status"] == "cancelled"
    live = True
    token = run_control.CancellationToken(session_id, "exec-1")

    def terminate() -> bool:
        nonlocal live
        live = False
        return True

    run_control.CANCEL_GRACE_S = 0.02
    run_control.register_execution_owner(
        "exec-1",
        session_id,
        token=token,
        is_alive=lambda: live,
        terminate=terminate,
    )

    assert token.is_cancelled()
    deadline = time.time() + 2
    while live and time.time() < deadline:
        time.sleep(0.02)
    assert not live
    assert not run_control.owner_is_alive("exec-1")


def test_forced_tool_passes_canonical_execution_id(monkeypatch):
    from openprogram.agent.dispatcher import forced_tool
    from openprogram.agent import surface_context

    captured: dict = {}
    released = []
    terminal: list[tuple[str, str]] = []
    runner_out = {"ok": True}

    class _Tool:
        name = "wc"
        _is_agentic = True

    monkeypatch.setattr(
        "openprogram.programs.agent_tools", lambda names=None: [_Tool()],
    )
    monkeypatch.setattr(
        "openprogram.programs._runtime.get",
        lambda name, *a, **k: _Tool() if name == _Tool.name else None,
    )
    monkeypatch.setattr(
        "openprogram.agent.process_runner.run_agentic_in_subprocess",
        lambda **kw: captured.update(kw) or dict(runner_out),
    )
    page_context = {"context_id": "page-context", "surfaces": []}
    monkeypatch.setattr(surface_context, "capture_pages", lambda: page_context)
    monkeypatch.setattr(
        surface_context,
        "release_bindings",
        lambda context: released.append(context),
    )
    monkeypatch.setattr(
        "openprogram.agent.run_control.set_current_session_id", lambda sid: object(),
    )
    monkeypatch.setattr(
        "openprogram.agent.run_control.reset_current_session_id", lambda t: None,
    )
    monkeypatch.setattr(
        "openprogram.agent.run_control.clear_cancel", lambda sid: None,
    )
    monkeypatch.setattr(
        "openprogram.agent.run_control.mark_execution_terminal",
        lambda execution_id, status: terminal.append((execution_id, status)),
    )
    records: dict[str, dict] = {}

    class _DB:
        @staticmethod
        def invalidate_cache(session_id):
            return None

        @staticmethod
        def update_session(*args, **kwargs):
            return None

        @staticmethod
        def get_nodes(session_id):
            return [
                type("Node", (), {"id": node_id, "metadata": metadata})()
                for node_id, metadata in records.items()
            ]

    monkeypatch.setattr("openprogram.agent.session_db.default_db", _DB)

    forced_tool.dispatch_forced_tool_call(
        session_id="s1",
        anchor_msg_id="user1|node:forcednode",
        tool_name="wc",
        tool_input={"text": "hi"},
        execution_id="forcednode",
    )
    assert captured["execution_id"] == "forcednode"
    assert terminal[-1] == ("forcednode", "completed")

    captured.clear()
    forced_tool.dispatch_forced_tool_call(
        session_id="s1",
        anchor_msg_id="|node:fromanchor",
        tool_name="wc",
        tool_input={"text": "hi"},
    )
    assert captured["execution_id"] == "fromanchor"
    assert terminal[-1] == ("fromanchor", "completed")

    _Tool.name = "gui_agent"
    captured.clear()
    forced_tool.dispatch_forced_tool_call(
        session_id="s1",
        anchor_msg_id="|node:guiagent",
        tool_name="gui_agent",
        tool_input={"task": "inspect", "surface": "browser"},
    )
    assert captured["timeout_seconds"] == 300
    assert captured["surface_context_snapshot"] is page_context
    assert released == [page_context]
    _Tool.name = "wc"

    runner_out.clear()
    runner_out.update({"killed": True, "signal": 9})
    forced_tool.dispatch_forced_tool_call(
        session_id="s1",
        anchor_msg_id="|node:unexpected",
        tool_name="wc",
        tool_input={},
    )
    assert terminal[-1] == ("unexpected", "interrupted")

    records["requested"] = {
        "status": "cancelling",
        "cancellation_requested_at": 1.0,
    }
    forced_tool.dispatch_forced_tool_call(
        session_id="s1",
        anchor_msg_id="|node:requested",
        tool_name="wc",
        tool_input={},
    )
    assert terminal[-1] == ("requested", "cancelled")


def test_register_on_cancelled_execution_does_not_retrip(store):
    """A retry that reuses a cancelled execution id must start clean."""
    session_id = "retry-cancelled"
    execution_id = "user-1_reply"
    _append_execution(store, session_id, execution_id, status="cancelled")
    ev = threading.Event()
    token = run_control.CancellationToken(session_id, execution_id)
    token._event = ev
    run_control.register_execution_owner(
        execution_id, session_id, token=token,
    )
    assert ev.is_set() is False
    assert token.is_cancelled() is False

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient


async def _async_noop():
    return None


def test_canonical_execution_recovery_runs_before_legacy_dag_recovery(
    monkeypatch,
):
    from openprogram.webui import server

    events = []
    dag_done = threading.Event()

    async def recover_execution_control():
        events.append("execution")

    def reconcile_interrupted_runs():
        events.append("dag")
        dag_done.set()
        return 0

    monkeypatch.setattr(server, "_recover_execution_control", recover_execution_control)
    monkeypatch.setattr(server, "reconcile_interrupted_runs", reconcile_interrupted_runs)
    monkeypatch.setattr("openprogram.mcp.load_mcp_servers", _async_noop)
    monkeypatch.setattr("openprogram.mcp.shutdown_mcp_servers", _async_noop)
    monkeypatch.setattr("openprogram.skills.watcher.start_watcher", lambda **_: None)
    monkeypatch.setattr("openprogram.plugins.autoupdate.start", lambda: None)
    monkeypatch.setattr("openprogram.agent._rewind.recover_all_rewinds", lambda: 0)

    app = server.create_app(owner_auth=object())

    async def run_lifespan():
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(run_lifespan())

    assert dag_done.wait(timeout=1)
    assert events == ["execution", "dag"]


def test_canonical_execution_recovery_failure_blocks_startup(monkeypatch):
    from openprogram.webui import server

    events = []

    def fail_recovery():
        raise RuntimeError("canonical recovery unavailable")

    monkeypatch.setattr(
        "openprogram.execution.default_control_service", fail_recovery
    )
    monkeypatch.setattr(
        server,
        "reconcile_interrupted_runs",
        lambda: events.append("dag"),
    )
    monkeypatch.setattr("openprogram.mcp.load_mcp_servers", _async_noop)
    monkeypatch.setattr("openprogram.mcp.shutdown_mcp_servers", _async_noop)
    monkeypatch.setattr("openprogram.skills.watcher.start_watcher", lambda **_: None)
    monkeypatch.setattr("openprogram.plugins.autoupdate.start", lambda: None)
    monkeypatch.setattr("openprogram.agent._rewind.recover_all_rewinds", lambda: 0)

    app = server.create_app(owner_auth=object())

    async def run_lifespan():
        async with app.router.lifespan_context(app):
            pass

    with pytest.raises(RuntimeError, match="canonical recovery unavailable"):
        asyncio.run(run_lifespan())

    assert events == []


def test_legacy_dag_recovery_does_not_block_public_lifespan(
    monkeypatch,
):
    """A legacy history lock must not delay the public health endpoint."""
    from openprogram.webui import server

    entered = threading.Event()
    released = threading.Event()
    finished = threading.Event()

    def blocked_recovery():
        entered.set()
        released.wait(timeout=5)
        finished.set()
        return 0

    monkeypatch.setattr(server, "reconcile_interrupted_runs", blocked_recovery)
    monkeypatch.setattr(server, "_recover_execution_control", _async_noop)
    monkeypatch.setattr("openprogram.mcp.load_mcp_servers", _async_noop)
    monkeypatch.setattr("openprogram.mcp.shutdown_mcp_servers", _async_noop)
    monkeypatch.setattr("openprogram.skills.watcher.start_watcher", lambda **_: None)
    monkeypatch.setattr("openprogram.plugins.autoupdate.start", lambda: None)
    monkeypatch.setattr("openprogram.agent._rewind.recover_all_rewinds", lambda: 0)

    app = server.create_app()
    started_at = time.monotonic()
    try:
        with TestClient(app, base_url="http://127.0.0.1:18100") as client:
            assert time.monotonic() - started_at < 2
            assert entered.wait(timeout=1)
            response = client.get("/healthz")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
    finally:
        released.set()

    assert finished.wait(timeout=1)


def test_rewind_recovery_is_lazy_and_not_a_lifespan_gate(monkeypatch):
    """Global rewind enumeration must not run before the public app is ready."""
    from openprogram.webui import server

    called = threading.Event()

    def recover_all_rewinds():
        called.set()
        return 0

    monkeypatch.setattr("openprogram.agent._rewind.recover_all_rewinds", recover_all_rewinds)
    monkeypatch.setattr(server, "_recover_execution_control", _async_noop)
    monkeypatch.setattr(server, "reconcile_interrupted_runs", lambda: 0)
    monkeypatch.setattr("openprogram.mcp.load_mcp_servers", _async_noop)
    monkeypatch.setattr("openprogram.mcp.shutdown_mcp_servers", _async_noop)
    monkeypatch.setattr("openprogram.skills.watcher.start_watcher", lambda **_: None)
    monkeypatch.setattr("openprogram.plugins.autoupdate.start", lambda: None)

    app = server.create_app()
    with TestClient(app, base_url="http://127.0.0.1:18100") as client:
        response = client.get("/healthz")
        assert response.status_code == 200
    assert not called.is_set()


def test_legacy_dag_recovery_waits_on_real_session_lock_after_health(
    tmp_path, monkeypatch,
):
    """A real session lock cannot prevent the public lifespan from yielding."""
    from openprogram.execution import ExecutionStore
    from openprogram.store.session.session_store import SessionStore
    from openprogram.webui import _exec_dag, server

    sessions = SessionStore(tmp_path / "sessions")
    session_id = "legacy-lock-session"
    sessions.create_session(session_id, "main")
    executions = ExecutionStore(tmp_path / "executions.sqlite")
    entered = threading.Event()
    finished = threading.Event()

    def reconcile_interrupted_runs():
        entered.set()
        result = _exec_dag.reconcile_interrupted_runs()
        finished.set()
        return result

    monkeypatch.setattr(server, "reconcile_interrupted_runs", reconcile_interrupted_runs)
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: sessions)
    monkeypatch.setattr("openprogram.execution.default_store", lambda: executions)
    monkeypatch.setattr(server, "_recover_execution_control", _async_noop)
    monkeypatch.setattr("openprogram.mcp.load_mcp_servers", _async_noop)
    monkeypatch.setattr("openprogram.mcp.shutdown_mcp_servers", _async_noop)
    monkeypatch.setattr("openprogram.skills.watcher.start_watcher", lambda **_: None)
    monkeypatch.setattr("openprogram.plugins.autoupdate.start", lambda: None)
    monkeypatch.setattr("openprogram.agent._rewind.recover_all_rewinds", lambda: 0)

    session_lock = sessions._session_lock(session_id)  # noqa: SLF001
    session_lock.acquire()
    try:
        app = server.create_app()
        with TestClient(app, base_url="http://127.0.0.1:18100") as client:
            assert entered.wait(timeout=1)
            response = client.get("/healthz")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
            assert not finished.wait(timeout=0.1)
            session_lock.release()
            assert finished.wait(timeout=1)
            session_lock = None
    finally:
        if session_lock is not None:
            session_lock.release()


def test_dag_recovery_preserves_canonical_paused_execution_after_wait_resolution(
    tmp_path, monkeypatch,
):
    """A resolved wait can leave a paused execution before continuation owns it."""
    from openprogram.context.nodes import Call, ROLE_CODE
    from openprogram.execution import CapabilitySet, ExecutionStore
    from openprogram.execution.attempts import AttemptStore
    from openprogram.store import SessionNodeWriter
    from openprogram.store.session.session_store import SessionStore
    from openprogram.webui import _exec_dag
    from openprogram.execution.model import ExecutionStatus

    sessions = SessionStore(tmp_path / "sessions")
    sessions.create_session("system-recovery", "main")
    sessions.update_session("system-recovery", status="running")
    sessions.update_session("system-recovery", status="running")
    SessionNodeWriter(sessions, "system-recovery").append(Call(
        id="assistant-system-recovery",
        role=ROLE_CODE,
        name="agentic_workflow",
        output="",
        metadata={"status": "running", "execution_kind": "agentic_function"},
    ))
    executions = ExecutionStore(tmp_path / "executions.sqlite")
    revision = executions.create_revision(manifest={"entrypoint": "agent"})
    execution = executions.admit_execution(
        execution_id="execution-system-recovery",
        run_id="run-system-recovery",
        session_id="system-recovery",
        revision_id=revision.revision_id,
        input_ref="input:system-recovery",
        input_hash="hash:system-recovery",
        entrypoint="openprogram.agent.production_driver:AgentProductionDriver",
        trusted_actor={"subject": "owner"},
        config_snapshot_ref="config:system-recovery",
        assistant_message_id="assistant-system-recovery",
        capabilities=CapabilitySet(pause=True),
    )
    executions.transition_execution(
        execution.execution_id,
        expected_version=execution.status_version,
        target=ExecutionStatus.PAUSED,
        reason_code="system_access_required",
    )
    current_node_id = "assistant-current-recovery"
    SessionNodeWriter(sessions, "system-recovery").append(Call(
        id=current_node_id,
        role=ROLE_CODE,
        name="agentic_workflow",
        output="",
        metadata={"status": "running", "execution_kind": "agentic_function"},
    ))
    current = executions.admit_execution(
        execution_id="execution-current-recovery",
        run_id="run-current-recovery",
        session_id="system-recovery",
        revision_id=revision.revision_id,
        input_ref="input:current-recovery",
        input_hash="hash:current-recovery",
        entrypoint="openprogram.agent.production_driver:AgentProductionDriver",
        trusted_actor={"subject": "owner"},
        config_snapshot_ref="config:current-recovery",
        assistant_message_id=current_node_id,
        capabilities=CapabilitySet(pause=True),
    )
    current_attempts = AttemptStore(executions)
    leased, reserved = current_attempts.lease(
        current.execution_id,
        expected_version=current.status_version,
        owner_id="current-worker",
        ttl_seconds=30,
        attempt_id="current-attempt",
    )
    _current_attempt, current = current_attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: sessions)
    monkeypatch.setattr("openprogram.execution.default_store", lambda: executions)

    _exec_dag.reconcile_interrupted_runs()

    node = sessions.get_nodes("system-recovery")[0]
    assert node.metadata["status"] == "paused"
    assert node.metadata.get("error") is None
    assert node.output == ""
    current_node = next(
        item for item in sessions.get_nodes("system-recovery")
        if item.id == current_node_id
    )
    assert current_node.metadata["status"] == "running"
    assert sessions.get_session("system-recovery")["status"] == "running"


def test_projection_cleans_only_synthetic_marker_and_preserves_cancel_output():
    from types import SimpleNamespace

    from openprogram.context.nodes import Call, ROLE_CODE
    from openprogram.execution.model import ExecutionStatus
    from openprogram.webui import _exec_dag

    node = Call(
        id="cancelled-system-recovery",
        role=ROLE_CODE,
        output="user-visible cancellation output",
        metadata={
            "status": "interrupted",
            "error": "Worker restarted before this turn finished",
            "interrupted_at": 2.0,
            "reason_code": "cancel.user",
        },
    )
    updates = []

    class _Shim:
        def update(self, node_id, **fields):
            updates.append((node_id, fields))

    changed = _exec_dag._repair_canonical_node(
        node, SimpleNamespace(status=ExecutionStatus.CANCELLED), _Shim(),
    )

    assert changed is True
    assert updates == [(
        "cancelled-system-recovery",
        {"metadata": {
            "status": "cancelled", "error": None, "error_type": None,
            "interrupted_at": None,
        }},
    )]
    assert node.output == "user-visible cancellation output"


def test_projection_leaves_real_canonical_interruption_unchanged():
    from types import SimpleNamespace

    from openprogram.context.nodes import Call, ROLE_CODE
    from openprogram.execution.model import ExecutionStatus
    from openprogram.webui import _exec_dag

    node = Call(
        id="real-interruption",
        role=ROLE_CODE,
        output="real interruption output",
        metadata={"status": "interrupted", "error": "process crashed"},
    )
    updates = []

    class _Shim:
        def update(self, node_id, **fields):
            updates.append((node_id, fields))

    assert _exec_dag._repair_canonical_node(
        node, SimpleNamespace(status=ExecutionStatus.INTERRUPTED), _Shim(),
    ) is False
    assert updates == []


@pytest.mark.parametrize("shared_parent", [False, True])
def test_hydration_projects_cancelled_wait_without_erasing_output(tmp_path, monkeypatch, shared_parent):
    from openprogram.context.nodes import Call, ROLE_CODE
    from openprogram.execution import CapabilitySet, ExecutionStore
    from openprogram.execution.model import ExecutionStatus
    from openprogram.store import SessionNodeWriter
    from openprogram.store.session.session_store import SessionStore
    from openprogram.webui import _exec_dag

    sessions = SessionStore(tmp_path / "sessions")
    sessions.create_session("cancel-wait", "main")
    SessionNodeWriter(sessions, "cancel-wait").append(Call(
        id="cancel-anchor", role=ROLE_CODE, name="gui_agent",
        output="recorded partial output", metadata={"status": "running"},
    ))
    executions = ExecutionStore(tmp_path / "executions.sqlite")
    revision = executions.create_revision(manifest={"entrypoint": "agent"})
    execution = executions.admit_execution(
        execution_id="cancel-execution", run_id="cancel-run", session_id="cancel-wait",
        revision_id=revision.revision_id, input_ref="input:cancel", input_hash="hash:cancel",
        entrypoint="openprogram.agent.production_driver:AgentProductionDriver",
        trusted_actor={"subject": "owner"}, config_snapshot_ref="config:cancel",
        assistant_message_id="cancel-anchor", capabilities=CapabilitySet(pause=True),
    )
    if shared_parent:
        execution = executions.admit_execution(
            execution_id="cancel-child", run_id="cancel-run", session_id="cancel-wait",
            parent_execution_id=execution.execution_id,
            revision_id=revision.revision_id, input_ref="input:child", input_hash="hash:child",
            entrypoint="openprogram.agent.production_driver:AgentProductionDriver",
            trusted_actor={"subject": "owner"}, config_snapshot_ref="config:child",
            assistant_message_id="cancel-anchor", capabilities=CapabilitySet(pause=True),
        )
    execution = executions.transition_execution(execution.execution_id,
        expected_version=execution.status_version, target=ExecutionStatus.CANCELLING,
        reason_code="cancel.user")
    executions.transition_execution(execution.execution_id,
        expected_version=execution.status_version, target=ExecutionStatus.CANCELLED,
        reason_code="cancel.user")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: sessions)
    monkeypatch.setattr("openprogram.execution.default_store", lambda: executions)
    _exec_dag.reconcile_session_projection("cancel-wait")
    node = sessions.get_nodes("cancel-wait")[0]
    assert node.metadata["status"] == ("running" if shared_parent else "cancelled")
    assert node.output == "recorded partial output"


def test_stop_server_requests_exit_and_waits_for_thread(monkeypatch):
    from types import SimpleNamespace
    from openprogram.webui import server
    events = []
    fake_server = SimpleNamespace(should_exit=False)
    class Loop:
        def is_closed(self): return False
        def call_soon_threadsafe(self, fn, *args):
            events.append("exit")
            fn(*args)
    class Thread:
        def join(self, timeout): events.append("join")
        def is_alive(self): return False
    monkeypatch.setattr(server, "_uvicorn_server", fake_server)
    monkeypatch.setattr(server, "_loop", Loop())
    monkeypatch.setattr(server, "_server_thread", Thread())
    monkeypatch.setattr(server, "_server_stopping", threading.Event())
    assert server.stop_server() is True
    assert fake_server.should_exit
    assert events == ["exit", "join"]


def test_shutdown_rejects_new_websocket_work(monkeypatch):
    import json
    from openprogram.webui import server
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(server, "_server_stopping", stop)
    messages = []
    class Socket:
        async def send_text(self, value): messages.append(json.loads(value))
    asyncio.run(server._handle_ws_command(Socket(), {"action": "chat", "message": "work"}))
    assert messages[0]["data"]["code"] == "worker_stopping"


def test_worker_shutdown_blocks_new_invocations_without_user_cancel(monkeypatch):
    from openprogram.agent import run_control
    from openprogram.providers.utils.errors import ExecInterrupt
    signal = threading.Event()
    monkeypatch.setattr(run_control, "_worker_stopping", signal)
    run_control.begin_worker_shutdown()
    with pytest.raises(ExecInterrupt, match="worker_stopping"):
        run_control.check_cancelled()

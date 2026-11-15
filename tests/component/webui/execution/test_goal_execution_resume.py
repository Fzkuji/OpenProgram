"""Goal resume must respect the previous canonical execution."""

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.runtime.context.session_db import SessionDB
from openprogram.runtime.execution.store import ExecutionStore
from openprogram.runtime.execution.model import ExecutionStatus


@pytest.fixture
def bound_goal(tmp_path, monkeypatch):
    package = importlib.import_module("openprogram.programs.tools.planning.goal")
    from openprogram.interfaces.server.routes.execution import goal as routes

    db = SessionDB(tmp_path / "sessions")
    db.create_session("bound", "main")
    store = ExecutionStore(tmp_path / "executions.db")
    revision = store.create_revision(manifest={"entrypoint": "goal"})
    execution = store.create_execution(
        session_id="bound", revision_id=revision.revision_id
    )
    monkeypatch.setattr(package, "_db", lambda: db)
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    monkeypatch.setattr(package, "_emit_goal_update", lambda *_a: None)
    package.save_goal(
        "bound",
        {
            "goal_id": "g",
            "run_id": "g-run",
            "version": 0,
            "text": "write a survey",
            "status": "paused",
            "pause_reason": "user",
            "execution_id": execution.execution_id,
        },
    )
    app = FastAPI()
    routes.register(app)
    return package, store, execution, TestClient(app)


def test_http_resume_rejects_a_previous_execution_that_has_not_stopped(bound_goal):
    package, _store, _execution, client = bound_goal
    before = package.load_goal("bound")
    response = client.post("/api/sessions/bound/goal", json={"action": "resume"})
    assert response.status_code == 409
    assert "invoke" not in response.json()
    assert package.load_goal("bound") == before


@pytest.mark.parametrize("surface", ["http", "tui"])
def test_pause_reports_failed_stop_without_losing_saved_intent(
    bound_goal, monkeypatch, surface
):
    package, _store, execution, client = bound_goal
    state = package.load_goal("bound")
    state.update(status="active", phase="working")
    package.save_goal("bound", state)

    def fail_stop(_execution_id):
        raise OSError("secret database path")

    monkeypatch.setattr("openprogram.runtime.execution.run_control.cancel_execution", fail_stop)
    if surface == "http":
        response = client.post("/api/sessions/bound/goal", json={"action": "pause"})
        assert response.status_code == 200
        result = response.json()
        assert "not confirmed" in result.get("stop_error", "")
        assert result["execution"]["status"] == "queued"
    else:
        result = package.handle_goal_command("bound", "pause")
        assert "not confirmed" in result["text"]
    assert "secret" not in str(result)
    saved = package.load_goal("bound")
    assert saved["status"] == "paused" and saved["stop_requested"] is True
    assert saved["execution_id"] == execution.execution_id
    assert "invoke" not in result
    assert (
        client.post("/api/sessions/bound/goal", json={"action": "resume"}).status_code
        == 409
    )


def test_stop_retry_reuses_the_canonical_command_and_reports_driver_failure(
    bound_goal, monkeypatch
):
    from openprogram.runtime.execution.attempts import AttemptStore
    from openprogram.runtime.execution.control import RuntimeControlService
    from openprogram.runtime.execution.driver import DriverAck, DriverBinding, DriverRegistry

    package, store, execution, client = bound_goal
    attempts = AttemptStore(store)
    lease, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="test",
    )
    attempt, running = attempts.activate(
        lease.attempt_id,
        generation=lease.generation,
        expected_execution_version=reserved.status_version,
    )

    class Driver:
        fail = True
        calls = []

        async def request_cancel(self, handle, command_id):
            self.calls.append(command_id)
            if self.fail:
                raise OSError("private driver error")
            return DriverAck(command_id=command_id, attempt_id=attempt.attempt_id)

    driver = Driver()
    registry = DriverRegistry()
    registry.bind(
        DriverBinding(
            execution_id=running.execution_id,
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            driver=driver,
            handle={},
        )
    )
    service = RuntimeControlService(store, attempts, registry)
    monkeypatch.setattr(service, "_schedule_cancel_escalation", lambda *_a: None)
    monkeypatch.setattr("openprogram.runtime.execution.run_control.cancellation._canonical_control_service", lambda _id: service)
    state = package.load_goal("bound")
    state.update(status="active")
    package.save_goal("bound", state)
    response = client.post("/api/sessions/bound/goal", json={"action": "pause"}).json()
    assert "not confirmed" in response.get("stop_error", "")
    assert response["execution"]["status"] == "cancelling"
    assert "private" not in str(response)
    saved = package.load_goal("bound")
    driver.fail = False
    retried = client.post("/api/sessions/bound/goal", json={"action": "stop"}).json()
    assert "stop_error" not in retried
    assert driver.calls == [f"execution-cancel:{execution.execution_id}"] * 2
    assert len(store.list_commands(execution.execution_id)) == 1
    assert package.load_goal("bound") == saved
    assert "invoke" not in retried


def test_stop_does_not_target_an_execution_in_another_session(bound_goal, monkeypatch):
    package, store, execution, client = bound_goal
    other = store.create_execution(
        session_id="other", revision_id=execution.revision_id
    )
    state = package.load_goal("bound")
    state.update(status="active", execution_id=other.execution_id)
    package.save_goal("bound", state)
    monkeypatch.setattr(
        "openprogram.runtime.execution.run_control.cancel_execution",
        lambda *_a: pytest.fail("foreign stop"),
    )
    response = client.post("/api/sessions/bound/goal", json={"action": "pause"}).json()
    assert "not confirmed" in response["stop_error"]
    assert store.get_execution(other.execution_id).status is ExecutionStatus.QUEUED


@pytest.mark.parametrize("surface", ["http", "tui"])
def test_stop_reaches_active_descendants_behind_terminal_parents(
    bound_goal, monkeypatch, surface
):
    from openprogram.runtime.execution.attempts import AttemptStore
    from openprogram.runtime.execution.control import RuntimeControlService
    from openprogram.runtime.execution.driver import DriverRegistry

    package, store, parent, client = bound_goal
    child = store.create_execution(
        session_id="bound",
        revision_id=parent.revision_id,
        parent_execution_id=parent.execution_id,
    )
    grandchild = store.create_execution(
        session_id="bound",
        revision_id=parent.revision_id,
        parent_execution_id=child.execution_id,
    )
    for item in (parent, child):
        item = store.transition_execution(
            item.execution_id,
            expected_version=item.status_version,
            target=ExecutionStatus.CANCELLING,
        )
        store.transition_execution(
            item.execution_id,
            expected_version=item.status_version,
            target=ExecutionStatus.CANCELLED,
        )
    service = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    monkeypatch.setattr("openprogram.runtime.execution.run_control.cancellation._canonical_control_service", lambda _id: service)
    state = package.load_goal("bound")
    state.update(status="paused", stop_requested=True)
    package.save_goal("bound", state)
    before = package.load_goal("bound")
    response = (
        client.post("/api/sessions/bound/goal", json={"action": "stop"}).json()
        if surface == "http"
        else package.handle_goal_command("bound", "stop")
    )
    assert "stop_error" not in response and "invoke" not in response
    assert (
        package.goal_execution_state(package.load_goal("bound"), "bound")["finished"]
        is True
    )
    assert package.load_goal("bound") == before
    assert (
        store.get_execution(grandchild.execution_id).status is ExecutionStatus.CANCELLED
    )


def test_terminal_parent_with_active_grandchild_cannot_resume(bound_goal, monkeypatch):
    package, store, parent, client = bound_goal
    from openprogram.programs.tools.planning.goal import chat

    starts = []
    monkeypatch.setattr(
        chat,
        "start_from_controls",
        lambda sid: starts.append(sid) or {"execution_id": "new-chat"},
    )
    child = store.create_execution(
        session_id="bound",
        revision_id=parent.revision_id,
        parent_execution_id=parent.execution_id,
    )
    grandchild = store.create_execution(
        session_id="bound",
        revision_id=parent.revision_id,
        parent_execution_id=child.execution_id,
    )
    for item in (parent, child):
        item = store.transition_execution(
            item.execution_id,
            expected_version=item.status_version,
            target=ExecutionStatus.CANCELLING,
        )
        store.transition_execution(
            item.execution_id,
            expected_version=item.status_version,
            target=ExecutionStatus.CANCELLED,
        )
    response = client.get("/api/sessions/bound/goal").json()
    assert response["execution"]["status"] == "cancelled"
    assert response["execution"]["active_children"] == [grandchild.execution_id]
    assert not response["execution"]["finished"]
    assert (
        client.post("/api/sessions/bound/goal", json={"action": "resume"}).status_code
        == 409
    )
    assert not starts
    grandchild = store.transition_execution(
        grandchild.execution_id,
        expected_version=grandchild.status_version,
        target=ExecutionStatus.CANCELLING,
    )
    store.transition_execution(
        grandchild.execution_id,
        expected_version=grandchild.status_version,
        target=ExecutionStatus.CANCELLED,
    )
    assert (
        client.post("/api/sessions/bound/goal", json={"action": "resume"}).status_code
        == 200
    )
    assert starts == ["bound"]
    assert package.load_goal("bound")["execution_mode"] == "chat"
    assert package.handle_goal_command("bound", "resume")["send_text"] is None


@pytest.mark.parametrize("failure", ["missing", "wrong_session", "storage"])
def test_unverifiable_execution_is_not_treated_as_stopped(
    bound_goal, monkeypatch, failure
):
    package, store, parent, client = bound_goal
    state = package.load_goal("bound")
    if failure == "missing":
        state["execution_id"] = "missing-execution"
        package.save_goal("bound", state)
    elif failure == "wrong_session":
        other = store.create_execution(
            session_id="other", revision_id=parent.revision_id
        )
        state["execution_id"] = other.execution_id
        package.save_goal("bound", state)
    else:

        def unavailable(*_a, **_kw):
            raise OSError("private database details")

        monkeypatch.setattr(store, "get_execution", unavailable)
    before = package.load_goal("bound")
    response = client.post("/api/sessions/bound/goal", json={"action": "resume"})
    assert response.status_code == 409
    assert "private" not in response.text
    assert "invoke" not in package.handle_goal_command("bound", "resume")
    assert package.load_goal("bound") == before


@pytest.mark.parametrize(
    "status", [ExecutionStatus.RUNNING, ExecutionStatus.CANCELLING]
)
def test_live_canonical_states_cannot_resume(bound_goal, status):
    from openprogram.runtime.execution.attempts import AttemptStore

    package, store, execution, client = bound_goal
    attempts = AttemptStore(store)
    lease, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="test",
    )
    _attempt, running = attempts.activate(
        lease.attempt_id,
        generation=lease.generation,
        expected_execution_version=reserved.status_version,
    )
    if status is ExecutionStatus.CANCELLING:
        store.transition_execution(
            running.execution_id, expected_version=running.status_version, target=status
        )
    assert (
        client.get("/api/sessions/bound/goal").json()["execution"]["status"]
        == status.value
    )
    assert (
        client.post("/api/sessions/bound/goal", json={"action": "resume"}).status_code
        == 409
    )

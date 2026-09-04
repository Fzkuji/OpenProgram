"""Goal resume must respect the previous canonical execution."""
import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.agent.session_db import SessionDB
from openprogram.execution.store import ExecutionStore
from openprogram.execution.model import ExecutionStatus


@pytest.fixture
def bound_goal(tmp_path, monkeypatch):
    package = importlib.import_module("openprogram.programs.workflow.goal")
    from openprogram.webui.routes import goal as routes
    db = SessionDB(tmp_path / "sessions")
    db.create_session("bound", "main")
    store = ExecutionStore(tmp_path / "executions.db")
    revision = store.create_revision(manifest={"entrypoint": "goal"})
    execution = store.create_execution(session_id="bound", revision_id=revision.revision_id)
    monkeypatch.setattr(package, "_db", lambda: db)
    monkeypatch.setattr("openprogram.execution.default_store", lambda: store)
    monkeypatch.setattr(package, "_emit_goal_update", lambda *_a: None)
    package.save_goal("bound", {
        "goal_id": "g", "run_id": "g-run", "version": 0,
        "text": "write a survey", "status": "paused", "pause_reason": "user",
        "execution_id": execution.execution_id,
    })
    app = FastAPI(); routes.register(app)
    return package, store, execution, TestClient(app)


def test_http_resume_rejects_a_previous_execution_that_has_not_stopped(bound_goal):
    package, _store, _execution, client = bound_goal
    before = package.load_goal("bound")
    response = client.post("/api/sessions/bound/goal", json={"action": "resume"})
    assert response.status_code == 409
    assert "invoke" not in response.json()
    assert package.load_goal("bound") == before


def test_public_goal_rechecks_execution_before_starting(bound_goal, monkeypatch):
    package, _store, _execution, _client = bound_goal
    function = importlib.import_module("openprogram.agentic_programming.function")
    monkeypatch.setattr(function, "current_session_id", lambda: "bound")
    monkeypatch.setattr(package, "reset_goal_usage_cursor", lambda *_a:
                        pytest.fail("resume started before prior execution stopped"))
    with pytest.raises(ValueError, match="execution"):
        package.goal("ignored", resume=True)


@pytest.mark.parametrize("surface", ["http", "tui"])
def test_answer_saves_without_resuming_an_unfinished_execution(bound_goal, surface):
    package, _store, _execution, client = bound_goal
    state = package.load_goal("bound")
    state.update(status="waiting_user", questions=[{"id": "q1", "prompt": "Scope?", "status": "pending"}])
    package.save_goal("bound", state)
    if surface == "http":
        response = client.post("/api/sessions/bound/goal", json={"action": "answer", "question_id": "q1", "answer": "narrow"})
        assert response.status_code == 200
        result = response.json()
        assert "execution" in result.get("resume_error", "")
    else:
        result = package.handle_goal_command("bound", "answer q1 narrow")
        assert "saved" in result["text"] and "execution" in result["text"]
    assert "invoke" not in result
    assert package.load_goal("bound")["questions"][0]["answer"] == "narrow"


def test_terminal_parent_with_active_grandchild_cannot_resume(bound_goal):
    package, store, parent, client = bound_goal
    child = store.create_execution(session_id="bound", revision_id=parent.revision_id, parent_execution_id=parent.execution_id)
    grandchild = store.create_execution(session_id="bound", revision_id=parent.revision_id, parent_execution_id=child.execution_id)
    for item in (parent, child):
        item = store.transition_execution(item.execution_id, expected_version=item.status_version, target=ExecutionStatus.CANCELLING)
        store.transition_execution(item.execution_id, expected_version=item.status_version, target=ExecutionStatus.CANCELLED)
    response = client.get("/api/sessions/bound/goal").json()
    assert response["execution"]["status"] == "cancelled"
    assert response["execution"]["active_children"] == [grandchild.execution_id]
    assert not response["execution"]["finished"]
    assert client.post("/api/sessions/bound/goal", json={"action": "resume"}).status_code == 409
    grandchild = store.transition_execution(grandchild.execution_id, expected_version=grandchild.status_version, target=ExecutionStatus.CANCELLING)
    store.transition_execution(grandchild.execution_id, expected_version=grandchild.status_version, target=ExecutionStatus.CANCELLED)
    assert client.post("/api/sessions/bound/goal", json={"action": "resume"}).status_code == 200
    assert "invoke" in package.handle_goal_command("bound", "resume")


@pytest.mark.parametrize("failure", ["missing", "wrong_session", "storage"])
def test_unverifiable_execution_is_not_treated_as_stopped(bound_goal, monkeypatch, failure):
    package, store, parent, client = bound_goal
    state = package.load_goal("bound")
    if failure == "missing":
        state["execution_id"] = "missing-execution"
        package.save_goal("bound", state)
    elif failure == "wrong_session":
        other = store.create_execution(session_id="other", revision_id=parent.revision_id)
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


@pytest.mark.parametrize("status", [ExecutionStatus.RUNNING, ExecutionStatus.CANCELLING])
def test_live_canonical_states_cannot_resume(bound_goal, status):
    from openprogram.execution.attempts import AttemptStore
    package, store, execution, client = bound_goal
    attempts = AttemptStore(store)
    lease, reserved = attempts.lease(execution.execution_id, expected_version=execution.status_version, owner_id="test", ttl_seconds=30)
    _attempt, running = attempts.activate(lease.attempt_id, generation=lease.generation, expected_execution_version=reserved.status_version)
    if status is ExecutionStatus.CANCELLING:
        store.transition_execution(running.execution_id, expected_version=running.status_version, target=status)
    assert client.get("/api/sessions/bound/goal").json()["execution"]["status"] == status.value
    assert client.post("/api/sessions/bound/goal", json={"action": "resume"}).status_code == 409


@pytest.mark.parametrize("pause_reason", ["role_unavailable", "user", "edited"])
def test_same_parent_sequential_resume_never_bypasses_a_user_stop(bound_goal, monkeypatch, pause_reason):
    from openprogram.execution.attempts import AttemptStore
    from openprogram.agent.run_control import set_current_execution_id, reset_current_execution_id
    package, store, execution, _client = bound_goal
    attempts = AttemptStore(store)
    lease, reserved = attempts.lease(execution.execution_id, expected_version=execution.status_version, owner_id="test", ttl_seconds=30)
    attempts.activate(lease.attempt_id, generation=lease.generation, expected_execution_version=reserved.status_version)
    state = package.load_goal("bound"); state["pause_reason"] = pause_reason
    package.save_goal("bound", state)
    monkeypatch.setattr("openprogram.agentic_programming.function.current_session_id", lambda: "bound")
    class Entered(Exception):
        pass
    def entered(*_a):
        raise Entered()
    monkeypatch.setattr(package, "reset_goal_usage_cursor", entered)
    token = set_current_execution_id(execution.execution_id)
    try:
        with pytest.raises(Entered if pause_reason == "role_unavailable" else package.GoalConflictError):
            package.goal("ignored", resume=True)
    finally:
        reset_current_execution_id(token)

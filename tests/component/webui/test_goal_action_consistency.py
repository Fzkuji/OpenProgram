"""Public Goal actions reject stale intent before external side effects."""
import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def action_goal(tmp_path, monkeypatch):
    from openprogram.agent.session_db import SessionDB
    from openprogram.webui.routes import goal as routes
    package = importlib.import_module("openprogram.programs.workflow.goal")
    db = SessionDB(tmp_path / "sessions")
    db.create_session("actions", "main")
    from openprogram.execution.store import ExecutionStore
    from openprogram.execution.model import ExecutionStatus
    executions = ExecutionStore(tmp_path / "executions.db")
    revision = executions.create_revision(manifest={"entrypoint": "goal"})
    prior = executions.create_execution(execution_id="exec-a", session_id="actions", revision_id=revision.revision_id)
    prior = executions.transition_execution(prior.execution_id, expected_version=prior.status_version, target=ExecutionStatus.CANCELLING)
    executions.transition_execution(prior.execution_id, expected_version=prior.status_version, target=ExecutionStatus.CANCELLED)
    monkeypatch.setattr("openprogram.execution.default_store", lambda: executions)
    monkeypatch.setattr(package, "_db", lambda: db)
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr(package, "_emit_goal_update", lambda *_a: None)
    package.save_goal("actions", {
        "goal_id": "goal-a", "run_id": "run-a", "revision": 1,
        "text": "write an article", "status": "paused", "version": 0,
        "execution_id": "exec-a",
        "questions": [{"id": "scope", "prompt": "Which scope?", "status": "pending"}],
    })
    app = FastAPI()
    routes.register(app)
    return package, TestClient(app)


@pytest.mark.parametrize("action", ["edit", "cancel", "answer", "resume"])
def test_http_rejects_a_different_goal_identity(action_goal, action):
    package, client = action_goal
    before = package.load_goal("actions")
    response = client.post("/api/sessions/actions/goal", json={
        "action": action, "prompt": "replace", "answer": "scope", "question_id": "scope",
        "expected": {"goal_id": "old-goal", "revision": 1},
    })
    assert response.status_code == 409
    assert package.load_goal("actions") == before


@pytest.mark.parametrize("action", ["edit", "cancel", "answer"])
def test_conflicted_action_does_not_cancel_or_resolve(action_goal, monkeypatch, action):
    package, _client = action_goal
    command = importlib.import_module("openprogram.programs.workflow.goal.command")
    effects = []
    monkeypatch.setattr(command, "_cancel_execution", lambda *_a: effects.append("cancel"))
    save = package.save_goal

    def concurrent_save(sid, value):
        latest = package.load_goal(sid)
        latest["last_reason"] = "concurrent progress"
        save(sid, latest)
        return save(sid, value)

    monkeypatch.setattr(package, "save_goal", concurrent_save)
    with pytest.raises(package.GoalConflictError):
        package.apply_goal_action("actions", action, prompt="edited", answer="answer",
                                  question_id="scope")
    assert effects == []
    assert package.load_goal("actions")["text"] == "write an article"
    assert package.load_goal("actions")["questions"][0]["status"] == "pending"


def test_tui_resume_is_bound_to_the_observed_goal(action_goal, monkeypatch):
    package, _client = action_goal
    function = importlib.import_module("openprogram.agentic_programming.function")
    monkeypatch.setattr(function, "current_session_id", lambda: "actions")
    invocation = package.handle_goal_command("actions", "resume")["invoke"]
    package.apply_goal_action("actions", "edit", prompt="a different article")
    before = package.load_goal("actions")
    calls = []
    monkeypatch.setattr(package, "refine_goal_spec_candidate", lambda *_a, **_k: calls.append("work"))
    monkeypatch.setattr(package, "reset_goal_usage_cursor",
                        lambda *_a: pytest.fail("stale resume started processing"))
    with pytest.raises(package.GoalConflictError):
        package.goal(**invocation["kwargs"])
    assert calls == []
    assert package.load_goal("actions") == before


@pytest.mark.parametrize("expected", [
    {"revision": 2}, {"run_id": "other-run"}, {"version": 0},
    {}, "invalid", {"unknown": "field"},
])
def test_http_rejects_stale_or_malformed_preconditions(action_goal, expected):
    package, client = action_goal
    before = package.load_goal("actions")
    result = client.post("/api/sessions/actions/goal", json={
        "action": "budget", "max_turns": 7, "expected": expected,
    })
    assert result.status_code == 409
    assert package.load_goal("actions") == before


def test_current_request_commits_before_cancel_delivery(action_goal, monkeypatch):
    package, client = action_goal
    command = importlib.import_module("openprogram.programs.workflow.goal.command")
    before = package.load_goal("actions")
    observed = []
    monkeypatch.setattr(command, "_cancel_execution", lambda sid, _goal:
                        observed.append(package.load_goal(sid)["status"]))
    result = client.post("/api/sessions/actions/goal", json={
        "action": "cancel", "expected": {key: before[key] for key in (
            "goal_id", "run_id", "revision", "version",
        )},
    })
    assert result.status_code == 200
    assert observed == ["cancelled"]


def test_goal_read_failure_is_unavailable_not_absent(action_goal, monkeypatch):
    package, client = action_goal
    before = package.load_goal("actions")
    def fail_read(*_args, **_kwargs):
        raise OSError("private database path and details")
    with monkeypatch.context() as patch:
        patch.setattr(package._db(), "get_session", fail_read)
        response = client.get("/api/sessions/actions/goal")
        assert response.status_code == 503
        assert "private" not in response.text
        assert client.post("/api/sessions/actions/goal", json={"action": "cancel"}).status_code == 503
        for args in ("", "resume", "clear", "pause", "answer scope yes"):
            command = package.handle_goal_command("actions", args)
            assert "unavailable" in command["text"].lower()
            assert "private" not in command["text"]
            assert "invoke" not in command
    assert package.load_goal("actions") == before


def test_public_goal_does_not_start_when_saved_state_is_unreadable(action_goal, monkeypatch):
    package, _client = action_goal
    function = importlib.import_module("openprogram.agentic_programming.function")
    monkeypatch.setattr(function, "current_session_id", lambda: "actions")
    monkeypatch.setattr(package, "reset_goal_usage_cursor", lambda *_a:
                        pytest.fail("unreadable Goal started processing"))
    def fail_read(*_a, **_kw):
        raise OSError("private database detail")
    monkeypatch.setattr(package._db(), "get_session", fail_read)
    with pytest.raises(package.GoalStateUnavailable, match="unavailable"):
        package.goal(prompt="original", resume=True)


def test_goal_absence_and_corrupt_record_are_distinct(action_goal):
    package, client = action_goal
    assert client.get("/api/sessions/not-created/goal").status_code == 404
    package._db().update_session("actions", goal="invalid stored object")
    assert client.get("/api/sessions/actions/goal").status_code == 503


def test_failed_goal_terminal_commit_does_not_publish_completion(action_goal, monkeypatch):
    package, _client = action_goal
    before = package.load_goal("actions")
    candidate = {**before, "status": "achieved"}
    events = []
    monkeypatch.setattr(package, "_emit_goal_update", lambda *args: events.append(args))
    monkeypatch.setattr(package, "_emit_goal_notice", lambda *args: events.append(args))
    def fail_commit():
        raise OSError("private database path and details")
    with pytest.raises(RuntimeError, match="unavailable"):
        package._finish("actions", candidate, None, persist=fail_commit)
    assert events == []
    assert package.load_goal("actions") == before


def test_tui_role_and_budget_commands_update_the_existing_goal(action_goal):
    package, _client = action_goal
    result = package.handle_goal_command("actions", 'role work worker writer effort=high timeout_s=17')
    assert "invoke" not in result
    saved = package.load_goal("actions")
    assert saved["role_requests"]["model"] == "worker:writer"
    assert saved["role_requests"]["timeout_s"] == 17
    result = package.handle_goal_command("actions", "budget max_tokens=123 max_turns=2")
    assert "invoke" not in result
    assert package.load_goal("actions")["budget"]["max_tokens"] == 123
    assert "unknown" in package.handle_goal_command("actions", "")["text"].lower()


@pytest.mark.parametrize("status", ["active", "evaluating", "achieved", "cancelled"])
def test_role_edit_rejects_running_or_terminal_goal(action_goal, status):
    package, client = action_goal
    state = package.load_goal("actions")
    state["status"] = status
    package.save_goal("actions", state)
    before = package.load_goal("actions")
    response = client.post("/api/sessions/actions/goal", json={
        "action": "roles", "roles": {"work": {
            "provider": "worker", "model": "writer", "effort": "high", "timeout_s": 17,
        }},
    })
    assert response.status_code == 409
    assert package.load_goal("actions") == before


@pytest.mark.parametrize("patch", [
    {"timeout_s": 0}, {"timeout_s": "nan"}, {"timeout_s": True},
    {"effort": "unsupported"}, {"provider": ""}, {"api_key": "must-not-save"},
])
def test_invalid_role_settings_do_not_change_saved_goal(action_goal, patch):
    package, client = action_goal
    before = package.load_goal("actions")
    response = client.post("/api/sessions/actions/goal", json={
        "action": "roles", "roles": {"work": {
            "provider": "worker", "model": "writer", "effort": "high", "timeout_s": 17, **patch,
        }},
    })
    assert response.status_code == 409
    assert package.load_goal("actions") == before


def test_tui_help_and_zero_budget_do_not_start_a_goal(action_goal):
    package, _client = action_goal
    before = package.load_goal("actions")
    assert "invoke" not in package.handle_goal_command("actions", "help")
    assert package.load_goal("actions") == before
    package.handle_goal_command("actions", "budget max_tokens=0")
    assert package.load_goal("actions")["budget"]["max_tokens"] is None

from __future__ import annotations

import pytest


@pytest.mark.parametrize("source", ["web", "tui", "acp"])
@pytest.mark.parametrize("legacy", [False, True])
def test_chat_runtime_keeps_goal_tool_membership_on_approval_resume(monkeypatch, source, legacy):
    from openprogram.agent import dispatcher
    from openprogram.agent.continuation import runtime_contract_snapshot, validate_runtime_contract
    from openprogram.agent.dispatcher import loop_runner
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.agent.types import AgentTool
    from openprogram.providers.types import Model

    model = Model(id="fake", name="fake", api="openai-completions", provider="openai",
                  base_url="https://example.invalid/v1")
    tools = [AgentTool(name=name, label=name, description=name,
                       parameters={"type": "object"}, execute=lambda *args: None)
             for name in ("goal", "todo_create", "update_goal")]
    monkeypatch.setattr(dispatcher, "_load_agent_profile", lambda *a: {})
    monkeypatch.setattr(dispatcher, "_resolve_model", lambda *a: model)
    monkeypatch.setattr(loop_runner, "_resolve_tools", lambda *a, **k: tools)
    monkeypatch.setattr(loop_runner, "_wrap_with_approval", lambda tool, *a: tool)
    monkeypatch.setattr("openprogram.context.components.build_system_prompt", lambda *a, **k: "system")
    monkeypatch.setattr("openprogram.programs.workflow.goal.chat.instructions", lambda *a: "")
    request = TurnRequest("approval-goal", "calculate", "main", source)
    request._execution_revision_id = "test-revision"
    initial = loop_runner.resolve_agent_runtime(request)[-1]
    assert "goal" not in {tool["name"] for tool in initial["tools"]}
    saved = runtime_contract_snapshot(model=model, system_prompt="system", tools=tools,
                                      request=request) if legacy else initial
    resumed = loop_runner.resolve_agent_runtime(request, saved_runtime_contract=saved)[-1]
    assert ("goal" in {tool["name"] for tool in resumed["tools"]}) is legacy
    validate_runtime_contract(saved, resumed)


@pytest.fixture
def session(tmp_path, monkeypatch):
    from openprogram.agent.session_db import SessionDB
    import openprogram.programs.workflow.goal as goals
    db = SessionDB(tmp_path / "sessions")
    db.create_session("chat-goal", "main")
    monkeypatch.setattr(goals, "_db", lambda: db)
    monkeypatch.setattr(goals, "_emit_goal_update", lambda *a, **k: None)
    monkeypatch.setattr(goals, "goal_usage", lambda *a: {"total_tokens": 0, "cost_usd": 0, "cost_known": True})
    yield goals, db
    db.close()


def test_slash_goal_starts_normal_chat(session):
    goals, _ = session
    result = goals.handle_goal_command("chat-goal", "implement and verify the feature")
    assert "invoke" not in result
    assert result["send_text"]
    goal = goals.load_goal("chat-goal")
    assert goal["execution_mode"] == "chat"
    assert goal["text"] == "implement and verify the feature"


def test_chat_goal_completion_rejects_unfinished_todos(session, monkeypatch):
    from openprogram.programs.workflow.goal import chat
    from openprogram.programs.tools.planning.todo import shared
    goals, _ = session
    goal = chat.create("chat-goal", "implement and verify")
    monkeypatch.setattr(shared, "load", lambda sid: [{
        "id": "1", "subject": "verify", "status": "pending",
        "goal_id": goal["goal_id"], "goal_revision": goal["revision"],
    }])
    with pytest.raises(ValueError, match="todo"):
        chat.update("chat-goal", "complete", expected=chat.identity(goal))
    assert goals.load_goal("chat-goal")["status"] == "active"


def test_old_revision_cannot_complete_new_goal(session):
    from openprogram.programs.workflow.goal import chat
    goals, _ = session
    goal = chat.create("chat-goal", "first objective")
    expected = chat.identity(goal)
    goal["revision"] += 1
    goal["text"] = "updated objective"
    goals.save_goal("chat-goal", goal)
    with pytest.raises(goals.GoalConflictError):
        chat.update("chat-goal", "complete", expected=expected)


def test_budget_exhausted_resume_does_not_reactivate(session):
    from openprogram.programs.workflow.goal import chat
    goals, _ = session
    goal = chat.create("chat-goal", "task", token_budget=10)
    goal.update(status="budget_exhausted", usage={"total_tokens": 10})
    goals.save_goal("chat-goal", goal)
    result = goals.handle_goal_command("chat-goal", "resume")
    assert not result["send_text"]
    assert "budget" in result["text"]
    assert goals.load_goal("chat-goal")["status"] == "budget_exhausted"

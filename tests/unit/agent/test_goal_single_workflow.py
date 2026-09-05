from __future__ import annotations

import importlib
import inspect

import pytest


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("owner", ["exec_goal_owner", None])
def test_goal_records_execution_owner_not_function_call(tmp_path, monkeypatch, resume, owner):
    goal_pkg = importlib.import_module("openprogram.programs.workflow.goal")
    goal_module = importlib.import_module("openprogram.programs.workflow.goal.goal")
    function_module = importlib.import_module("openprogram.agentic_programming.function")
    from openprogram.agent.run_control import (
        reset_current_execution_id, set_current_execution_id,
    )

    monkeypatch.setattr(function_module, "current_call_id", lambda: "function-node")
    previous = {"status": "paused", "text": "test", "goal_id": "g", "run_id": "old",
                "execution_id": "exec_old_owner"}
    from openprogram.execution.store import ExecutionStore
    from openprogram.execution.model import ExecutionStatus
    from openprogram.agent.session_db import SessionDB
    executions = ExecutionStore(tmp_path / "executions.db")
    revision = executions.create_revision(manifest={"entrypoint": "goal"})
    prior = executions.create_execution(execution_id="exec_old_owner", session_id="test-owner", revision_id=revision.revision_id)
    prior = executions.transition_execution(prior.execution_id, expected_version=prior.status_version, target=ExecutionStatus.CANCELLING)
    executions.transition_execution(prior.execution_id, expected_version=prior.status_version, target=ExecutionStatus.CANCELLED)
    monkeypatch.setattr("openprogram.execution.default_store", lambda: executions)
    monkeypatch.setattr(goal_pkg, "_db", lambda: SessionDB(tmp_path / "sessions"))
    monkeypatch.setattr(goal_pkg, "load_goal", lambda _sid: previous)
    monkeypatch.setattr(function_module, "current_session_id", lambda: "test-owner")
    monkeypatch.setattr(goal_pkg, "reset_goal_usage_cursor", lambda *_a: None)
    monkeypatch.setattr(goal_pkg, "save_goal", lambda *_a, **_k: None)
    monkeypatch.setattr(goal_pkg, "_emit_goal_update", lambda *_a, **_k: None)
    monkeypatch.setattr(goal_pkg, "_emit_goal_notice", lambda *_a, **_k: None)
    monkeypatch.setattr(goal_pkg, "render_session_view", lambda *_a: "")
    monkeypatch.setattr(goal_pkg, "refine_goal_spec_candidate", lambda *_a, **_k: ("SPEC", []))
    owners = []

    def stop_at_budget(state):
        owners.append(state["execution_id"])
        return "elapsed_time"

    monkeypatch.setattr(goal_pkg, "budget_exhausted", stop_at_budget)
    token = set_current_execution_id(owner)
    try:
        goal_module.goal("test", resume=resume)
    finally:
        reset_current_execution_id(token)
    assert owners and set(owners) == {owner}


def test_goal_set_builds_the_single_workflow_call_without_writing_state(
    tmp_path, monkeypatch,
) -> None:
    from openprogram.agent.session_db import SessionDB
    import openprogram.programs.workflow.goal as goal_pkg

    db = SessionDB(tmp_path / "sessions-git")
    db.create_session("s1", "main")
    monkeypatch.setattr(goal_pkg, "_db", lambda: db)
    result = goal_pkg.handle_goal_command("s1", "tests pass")

    assert goal_pkg.load_goal("s1") is None
    assert result["invoke"] == {
        "name": "goal",
        "kwargs": {
            "prompt": "tests pass",
            "context_mode": "session",
        },
    }
    assert result["send_text"] is None


def test_one_goal_function_selects_only_the_initial_context(
    monkeypatch,
) -> None:
    goal_module = importlib.import_module(
        "openprogram.programs.workflow.goal.goal"
    )
    goal_pkg = importlib.import_module("openprogram.programs.workflow.goal")
    agent_module = importlib.import_module("openprogram.agentic_programming.agent")
    function_module = importlib.import_module(
        "openprogram.agentic_programming.function"
    )

    monkeypatch.setattr(function_module, "current_session_id", lambda: "s1")
    refine_contexts: list[str] = []

    def fake_refine(*_args, **kwargs):
        refine_contexts.append(kwargs.get("context", ""))
        return "SPEC", []

    monkeypatch.setattr(goal_pkg, "refine_goal_spec_candidate", fake_refine)
    monkeypatch.setattr(goal_pkg, "save_goal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(goal_pkg, "_emit_goal_update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(goal_pkg, "_emit_goal_notice", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(goal_pkg, "render_session_view", lambda _sid: "SESSION VIEW")

    work_prompts: list[str] = []
    judge_views: list[str] = []

    def fake_agent(**kwargs):
        work_prompts.append(kwargs["prompt"])
        return "finished"

    def fake_evaluate(_sid, _goal, *, agent_id, spawn_caller=None,
                      session_view=None):
        judge_views.append(session_view)
        return "met", "done", "", []

    monkeypatch.setattr(agent_module, "agent", fake_agent)
    monkeypatch.setattr(goal_pkg, "evaluate_goal", fake_evaluate)

    assert goal_module.goal(
        "do work", context_mode="isolated", max_rounds=1,
    ) == "finished"
    assert "SESSION VIEW" not in work_prompts[-1]
    assert judge_views[-1] == "[goal work round 1]\nfinished"
    assert refine_contexts[-1] == ""

    assert goal_module.goal(
        "do work", context_mode="session", max_rounds=1,
    ) == "finished"
    assert "SESSION VIEW" in work_prompts[-1]
    assert judge_views[-1].startswith("SESSION VIEW\n")
    assert refine_contexts[-1] == "SESSION VIEW"


def test_goal_program_isolated_history_is_part_of_its_registered_contract() -> None:
    goal_module = importlib.import_module(
        "openprogram.programs.workflow.goal.goal"
    )

    assert goal_module.goal.render_range == {"callers": 0}
    assert goal_module.goal.input_meta["context_mode"]["hidden"] is True


def test_goal_has_one_text_parameter_and_exposes_context_mode() -> None:
    goal_module = importlib.import_module(
        "openprogram.programs.workflow.goal.goal"
    )
    params = goal_module.goal._agent_tool.parameters["properties"]
    assert "prompt" in params
    assert "context_mode" in params
    assert params["context_mode"]["enum"] == ["isolated", "session"]
    assert "condition" not in params
    assert "condition" not in inspect.signature(goal_module.goal).parameters


def test_goal_accepts_numeric_strings_from_programs_run(monkeypatch) -> None:
    goal_pkg = importlib.import_module("openprogram.programs.workflow.goal")
    goal_module = importlib.import_module("openprogram.programs.workflow.goal.goal")
    agent_module = importlib.import_module("openprogram.agentic_programming.agent")
    monkeypatch.setattr(goal_pkg, "refine_goal_spec_candidate", lambda *_a, **_k: ("SPEC", []))
    monkeypatch.setattr(goal_pkg, "evaluate_goal", lambda *_a, **_k: ("met", "done", "", [], False))
    seen: list[dict] = []
    monkeypatch.setattr(
        agent_module,
        "agent",
        lambda **kwargs: seen.append(kwargs) or "finished",
    )

    assert goal_module.goal(
        "do work",
        max_rounds="2",
        max_tokens="20000",
        max_elapsed_s="600",
        max_cost_usd="1.5",
        timeout_s="180",
    ) == "finished"
    assert seen[0]["timeout_s"] == 180.0
    assert seen[0]["tools_deny"] == ["ask_user_question"]
    assert "Human questions are asynchronous" in seen[0]["prompt"]


def test_headless_goal_reports_budget_stop_instead_of_empty_success(monkeypatch) -> None:
    goal_pkg = importlib.import_module("openprogram.programs.workflow.goal")
    goal_module = importlib.import_module("openprogram.programs.workflow.goal.goal")
    function_module = importlib.import_module("openprogram.agentic_programming.function")
    monkeypatch.setattr(function_module, "current_session_id", lambda: "")
    monkeypatch.setattr(goal_pkg, "refine_goal_spec_candidate", lambda *_a, **_k: ("SPEC", []))
    monkeypatch.setattr(goal_pkg, "budget_exhausted", lambda *_a, **_k: "elapsed_time")

    result = goal_module.goal("do work", max_elapsed_s=1)

    assert "budget_exhausted" in result
    assert "elapsed_time" in result

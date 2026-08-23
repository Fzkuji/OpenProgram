from __future__ import annotations

import importlib


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
            "condition": "tests pass",
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
        "do work", "done", context_mode="isolated", max_rounds=1,
    ) == "finished"
    assert "SESSION VIEW" not in work_prompts[-1]
    assert judge_views[-1] == "[goal work round 1]\nfinished"
    assert refine_contexts[-1] == ""

    assert goal_module.goal(
        "do work", "done", context_mode="session", max_rounds=1,
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

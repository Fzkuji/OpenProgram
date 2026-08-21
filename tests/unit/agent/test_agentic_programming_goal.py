from __future__ import annotations

import importlib


def test_goal_workflow_reuses_the_session_judge(monkeypatch) -> None:
    goal_module = importlib.import_module(
        "openprogram.programs.workflow.goal.goal"
    )
    agent_module = importlib.import_module("openprogram.agentic_programming.agent")
    pkg = importlib.import_module("openprogram.programs.workflow.goal")

    monkeypatch.setattr(agent_module, "agent", lambda **_kwargs: "finished")
    monkeypatch.setattr(
        pkg, "judge_goal", lambda **_kwargs: {"met": True, "reason": "done"},
    )

    assert goal_module.goal("do work", "done", max_rounds=1) == "finished"

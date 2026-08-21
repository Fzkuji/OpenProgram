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
        pkg, "refine_goal_spec_candidate", lambda *_args, **_kwargs: ("", []),
    )
    monkeypatch.setattr(
        pkg, "judge_goal", lambda **_kwargs: {"met": True, "reason": "done"},
    )

    assert goal_module.goal("do work", "done", max_rounds=1) == "finished"


def test_callable_goal_stop_rules_match_session_limits() -> None:
    from openprogram.programs.workflow.goal.loop import apply_callable_verdict

    achieved = {"turns_used": 1, "max_turns": 10, "judge_parse_failures": 2}
    assert apply_callable_verdict(achieved, "met", "ok") == "achieved"
    assert achieved["status"] == "achieved"
    assert achieved["judge_parse_failures"] == 0

    failing = {"turns_used": 1, "max_turns": 10, "judge_parse_failures": 2}
    assert apply_callable_verdict(failing, "judge_failure", "bad json") == "error"
    assert failing["status"] == "error"

    capped = {"turns_used": 2, "max_turns": 2, "judge_parse_failures": 0}
    assert apply_callable_verdict(capped, "unmet", "not yet") == "capped"

from __future__ import annotations

import importlib


def test_goal_judgment_uses_valid_json_schema(monkeypatch) -> None:
    goal_module = importlib.import_module("openprogram.agentic_programming.goal")
    agent_module = importlib.import_module("openprogram.agentic_programming.agent")
    llm_module = importlib.import_module("openprogram.agentic_programming.llm")
    calls = []

    monkeypatch.setattr(agent_module, "agent", lambda **_kwargs: "finished")

    def fake_llm(**kwargs):
        calls.append(kwargs)
        return {"met": True, "reason": "done"}

    monkeypatch.setattr(llm_module, "llm", fake_llm)

    assert goal_module.goal("do work", "done", max_rounds=1) == "finished"
    response_format = calls[0]["response_format"]
    assert response_format.name == "goal_judgment"
    assert response_format.schema["required"] == ["met", "reason"]
    assert response_format.schema["additionalProperties"] is False

"""Test three-tier architecture: goal → agent → llm."""
from __future__ import annotations

from pathlib import Path

import pytest

import openprogram.programs.agentic_functions.agentic_workflow as TL


@pytest.fixture
def session_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(TL, "_session_repo", lambda _sid: tmp_path)
    monkeypatch.setattr(TL, "_registered_agentic_functions", lambda: {})
    return tmp_path


def test_workflow_can_call_all_three_tiers(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    """Workflow 程序里可以调用 llm、agent、goal 三层。"""

    def _planner(_sid, _prompt, **_kwargs):
        return """```python
def workflow():
    summary = llm("总结一下任务")
    result = agent("执行任务：" + summary)
    final = goal("优化结果", "测试通过")
    return final
```"""

    llm_calls = []
    agent_calls = []
    goal_calls = []

    def fake_llm(prompt, **kwargs):
        llm_calls.append(prompt)
        return "任务摘要"

    def fake_agent(prompt, **kwargs):
        agent_calls.append(prompt)
        return "执行完成"

    def fake_goal(prompt, condition, **kwargs):
        goal_calls.append((prompt, condition))
        return "优化完成"

    monkeypatch.setattr(TL, "_run_planner_turn", _planner)

    # Mock the factory functions that workflow calls
    monkeypatch.setattr(TL, "_llm_function", lambda: fake_llm)
    monkeypatch.setattr(TL, "_agent_function", lambda _sid, _spawn: fake_agent)
    monkeypatch.setattr(TL, "_goal_function", lambda: fake_goal)

    result = TL.agentic_workflow("测试三层调用", session_id="s1")

    assert result["status"] == "completed"
    assert llm_calls == ["总结一下任务"]
    assert agent_calls == ["执行任务：任务摘要"]
    assert goal_calls == [("优化结果", "测试通过")]
    assert result["summary"] == "优化完成"

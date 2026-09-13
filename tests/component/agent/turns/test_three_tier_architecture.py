"""Test three-tier architecture: goal → agent → llm."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support.workflow_tl import TL


@pytest.fixture
def session_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(TL, "_session_repo", lambda _sid: tmp_path)
    monkeypatch.setattr(TL, "_workflow_projects_root", lambda: tmp_path / "catalog")
    monkeypatch.setattr(TL, "_registered_agentic_functions", lambda: {})
    monkeypatch.setattr(TL, "current_session_id", lambda: "s1")
    monkeypatch.setattr(
        TL, "_summarize_workflow",
        lambda state: {"summary": str(state["result"]), "return_result": False},
    )
    return tmp_path


def test_workflow_can_call_all_three_tiers(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    """Workflow 程序里可以调用 llm、agent、goal 三层。"""

    def _planner(_sid, _prompt, **_kwargs):
        if "<workflow project candidates>" in _prompt:
            return json.dumps({"action": "create"})
        return json.dumps({
            "project_metadata": {
                "name": "three_tier_workflow",
                "summary": "Exercise llm, agent, and goal",
                "tags": ["test"],
            },
            "readme": "# Three tier workflow\n",
            "files": {
                "steps/tiers.py": (
                    "def run_tiers():\n"
                    "    summary = llm('总结一下任务')\n"
                    "    agent('执行任务：' + summary)\n"
                    "    return goal('优化结果，直到测试通过')\n"
                ),
                "__init__.py": (
                    "from .workflow import three_tier_workflow\n\n"
                    "__all__ = ['three_tier_workflow']\n"
                ),
                "workflow.py": (
                    "from openprogram.agentic_programming import agentic_function\n"
                    "from .steps.tiers import run_tiers\n\n"
                    "@agentic_function\n"
                    "def three_tier_workflow(task):\n"
                    "    return run_tiers()\n"
                ),
                "tests/test_workflow.py": (
                    "from workflows.three_tier_workflow import three_tier_workflow\n\n"
                    "def test_three_tier_workflow():\n"
                    "    assert callable(three_tier_workflow)\n"
                ),
            },
        }, ensure_ascii=False)

    llm_calls = []
    agent_calls = []
    goal_calls = []

    def fake_llm(prompt, **kwargs):
        llm_calls.append(prompt)
        return "任务摘要"

    def fake_agent(prompt, **kwargs):
        agent_calls.append(prompt)
        return "执行完成"

    def fake_goal(prompt, **kwargs):
        goal_calls.append(prompt)
        return "优化完成"

    monkeypatch.setattr(TL, "_run_planner_turn", _planner)
    monkeypatch.setattr(TL, "_llm_function", lambda: fake_llm)
    monkeypatch.setattr(TL, "_agent_loop_function", lambda: fake_agent)
    monkeypatch.setattr(TL, "_goal_function", lambda: fake_goal)

    created = TL.create_workflow("测试三层调用")
    result = TL._run_published_workflow(
        "测试三层调用",
        created["workflow_id"],
        created["revision"],
        session_id=TL.current_session_id(),
        spawn_caller=None,
    )

    assert result["status"] == "completed"
    assert llm_calls == ["总结一下任务"]
    assert agent_calls == ["执行任务：任务摘要"]
    assert goal_calls == ["优化结果，直到测试通过"]
    assert result["summary"] == "优化完成"

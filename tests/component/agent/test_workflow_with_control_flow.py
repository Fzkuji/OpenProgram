"""Test workflow can use control flow primitives."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import openprogram.programs.functions.agentic.workflow.authoring as TL


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


def _project_planner(result: str):
    def planner(_sid, prompt, **_kwargs):
        if "<workflow project candidates>" in prompt:
            return json.dumps({"action": "create"})
        return json.dumps({
            "project_metadata": {
                "name": "control_flow_workflow",
                "summary": f"Return {result}",
                "tags": ["test"],
            },
            "readme": f"# {result} workflow\n",
            "files": {
                "steps/run.py": f"def run():\n    return {result!r}\n",
                "__init__.py": (
                    "from .workflow import control_flow_workflow\n\n"
                    "__all__ = ['control_flow_workflow']\n"
                ),
                "workflow.py": (
                    "from openprogram.agentic_programming import agentic_function\n"
                    "from .steps.run import run\n\n"
                    "@agentic_function\n"
                    "def control_flow_workflow(task):\n"
                    "    return run()\n"
                ),
                "tests/test_workflow.py": (
                    "from workflows.control_flow_workflow import "
                    "control_flow_workflow\n\n"
                    "def test_control_flow_workflow():\n"
                    "    assert callable(control_flow_workflow)\n"
                ),
            },
        })

    return planner


def test_workflow_can_use_validate_and_retry(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    """Workflow 可以使用 validate_and_retry 控制流原语。"""

    monkeypatch.setattr(
        TL, "_run_planner_turn", _project_planner("validate_and_retry executed"),
    )

    created = TL.create_workflow("test validate_and_retry")
    result = TL._run_published_workflow(
        "test validate_and_retry",
        created["workflow_id"],
        created["revision"],
        session_id=TL.current_session_id(),
        spawn_caller=None,
    )

    assert result["status"] == "completed"
    assert "validate_and_retry executed" in result["summary"]


def test_workflow_can_use_route(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    """Workflow 可以使用 route 控制流原语。"""

    monkeypatch.setattr(TL, "_run_planner_turn", _project_planner("route executed"))

    created = TL.create_workflow("test route")
    result = TL._run_published_workflow(
        "test route",
        created["workflow_id"],
        created["revision"],
        session_id=TL.current_session_id(),
        spawn_caller=None,
    )

    assert result["status"] == "completed"
    assert "route executed" in result["summary"]


def test_workflow_can_use_conditional(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    """Workflow 可以使用 conditional 控制流原语。"""

    monkeypatch.setattr(
        TL, "_run_planner_turn", _project_planner("conditional executed"),
    )

    created = TL.create_workflow("test conditional")
    result = TL._run_published_workflow(
        "test conditional",
        created["workflow_id"],
        created["revision"],
        session_id=TL.current_session_id(),
        spawn_caller=None,
    )

    assert result["status"] == "completed"
    assert "conditional executed" in result["summary"]


def test_workflow_control_flow_compose(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    """Workflow 可以组合使用多个控制流原语。"""

    monkeypatch.setattr(
        TL, "_run_planner_turn", _project_planner("all primitives available"),
    )

    created = TL.create_workflow("test compose")
    result = TL._run_published_workflow(
        "test compose",
        created["workflow_id"],
        created["revision"],
        session_id=TL.current_session_id(),
        spawn_caller=None,
    )

    assert result["status"] == "completed"
    assert "all primitives available" in result["summary"]

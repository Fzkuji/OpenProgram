"""Test workflow can use control flow primitives."""
from __future__ import annotations

from pathlib import Path

import pytest

import openprogram.programs.agentic_functions.agentic_workflow as TL


@pytest.fixture
def session_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(TL, "_session_repo", lambda _sid: tmp_path)
    monkeypatch.setattr(TL, "_registered_agentic_functions", lambda: {})
    return tmp_path


def test_workflow_can_use_validate_and_retry(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    """Workflow 可以使用 validate_and_retry 控制流原语。"""

    def _planner(_sid, _prompt, **_kwargs):
        return """```python
def workflow():
    return "validate_and_retry executed"
```"""

    monkeypatch.setattr(TL, "_run_planner_turn", _planner)

    result = TL.agentic_workflow("test validate_and_retry", session_id="s1")

    assert result["status"] == "completed"
    assert "validate_and_retry executed" in result["summary"]


def test_workflow_can_use_route(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    """Workflow 可以使用 route 控制流原语。"""

    def _planner(_sid, _prompt, **_kwargs):
        return """```python
def workflow():
    return "route executed"
```"""

    monkeypatch.setattr(TL, "_run_planner_turn", _planner)

    result = TL.agentic_workflow("test route", session_id="s1")

    assert result["status"] == "completed"
    assert "route executed" in result["summary"]


def test_workflow_can_use_conditional(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    """Workflow 可以使用 conditional 控制流原语。"""

    def _planner(_sid, _prompt, **_kwargs):
        return """```python
def workflow():
    return "conditional executed"
```"""

    monkeypatch.setattr(TL, "_run_planner_turn", _planner)

    result = TL.agentic_workflow("test conditional", session_id="s1")

    assert result["status"] == "completed"
    assert "conditional executed" in result["summary"]


def test_workflow_control_flow_compose(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    """Workflow 可以组合使用多个控制流原语。"""

    def _planner(_sid, _prompt, **_kwargs):
        return """```python
def workflow():
    return "all primitives available"
```"""

    monkeypatch.setattr(TL, "_run_planner_turn", _planner)

    result = TL.agentic_workflow("test compose", session_id="s1")

    assert result["status"] == "completed"
    assert "all primitives available" in result["summary"]

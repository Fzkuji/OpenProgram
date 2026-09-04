from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

HARNESS_ROOT = (
    Path(__file__).resolve().parents[3]
    / "openprogram/programs/applications/gui_harness"
)


@pytest.fixture
def harness_on_path():
    if not (HARNESS_ROOT / "gui_harness" / "main.py").is_file():
        pytest.skip("gui_harness checkout is not present")
    root = str(HARNESS_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


def test_legacy_harness_json_parser_imports_resolve():
    from openprogram.programs.agentic_functions._utils import (
        parse_json as parse_from_utils,
    )
    from openprogram.programs.agentic_functions.json_parsing import (
        parse_json as parse_from_module,
    )
    from openprogram.programs.workflow.json_parsing import parse_json

    assert parse_from_utils is parse_json
    assert parse_from_module is parse_json


def _stub_harness_loop(monkeypatch, *, step_result=None, conclusion_result=None):
    """Stub the capability layer so cv2 and a real provider stay unused."""
    import gui_harness.tasks as tasks_package

    capability_loop = types.ModuleType("gui_harness.tasks.capability_loop")
    result_module = types.ModuleType("gui_harness.tasks.result")

    def fake_step(**_kwargs):
        if isinstance(step_result, BaseException):
            raise step_result
        return step_result or {
            "done": True,
            "infeasible": True,
            "plan": {
                "call": "fail",
                "args": {"reasoning": "FAIL/INFEASIBLE need human login"},
            },
        }

    seen = {}

    def fake_conclusion(**kwargs):
        seen.update(kwargs)
        if isinstance(conclusion_result, BaseException):
            raise conclusion_result
        return conclusion_result or {
            "summary": "human should log in", "success": True, "issues": None,
        }

    def fake_call(_name, _args, **_kwargs):
        try:
            step = fake_step()
        except Exception as exc:
            return {
                "status": "failed",
                "success": False,
                "reason_code": "step_error",
                "summary": str(exc),
            }
        if step.get("done"):
            status = str(step.get("terminal_status") or (
                "infeasible" if step.get("infeasible") else "succeeded"
            ))
            plan_args = (step.get("plan") or {}).get("args") or {}
            handoff = str(
                step.get("handoff_instruction")
                or plan_args.get("handoff_instruction")
                or plan_args.get("reasoning")
                or ""
            )
            return {
                "status": status,
                "success": status == "succeeded",
                "reason_code": step.get("reason_code") or status,
                "handoff_instruction": handoff,
                "blocker": step.get("blocker") or "blocked",
                "completion_verified": status == "succeeded",
                "step": step,
            }
        return {
            "status": "applied",
            "success": bool((step.get("exec_result") or {}).get("success")),
            "step": step,
        }

    def fake_plan(*, history, **_kwargs):
        capability_entries = [
            entry for entry in history
            if entry.get("type") == "capability_call"
        ]
        if not capability_entries:
            return {"call": "computer_use", "args": {"task": "test step"}}
        output = capability_entries[-1]["output"]
        if output.get("status") in {"succeeded", "infeasible", "failed"}:
            status = output["status"]
            return {
                "call": "terminal",
                "args": {
                    "status": status,
                    "reason": output.get("reason_code") or status,
                    "reason_code": output.get("reason_code") or status,
                    "blocker": output.get("blocker") or "blocked",
                    "handoff_instruction": output.get("handoff_instruction") or "",
                },
            }
        return {"call": "computer_use", "args": {"task": "test step"}}

    capability_loop.CAPABILITIES = ("computer_use", "browser_use", "vm_use")
    capability_loop.capability_status = lambda **_kwargs: {
        "computer_use": {"available": True},
        "browser_use": {"available": True},
        "vm_use": {"available": False},
    }
    capability_loop.plan_next_capability = fake_plan
    capability_loop.call_capability = fake_call
    capability_loop.validate_terminal_decision = lambda decision, _history: {
        "accepted": True,
        **decision["args"],
    }
    result_module.save_workflow_record = lambda *_args, **_kwargs: None
    result_module.conclusion = fake_conclusion

    monkeypatch.setattr(tasks_package, "capability_loop", capability_loop, raising=False)
    monkeypatch.setitem(sys.modules, "gui_harness.tasks.capability_loop", capability_loop)
    monkeypatch.setitem(sys.modules, "gui_harness.tasks.result", result_module)
    return {"seen": seen}


def test_gui_agent_fail_forces_success_false(harness_on_path, monkeypatch):
    from gui_harness.main import gui_agent

    stubs = _stub_harness_loop(monkeypatch)
    result = gui_agent(task="need login", max_steps=3, runtime=object())
    assert result["success"] is False
    assert result["status"] == "infeasible"
    assert result["infeasible_declared"] is True
    assert result["summary"] == "FAIL/INFEASIBLE need human login"
    assert result["handoff_instruction"] == "FAIL/INFEASIBLE need human login"
    assert stubs["seen"].get("infeasible") is True


def test_gui_agent_real_modules_preserve_infeasible(
    harness_on_path, monkeypatch,
):
    """Import the committed harness module graph before replacing leaf calls."""
    import importlib

    if importlib.util.find_spec("cv2") is None:
        pytest.skip("real GUI harness test requires the optional opencv-python dependency")

    capability_loop = importlib.import_module("gui_harness.tasks.capability_loop")
    result_module = importlib.import_module("gui_harness.tasks.result")
    monkeypatch.setattr(
        capability_loop,
        "gui_step",
        lambda **_kwargs: {
            "done": True,
            "infeasible": True,
            "plan": {
                "call": "fail",
                "args": {"reasoning": "FAIL/INFEASIBLE take over login"},
            },
        },
    )
    monkeypatch.setattr(
        capability_loop,
        "plan_next_capability",
        lambda **kwargs: (
            {"call": "computer_use", "args": {"task": "need login"}}
            if not kwargs["history"]
            else {
                "call": "terminal",
                "args": {
                    "status": "infeasible",
                    "reason": "login required",
                    "blocker": "login required",
                    "handoff_instruction": "FAIL/INFEASIBLE take over login",
                },
            }
        ),
    )
    monkeypatch.setattr(result_module, "save_workflow_record", lambda *_a, **_k: None)
    monkeypatch.setattr(
        result_module,
        "conclusion",
        lambda **_kwargs: {
            "summary": "incorrect optimistic conclusion",
            "success": True,
            "issues": None,
        },
    )

    from gui_harness.main import gui_agent

    result = gui_agent(task="need login", max_steps=1, runtime=object())

    assert result["success"] is False
    assert result["infeasible_declared"] is True
    assert result["handoff_instruction"] == "FAIL/INFEASIBLE take over login"


def test_gui_agent_step_limit_cannot_be_overridden_by_conclusion(
    harness_on_path, monkeypatch,
):
    from gui_harness.main import gui_agent

    _stub_harness_loop(
        monkeypatch,
        step_result={
            "done": False,
            "plan": {"call": "wait", "goal": "wait"},
            "exec_result": {"success": True},
        },
        conclusion_result={"summary": "not finished", "success": True, "issues": None},
    )
    result = gui_agent(task="long task", max_steps=1, runtime=object())

    assert result["status"] == "failed"
    assert result["reason_code"] == "safety_step_limit"
    assert result["success"] is False


def test_gui_agent_preserves_terminal_failure_from_step(harness_on_path, monkeypatch):
    from gui_harness.main import gui_agent

    _stub_harness_loop(
        monkeypatch,
        step_result={
            "done": True,
            "terminal_status": "failed",
            "reason_code": "planner_invalid_action",
            "plan": {"call": "planner_error", "reasoning": "invalid reply"},
        },
        conclusion_result={"summary": "planner failed", "success": True, "issues": None},
    )
    result = gui_agent(task="long task", max_steps=3, runtime=object())

    assert result["status"] == "failed"
    assert result["reason_code"] == "planner_invalid_action"
    assert result["success"] is False


def test_gui_agent_unhandled_step_error_fails_without_retrying(
    harness_on_path, monkeypatch,
):
    from gui_harness.main import gui_agent

    _stub_harness_loop(
        monkeypatch,
        step_result=RuntimeError("detector unavailable"),
        conclusion_result={"summary": "failed", "success": True, "issues": None},
    )
    result = gui_agent(task="long task", max_steps=5, runtime=object())

    assert result["status"] == "failed"
    assert result["reason_code"] == "step_error"
    assert result["success"] is False
    assert result["steps_taken"] == 1


def test_gui_agent_conclusion_error_invalidates_success(harness_on_path, monkeypatch):
    from gui_harness.main import gui_agent

    _stub_harness_loop(
        monkeypatch,
        step_result={
            "done": True,
            "terminal_status": "succeeded",
            "reason_code": "completed",
            "plan": {"call": "done", "reasoning": "visible result"},
            "img_path": "final-screen.png",
        },
        conclusion_result=TimeoutError("screenshot timed out"),
    )
    result = gui_agent(task="describe screen", max_steps=2, runtime=object())

    assert result["status"] == "failed"
    assert result["reason_code"] == "conclusion_error"
    assert result["success"] is False

from __future__ import annotations

import builtins
import sys
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


def test_gui_agent_replans_from_complete_capability_history(
    harness_on_path, monkeypatch,
):
    from gui_harness import main as module
    from gui_harness.tasks import capability_loop
    from gui_harness.tasks import result as result_module

    decisions = iter([
        {"call": "computer_use", "args": {"task": "inspect desktop"}},
        {"call": "browser_use", "args": {"task": "verify page"}},
        {
            "call": "terminal",
            "args": {"status": "succeeded", "reason": "browser verified"},
        },
    ])
    planner_histories = []
    planner_timeouts = []

    def plan(**kwargs):
        planner_histories.append(list(kwargs["history"]))
        planner_timeouts.append(kwargs["timeout_s"])
        return next(decisions)

    monkeypatch.setattr(capability_loop, "plan_next_capability", plan)
    monkeypatch.setattr(
        capability_loop,
        "computer_use",
        lambda **kwargs: {
            "status": "applied",
            "success": True,
            "received": kwargs["task"],
            "max_seconds": kwargs["max_seconds"],
            "next_feedback": {"action": "observe"},
        },
    )
    monkeypatch.setattr(
        capability_loop,
        "browser_use",
        lambda **kwargs: {
            "status": "succeeded",
            "success": True,
            "received": kwargs["task"],
            "reason_code": "verified",
            "completion_verified": True,
        },
    )
    monkeypatch.setattr(
        capability_loop,
        "capability_status",
        lambda **_kwargs: {
            "computer_use": {"available": True},
            "browser_use": {"available": True},
            "vm_use": {"available": False},
        },
    )
    monkeypatch.setattr(result_module, "save_workflow_record", lambda *_a, **_k: None)
    monkeypatch.setattr(
        result_module,
        "conclusion",
        lambda **_kwargs: {"summary": "done", "issues": None},
    )

    result = module.gui_agent(
        task="inspect both surfaces",
        max_steps=6,
        max_seconds=30,
        runtime=object(),
    )

    assert result["status"] == "succeeded"
    assert result["success"] is True
    assert [entry["capability"] for entry in result["history"]] == [
        "computer_use",
        "browser_use",
    ]
    assert planner_histories[0] == []
    assert planner_histories[1] == [result["history"][0]]
    assert planner_histories[2] == result["history"]
    assert all(0 < timeout <= 30 for timeout in planner_timeouts)
    assert result["history"][0]["input"] == {"task": "inspect desktop"}
    assert 0 < result["history"][0]["output"]["max_seconds"] <= 30
    assert result["history"][1]["output"]["reason_code"] == "verified"


def test_gui_controller_import_does_not_require_desktop_perception(
    harness_on_path, monkeypatch,
):
    imported = {
        name: module
        for name, module in sys.modules.items()
        if name == "gui_harness" or name.startswith("gui_harness.")
    }
    for name in imported:
        monkeypatch.delitem(sys.modules, name, raising=False)

    real_import = builtins.__import__

    def reject_perception(name, *args, **kwargs):
        if name.split(".")[0] in {"cv2", "ultralytics"}:
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_perception)
    try:
        from gui_harness.tasks import capability_loop, result

        assert callable(capability_loop.browser_use)
        assert callable(result.conclusion)
    finally:
        for name in tuple(sys.modules):
            if name == "gui_harness" or name.startswith("gui_harness."):
                sys.modules.pop(name)
        sys.modules.update(imported)


def test_capability_status_reports_missing_perception_dependencies(harness_on_path, monkeypatch):
    from gui_harness.tasks import capability_loop as module

    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda _name: None)
    status = module.capability_status(vm_url="http://localhost:5000")
    assert status["computer_use"]["available"] is False
    assert status["vm_use"]["available"] is False
    assert status["computer_use"]["missing_dependencies"] == ["cv2", "ultralytics"]


def test_gui_step_does_not_dispatch_after_planner_deadline(harness_on_path, monkeypatch):
    from gui_harness.tasks import execute_task as module

    now = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(module, "observe_screen", lambda *_: {
        "img_path": "unused.png", "component_info": "", "current_state": None,
        "transitions_info": "",
    })
    def late_plan(**_kwargs):
        now[0] = 2.0
        return {"call": "key_press", "args": {"key": "enter"}}
    monkeypatch.setattr(module, "plan_next_action", late_plan)
    dispatched = []
    monkeypatch.setattr(module, "dispatch_action", lambda *a, **k: dispatched.append(a))
    with pytest.raises(TimeoutError):
        module.gui_step(task="press enter", feedback=None, app_name="desktop",
                        runtime=object(), timeout_s=1)
    assert not dispatched


def test_browser_conclusion_uses_recorded_evidence_without_host_capture(
    harness_on_path, monkeypatch,
):
    from gui_harness import main as module
    from gui_harness.tasks import capability_loop, result as result_module

    decisions = iter([
        {"call": "browser_use", "args": {"task": "read page title"}},
        {"call": "terminal", "args": {
            "status": "succeeded", "reason": "Title verified: Example Domain",
        }},
    ])
    monkeypatch.setattr(capability_loop, "plan_next_capability", lambda **_: next(decisions))
    monkeypatch.setattr(capability_loop, "capability_status", lambda **_: {
        "browser_use": {"available": True},
    })
    monkeypatch.setattr(capability_loop, "browser_use", lambda **_: {
        "status": "succeeded", "completion_verified": True,
        "summary": "Title verified: Example Domain",
    })
    def forbidden_capture(*_args, **_kwargs):
        raise AssertionError("browser conclusion must not capture the host desktop")
    monkeypatch.setattr(result_module._screenshot, "take", forbidden_capture)
    prompts = []
    monkeypatch.setattr(result_module, "llm", lambda content, **_: (
        prompts.append(content) or '{"summary":"Example Domain","issues":null}'
    ))
    monkeypatch.setattr(result_module, "save_workflow_record", lambda *_a, **_k: None)

    result = module.gui_agent(task="read page title", runtime=object())

    assert result["status"] == "succeeded"
    assert result["summary"] == "Example Domain"
    assert "Title verified: Example Domain" in str(prompts)
    assert all(block["type"] != "image" for block in prompts[0])


def test_gui_agent_rejects_unsupported_success_terminal(
    harness_on_path, monkeypatch,
):
    from gui_harness import main as module
    from gui_harness.tasks import capability_loop
    from gui_harness.tasks import result as result_module

    decisions = iter([
        {
            "call": "terminal",
            "args": {"status": "succeeded", "reason": "assumed complete"},
        },
        {
            "call": "terminal",
            "args": {
                "status": "infeasible",
                "reason": "no usable surface",
                "blocker": "all capabilities unavailable",
                "handoff_instruction": "Open a surface and retry.",
            },
        },
    ])
    seen_histories = []

    def plan(**kwargs):
        seen_histories.append(list(kwargs["history"]))
        return next(decisions)

    monkeypatch.setattr(capability_loop, "plan_next_capability", plan)
    monkeypatch.setattr(
        capability_loop,
        "capability_status",
        lambda **_kwargs: {
            name: {"available": False}
            for name in ("computer_use", "browser_use", "vm_use")
        },
    )
    monkeypatch.setattr(result_module, "save_workflow_record", lambda *_a, **_k: None)
    monkeypatch.setattr(
        result_module,
        "conclusion",
        lambda **kwargs: {
            "summary": kwargs["handoff_instruction"], "issues": None,
        },
    )

    result = module.gui_agent(task="do work", max_steps=3, runtime=object())

    assert result["status"] == "infeasible"
    assert result["success"] is False
    assert result["handoff_instruction"] == "Open a surface and retry."
    assert seen_histories[1][0]["type"] == "terminal_rejected"


def test_gui_agent_records_capability_error_as_failed_result(
    harness_on_path, monkeypatch,
):
    from gui_harness import main as module
    from gui_harness.tasks import capability_loop
    from gui_harness.tasks import result as result_module

    decisions = iter([
        {"call": "computer_use", "args": {"task": "inspect desktop"}},
        {
            "call": "terminal",
            "args": {
                "status": "failed",
                "reason": "desktop operation timed out",
            },
        },
    ])
    monkeypatch.setattr(
        capability_loop, "plan_next_capability", lambda **_kwargs: next(decisions),
    )
    monkeypatch.setattr(
        capability_loop,
        "call_capability",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("late")),
    )
    monkeypatch.setattr(
        capability_loop,
        "capability_status",
        lambda **_kwargs: {
            "computer_use": {"available": True},
            "browser_use": {"available": False},
            "vm_use": {"available": False},
        },
    )
    monkeypatch.setattr(result_module, "save_workflow_record", lambda *_a, **_k: None)
    monkeypatch.setattr(
        result_module,
        "conclusion",
        lambda **_kwargs: {"summary": "failed", "issues": None},
    )

    result = module.gui_agent(task="inspect", max_steps=2, runtime=object())

    output = result["history"][0]["output"]
    assert result["status"] == "failed"
    assert result["success"] is False
    assert output["reason_code"] == "capability_operation_failed"
    assert output["error_type"] == "TimeoutError"


def test_gui_agent_skips_conclusion_after_deadline(
    harness_on_path, monkeypatch,
):
    from gui_harness import main as module
    from gui_harness.tasks import capability_loop
    from gui_harness.tasks import result as result_module

    clock = iter([0.0, 0.0, 0.0, 0.0, 2.0, 2.0, 2.0])
    monkeypatch.setattr(module.time, "time", lambda: next(clock, 2.0))
    monkeypatch.setattr(
        capability_loop,
        "plan_next_capability",
        lambda **_kwargs: {
            "call": "computer_use", "args": {"task": "inspect"},
        },
    )
    monkeypatch.setattr(
        capability_loop,
        "call_capability",
        lambda *_args, **_kwargs: {
            "status": "applied", "success": True,
        },
    )
    monkeypatch.setattr(
        capability_loop,
        "capability_status",
        lambda **_kwargs: {
            "computer_use": {"available": True},
            "browser_use": {"available": False},
            "vm_use": {"available": False},
        },
    )
    monkeypatch.setattr(result_module, "save_workflow_record", lambda *_a, **_k: None)
    monkeypatch.setattr(
        result_module,
        "conclusion",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("conclusion must not run after deadline")
        ),
    )

    result = module.gui_agent(
        task="inspect", max_steps=2, max_seconds=1, runtime=object(),
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == "timeout"
    assert result["summary"] == "GUI Agent exceeded its Runtime time limit."
    assert "Conclusion skipped" in result["issues"]


def test_gui_agent_rejects_terminal_returned_after_deadline(
    harness_on_path, monkeypatch,
):
    from gui_harness import main as module
    from gui_harness.tasks import capability_loop
    from gui_harness.tasks import result as result_module

    clock = iter([0.0, 0.0, 0.0, 2.0, 2.0, 2.0])
    monkeypatch.setattr(module.time, "time", lambda: next(clock, 2.0))
    monkeypatch.setattr(
        capability_loop,
        "plan_next_capability",
        lambda **_kwargs: {
            "call": "terminal",
            "args": {"status": "succeeded", "reason": "late success"},
        },
    )
    monkeypatch.setattr(
        capability_loop,
        "capability_status",
        lambda **_kwargs: {
            "computer_use": {"available": True},
            "browser_use": {"available": False},
            "vm_use": {"available": False},
        },
    )
    monkeypatch.setattr(result_module, "save_workflow_record", lambda *_a, **_k: None)
    monkeypatch.setattr(
        result_module,
        "conclusion",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("conclusion must not run after deadline")
        ),
    )

    result = module.gui_agent(
        task="inspect", max_steps=1, max_seconds=1, runtime=object(),
    )

    assert result["status"] == "failed"
    assert result["success"] is False
    assert result["reason_code"] == "timeout"


def test_capability_status_does_not_expose_vm_endpoint_credentials(
    harness_on_path,
):
    from gui_harness.tasks.capability_loop import capability_status

    status = capability_status(
        vm_url="http://user:secret@127.0.0.1:5000?token=private",
    )

    assert status["vm_use"] == {
        "available": True,
        "target": "configured VM",
        "missing_dependencies": [],
    }
    assert "secret" not in repr(status)
    assert "private" not in repr(status)


def test_capability_planner_passes_deadline_to_llm(harness_on_path, monkeypatch):
    from gui_harness.tasks import capability_loop

    calls = []
    monkeypatch.setattr(
        capability_loop,
        "llm",
        lambda prompt, **kwargs: calls.append((prompt, kwargs)) or (
            '{"call":"computer_use","args":{"task":"inspect"}}'
        ),
    )

    decision = capability_loop.plan_next_capability(
        task="inspect",
        history=[],
        availability={"computer_use": {"available": True}},
        timeout_s=12.5,
    )

    assert decision["call"] == "computer_use"
    assert calls[0][1] == {"timeout_s": 12.5}


def test_internal_capabilities_are_not_registered_as_public_tools(
    harness_on_path,
):
    from gui_harness.tasks import capability_loop  # noqa: F401
    from openprogram.programs import agent_tools

    names = {
        tool.name
        for tool in agent_tools(
            names=[
                "plan_next_capability",
                "computer_use",
                "browser_use",
                "vm_use",
            ],
        )
    }

    assert names == set()


def test_vm_use_restores_local_input_and_screenshot_backend(
    harness_on_path, monkeypatch,
):
    from gui_harness.action import input as input_module
    from gui_harness.adapters import vm_adapter
    from gui_harness.perception import screenshot
    from gui_harness.tasks import capability_loop

    original_take = screenshot.take
    original_take_window = screenshot.take_window
    monkeypatch.setattr(vm_adapter, "_vm_screenshot", lambda _url, path: path)
    monkeypatch.setattr(
        capability_loop,
        "gui_step",
        lambda **_kwargs: {
            "done": False,
            "plan": {"call": "click", "args": {"target": "OK"}},
            "exec_result": {"success": True},
        },
    )

    result = capability_loop.vm_use(
        task="click OK",
        vm_url="http://127.0.0.1:5000",
        runtime=object(),
    )

    assert result["status"] == "applied"
    assert input_module.get_default_name() == "local"
    assert screenshot.take is original_take
    assert screenshot.take_window is original_take_window


def test_vm_target_session_restores_backends_after_error(
    harness_on_path, monkeypatch,
):
    from gui_harness.action import input as input_module
    from gui_harness.adapters import vm_adapter
    from gui_harness.perception import screenshot

    original_take = screenshot.take
    original_take_window = screenshot.take_window
    monkeypatch.setattr(vm_adapter, "_vm_screenshot", lambda _url, path: path)

    with pytest.raises(RuntimeError, match="stop"):
        with vm_adapter.target_session("http://127.0.0.1:5000"):
            assert input_module.get_default_name() == "vm"
            raise RuntimeError("stop")

    assert input_module.get_default_name() == "local"
    assert screenshot.take is original_take
    assert screenshot.take_window is original_take_window


def test_vm_target_session_restores_previous_vm_configuration(
    harness_on_path,
):
    from gui_harness.action import input as input_module
    from gui_harness.adapters import vm_adapter

    input_module.configure(vm_url="http://previous-vm:5000")
    previous_target = input_module.get_target()
    try:
        with vm_adapter.target_session("http://temporary-vm:5000"):
            assert input_module.get_target().url == "http://temporary-vm:5000"
        assert input_module.get_target() is previous_target
        assert input_module._backend == "vm"
        assert input_module._vm_url == "http://previous-vm:5000"
    finally:
        input_module.configure()


def test_browser_use_delegates_to_background_page_runtime(
    harness_on_path, monkeypatch,
):
    from gui_harness.tasks import capability_loop
    from openprogram.programs.workflow import browser as browser_module

    calls = []
    monkeypatch.setattr(
        browser_module,
        "_run_browser_task_commands",
        lambda **kwargs: calls.append(kwargs) or {
            "status": "succeeded",
            "reason_code": "verified",
            "summary": "page verified",
        },
    )
    runtime = object()

    result = capability_loop.browser_use(
        task="verify the current page title",
        backend="chrome_devtools_mcp",
        max_steps=4,
        max_seconds=30,
        runtime=runtime,
    )

    assert result["status"] == "succeeded"
    assert result["success"] is True
    assert result["completion_verified"] is True
    assert calls == [{
        "task": "verify the current page title",
        "backend": "chrome_devtools_mcp",
        "max_steps": 4,
        "max_seconds": 30,
        "runtime": runtime,
    }]


def test_bridge_delegates_surface_choice_to_unified_harness(monkeypatch):
    from openprogram.programs.gui_harness_bridge import install_gui_harness_web_use

    calls = []

    def harness(**kwargs):
        calls.append(kwargs)
        return {"status": "succeeded", "summary": "done"}

    wrapped = install_gui_harness_web_use(harness)
    result = wrapped(
        task="inspect the current UI",
        surface="browser",
        backend="chrome_devtools_mcp",
        vm_url="http://vm:5000",
        runtime=object(),
    )

    assert result["success"] is True
    assert len(calls) == 1
    assert calls[0]["preferred_capability"] == "browser_use"
    assert calls[0]["browser_backend"] == "chrome_devtools_mcp"
    assert calls[0]["vm_url"] == "http://vm:5000"

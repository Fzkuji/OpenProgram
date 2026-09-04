from __future__ import annotations

import inspect


def test_agent_forwards_bounded_tool_loop_controls_and_can_return_raw_result():
    from openprogram.agentic_programming.agent import agent

    calls = []

    class Runtime:
        def exec(self, **kwargs):
            calls.append(kwargs)
            return {"text": "finished", "tool_result": {"ok": True}}

    tool = object()
    result = agent(
        "perform one browser action",
        tools=[tool],
        tool_choice={"type": "function", "name": "browser_page"},
        parallel_tool_calls=False,
        max_iterations=1,
        timeout_s=30,
        execution_kind="browser_agent",
        runtime=Runtime(),
        return_raw=True,
    )

    assert result == {"text": "finished", "tool_result": {"ok": True}}
    assert calls == [{
        "content": [{"type": "text", "text": "perform one browser action"}],
        "model": None,
        "tools": [tool],
        "tool_choice": {"type": "function", "name": "browser_page"},
        "parallel_tool_calls": False,
        "max_iterations": 1,
        "timeout_s": 30,
        "effort": None,
        "execution_kind": "browser_agent",
    }]


def test_agent_keeps_text_return_contract_by_default():
    from openprogram.agentic_programming.agent import agent

    class Runtime:
        def exec(self, **_kwargs):
            return {"text": "finished", "other": "metadata"}

    assert agent("work", runtime=Runtime()) == "finished"


def test_browser_task_uses_high_level_agent_wrapper(monkeypatch):
    from types import SimpleNamespace

    from openprogram.programs.workflow import browser as browser_module

    class Controller:
        initial_url = ""
        binding_id = ""
        max_steps = 0
        _action_seq = 0
        _planner_screenshot_result = None
        _frame = {"frame_id": "frame-1", "url": "http://localhost/"}
        _last_result = None
        _last_action = ""

        def execute(self, **_kwargs):
            return {"frame_id": "frame-1", "url": "http://localhost/"}

        def tool_for_actions(self, _actions):
            return SimpleNamespace(name="browser_page")

        def revoke_screenshot(self):
            return None

        def final_result(self, *, summary: str, reason_code: str | None = None):
            return {
                "status": "failed",
                "reason_code": reason_code or "verification_missing",
                "summary": summary,
            }

        def close(self):
            return None

    calls = []

    def high_level_agent(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return "no tool call"

    class Runtime:
        def exec(self, **_kwargs):
            raise AssertionError("browser task must use agent(), not Runtime.exec()")

    monkeypatch.setattr(browser_module, "_new_controller", Controller)
    monkeypatch.setattr(browser_module, "agent", high_level_agent)

    result = browser_module._run_browser_task(
        task="inspect the page",
        url="",
        max_steps=1,
        max_seconds=30,
        runtime=Runtime(),
    )

    assert result["status"] == "failed"
    assert len(calls) == 6
    assert calls[0][1]["tools"][0].name == "browser_page"
    assert calls[0][1]["tool_choice"] == {
        "type": "function", "name": "browser_page",
    }
    assert calls[0][1]["runtime"].__class__ is Runtime


def test_browser_gui_loops_do_not_call_runtime_exec_directly():
    from openprogram.programs.workflow import browser as browser_module

    source = "\n".join([
        inspect.getsource(browser_module._run_browser_task_commands),
        inspect.getsource(browser_module._run_browser_task),
    ])

    assert "runtime.exec(" not in source

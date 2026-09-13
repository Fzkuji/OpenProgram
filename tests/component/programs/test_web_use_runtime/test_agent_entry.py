"""web use agent entry tests."""
from __future__ import annotations
from ._support import (
    SimpleNamespace,
    asyncio,
)


def test_registered_gui_agent_can_select_computer_use_backend(monkeypatch):
    from openprogram.programs import _runtime
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_web_use,
    )

    calls = []

    def original(**kwargs):
        calls.append(("original", kwargs))
        return {"status": "succeeded", "mode": "unified"}
    wrapped = install_gui_harness_web_use(original)
    tool = _runtime.get("gui_agent")
    assert tool is not None

    result = wrapped(
        task="click Save", backend="chrome_devtools_mcp",
        runtime=SimpleNamespace(),
    )
    assert result["status"] == "infeasible"
    assert result["success"] is False
    assert result["reason_code"] == "guarded_dispatch_unsupported"
    assert calls == []



def test_programs_cli_resolves_registered_gui_agent(monkeypatch, capsys):
    from openprogram.agentic_programming.function import _registry
    from openprogram.cli.commands.programs import _cmd_run
    from openprogram.programs import gui_harness_bridge
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_web_use,
    )

    monkeypatch.setitem(_registry, "gui_agent", _registry.get("gui_agent"))
    monkeypatch.setattr(
        gui_harness_bridge,
        "gui_agent",
        getattr(gui_harness_bridge, "gui_agent", None),
        raising=False,
    )

    wrapped = install_gui_harness_web_use(
        lambda **_kwargs: {
            "status": "succeeded",
            "success": True,
            "summary": "inspected",
        },
    )

    _cmd_run("gui_agent", ["task=inspect"])

    assert gui_harness_bridge.gui_agent is wrapped
    assert "'status': 'succeeded'" in capsys.readouterr().out



def test_registered_gui_agent_browser_surface_uses_standard_entry(monkeypatch):
    from openprogram.programs import gui_browser_agent
    from openprogram.programs.gui_harness_bridge import DEFAULT_MAX_STEPS, install_gui_harness_web_use
    calls = []
    def standard_entry(**kwargs):
        calls.append(kwargs)
        return {"status": "succeeded", "reason_code": "verified_browser_assertion", "summary": "done"}
    monkeypatch.setattr(gui_browser_agent, "run_browser_gui_agent", standard_entry)
    def original(**kwargs):
        raise AssertionError("browser must not use the legacy planner")
    runtime = object()
    wrapped = install_gui_harness_web_use(original)
    result = wrapped(task="inspect the page", surface="browser", runtime=runtime)
    assert result["success"] is True
    assert calls == [{"task": "inspect the page", "max_steps": DEFAULT_MAX_STEPS,
                      "max_seconds": None, "runtime": runtime, "allow_general": False, "backend": ""}]



def test_gui_agent_app_name_does_not_select_browser_surface(monkeypatch):
    from openprogram.programs.workflow import browser as browser_module
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_web_use,
    )

    calls = []

    def original(**kwargs):
        calls.append(kwargs)
        return {"status": "succeeded", "summary": "desktop"}

    monkeypatch.setattr(
        browser_module,
        "_run_browser_task_commands",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("app_name must not select the browser route")
        ),
    )

    wrapped = install_gui_harness_web_use(original)
    result = wrapped(task="inspect", app_name="browser", runtime=object())

    assert result["success"] is True
    assert calls[0]["app_name"] == "browser"



def test_gui_agent_wrapper_resolves_step_budget():
    from openprogram.programs.gui_harness_bridge import (
        DEFAULT_MAX_STEPS,
        install_gui_harness_web_use,
    )

    seen = []

    def original(**kwargs):
        seen.append(kwargs)
        return {"ok": True}

    wrapped = install_gui_harness_web_use(original)
    wrapped(task="t")
    assert seen[-1]["max_steps"] == DEFAULT_MAX_STEPS
    wrapped(task="t", max_steps=0)
    assert seen[-1]["max_steps"] == 0
    wrapped(task="t", max_steps=-3)
    assert seen[-1]["max_steps"] == 0
    wrapped(task="t", max_steps=20)
    assert seen[-1]["max_steps"] == 20



def test_gui_agent_wrapper_forces_success_false_when_infeasible_declared():
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_web_use,
    )

    def original(**kwargs):
        return {
            "status": "succeeded",
            "infeasible_declared": True,
            "success": True,
            "summary": "Human must log in and retry.",
        }

    wrapped = install_gui_harness_web_use(original)
    result = wrapped(task="t")
    assert result["success"] is False
    assert result["status"] == "infeasible"
    assert result["infeasible_declared"] is True
    assert result["handoff_instruction"] == "Human must log in and retry."
    assert result["summary"] == "Human must log in and retry."



def test_gui_agent_wrapper_calls_raw_harness_function_once():
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_web_use,
    )

    calls = []

    def raw_harness(**kwargs):
        calls.append(kwargs)
        return {"success": True, "summary": "done"}

    def decorated_harness(**_kwargs):
        raise AssertionError("bridge called the decorated harness wrapper")

    decorated_harness.__wrapped__ = raw_harness
    wrapped = install_gui_harness_web_use(decorated_harness)

    result = wrapped(task="t")
    assert result["status"] == "succeeded"
    assert result["success"] is True
    assert result["summary"] == "done"
    assert len(calls) == 1



def test_gui_agent_wrapper_records_one_public_gui_agent_node(tmp_path):
    from openprogram.agentic_programming.function import agentic_function
    from openprogram.agentic_programming.runtime import Runtime
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_web_use,
    )
    from openprogram.store import SessionNodeWriter, SessionStore, _store

    @agentic_function
    def gui_step(task, runtime=None):
        return {"task": task, "success": True, "summary": "done"}

    @agentic_function
    def gui_agent(task, runtime=None, **_kwargs):
        return gui_step(task, runtime=runtime)

    store = SessionStore(tmp_path / "sessions")
    store.create_session("s1", agent_id="main")
    writer = SessionNodeWriter(store, "s1")
    token = _store.set(writer)
    try:
        wrapped = install_gui_harness_web_use(gui_agent)
        wrapped(task="t", runtime=Runtime(call=lambda *_a, **_k: "", model="dummy"))
    finally:
        _store.reset(token)

    names = [node.name for node in writer.load() if node.is_code()]
    assert names.count("gui_agent") == 1
    assert names.count("gui_step") == 1



def test_gui_agent_harness_uses_selected_computer_use_backend(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import (
        web_use_runtime,
    )

    class _Registry:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["command"] == "observe":
                return {
                    "ok": True, "frame_id": "frame-1",
                    "web_session_id": "cs-1",
                    "backend": kwargs.get("backend") or "chrome_devtools_mcp",
                }
            if kwargs["command"] == "verify":
                return {"ok": True, "passed": True, "backend": "chrome_devtools_mcp"}
            return {"ok": True}

    registry = _Registry()
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    context = {
        "context_id": "ctx-1",
        "surfaces": [{
            "binding_id": "binding-1",
            "capabilities": ["observe", "interact", "navigate"],
        }],
    }
    monkeypatch.setattr(surface_context, "current", lambda: context)
    monkeypatch.setattr(surface_context, "resolve_binding", lambda _page="": "binding-1")
    monkeypatch.setattr(surface_context, "resolve_page_key", lambda _page="": "page-1")

    class _Runtime:
        def exec(self, **kwargs):
            tool = kwargs["tools"][0]
            asyncio.run(tool.execute(
                "call-1",
                {
                    "action": "verify", "expected_frame_id": "frame-1",
                    "page_context_token": "pct-current",
                    "assertion": "text_contains", "value": "done",
                },
                asyncio.Event(),
                None,
            ))
            return "verified"

    result = module.browser_agent(
        task="Verify the page",
        backend="chrome_devtools_mcp",
        runtime=_Runtime(),
    )
    assert result["status"] == "succeeded"
    verify_call = next(
        call for call in registry.calls if call["command"] == "verify"
    )
    assert "page_context_token" not in verify_call["arguments"]
    assert registry.calls[0] == {
        "command": "observe",
        "backend": "chrome_devtools_mcp",
        "binding_id": "binding-1",
        "page_key": "page-1",
        "owner_id": "harness:ctx-1",
        "page_context": context,
    }
    assert registry.calls[-1] == {
        "command": "close", "web_session_id": "cs-1",
        "owner_id": "harness:ctx-1",
    }



def test_gui_agent_prompt_receives_group_aware_page_inventory(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import (
        web_use_runtime,
    )

    inventory_context = {
        "context_id": "ctx-pages",
        "window_id": "window-1",
        "inventory_revision": 9,
        "active_tab_entry_id": "group:g3",
        "focused_page": "p4",
        "tab_entries": [{
            "id": "group:g3", "mode": "split", "pages": ["p3", "p4"],
        }],
        "windows": [
            {
                "window_id": "window-1", "inventory_revision": 9,
                "active_tab_entry_id": "group:g3", "focused_page": "p4",
                "tab_entries": [{
                    "id": "group:g3", "mode": "split", "pages": ["p3", "p4"],
                }],
                "pages": ["p3", "p4"],
            },
            {
                "window_id": "window-2", "inventory_revision": 4,
                "active_tab_entry_id": "tab:tab-d", "focused_page": "p5",
                "tab_entries": [{
                    "id": "tab:tab-d", "mode": "single", "pages": ["p5"],
                }],
                "pages": ["p5"],
            },
        ],
        "surfaces": [],
    }

    class _Registry:
        def list_pages(self, **_kwargs):
            return {
                "ok": True,
                "browser_context_id": "ctx-pages",
                "window_id": "window-1",
                "inventory_revision": 9,
                "active_tab_entry_id": "group:g3",
                "focused_page": "p4",
                "tab_entries": inventory_context["tab_entries"],
                "windows": inventory_context["windows"],
                "pages": [
                    {"page": "p3", "window_id": "window-1", "tab_id": "tab-c", "visible": True, "focused": False, "page_context_token": "pct_3"},
                    {"page": "p4", "window_id": "window-1", "tab_id": "tab-d", "visible": True, "focused": True, "page_context_token": "pct_4"},
                    {"page": "p5", "window_id": "window-2", "tab_id": "tab-d", "visible": True, "focused": True, "page_context_token": "pct_5"},
                ],
            }

        def execute(self, **kwargs):
            if kwargs["command"] == "observe":
                return {
                    "ok": True, "frame_id": "frame-1",
                    "web_session_id": "cs-1",
                }
            if kwargs["command"] == "verify":
                return {"ok": True, "passed": True}
            return {"ok": True, "closed": True}

    registry = _Registry()
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: inventory_context)
    monkeypatch.setattr(
        surface_context, "capture_pages", lambda _context=None: inventory_context,
    )
    prompts = []

    class _Runtime:
        def exec(self, **kwargs):
            prompts.append(kwargs["content"][0]["text"])
            asyncio.run(kwargs["tools"][0].execute(
                "call-1",
                {
                    "action": "verify", "expected_frame_id": "frame-1",
                    "assertion": "text_contains", "value": "done",
                },
                asyncio.Event(),
                None,
            ))
            return "verified"

    result = module._run_browser_task_commands(
        task="Verify the split page", backend="chrome_devtools_mcp",
        max_steps=2, max_seconds=30, runtime=_Runtime(),
    )

    assert result["status"] == "succeeded"
    assert '"active_tab_entry_id": "group:g3"' in prompts[0]
    assert '"pages": ["p3", "p4"]' in prompts[0]
    assert '"page": "p4"' in prompts[0]
    assert '"window_id": "window-2"' in prompts[0]
    assert '"page": "p5"' in prompts[0]
    assert prompts[0].count('"bound": true') == 1


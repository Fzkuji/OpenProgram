from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


class _Adapter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, dict]] = []

    def observe(self, session, arguments):
        self.calls.append(("observe", dict(arguments)))
        return {"frame_id": "frame-1", "backend": self.name}

    def act(self, session, arguments):
        self.calls.append(("act", dict(arguments)))
        return {"ok": True, "backend": self.name}

    def verify(self, session, arguments):
        self.calls.append(("verify", dict(arguments)))
        return {"passed": True, "backend": self.name}

    def close(self, session):
        self.calls.append(("close", {}))


def test_registry_supports_three_backends_and_freezes_one_per_session():
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSessionRegistry,
        SUPPORTED_BACKENDS,
    )

    assert SUPPORTED_BACKENDS == (
        "playwright_mcp",
        "chrome_devtools_mcp",
        "open_claude_chrome",
    )
    adapters = {name: _Adapter(name) for name in SUPPORTED_BACKENDS}
    registry = ComputerUseSessionRegistry(adapters=adapters)

    observed = registry.execute(
        command="observe",
        backend="chrome_devtools_mcp",
        binding_id="binding-1",
        arguments={"detail": "interactive"},
    )
    session_id = observed["computer_session_id"]
    assert observed["backend"] == "chrome_devtools_mcp"

    mismatch = registry.execute(
        command="act",
        backend="playwright_mcp",
        computer_session_id=session_id,
        arguments={"action": "click", "ref": "e1"},
    )
    assert mismatch == {
        "ok": False,
        "reason_code": "backend_mismatch",
        "computer_session_id": session_id,
        "backend": "chrome_devtools_mcp",
    }
    assert adapters["playwright_mcp"].calls == []

    acted = registry.execute(
        command="act",
        computer_session_id=session_id,
        arguments={"action": "click", "ref": "e1"},
    )
    assert acted["ok"] is True
    assert acted["backend"] == "chrome_devtools_mcp"
    assert adapters["chrome_devtools_mcp"].calls == [
        ("observe", {"detail": "interactive"}),
        ("act", {"action": "click", "ref": "e1"}),
    ]


def test_public_computer_use_schema_is_command_based():
    from openprogram.programs import agent_tools

    tool = next(
        item for item in agent_tools(names=["computer_use"])
        if item.name == "computer_use"
    )
    properties = tool.parameters["properties"]
    assert properties["command"]["enum"] == [
        "list_pages", "observe", "act", "verify", "close",
    ]
    assert properties["backend"]["enum"] == [
        "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
    ]
    assert "task" not in properties
    assert tool.parameters["required"] == ["command"]


def test_openprogram_mcp_exposes_computer_use_as_a_first_class_tool():
    from openprogram.mcp_server.contracts import get_mcp_tools

    tools = {tool.name: tool for tool in get_mcp_tools()}
    assert "computer_use" in tools
    schema = tools["computer_use"].inputSchema
    assert schema["properties"]["command"]["enum"] == [
        "list_pages", "observe", "act", "verify", "close",
    ]


class _Page:
    def __init__(self) -> None:
        self.marker_name = ""
        self.marker_value = ""

    def evaluate(self, script, arg=None):
        if "Object.defineProperty" in script:
            self.marker_name, self.marker_value = arg
        return None


class _Controller:
    def __init__(self) -> None:
        self.binding_id = ""
        self.page = _Page()
        self.frame = {
            "frame_id": "frame-1", "url": "https://example.test/",
            "target": {"tab_id": "tab-1", "target_id": "target-1"},
        }
        self.invalidated = 0
        self.closed = 0

    def _page(self):
        return self.page

    def execute(self, **params):
        if params["action"] == "observe":
            return dict(self.frame)
        return {"passed": True}

    def _require_fresh(self, frame_id):
        return None if frame_id == self.frame["frame_id"] else {
            "ok": False, "reason_code": "stale_observation",
        }

    def _invalidate_frame(self):
        self.invalidated += 1

    def _write_allowed(self):
        return None

    def _mutated(self, detail):
        self.invalidated += 1
        return {"ok": True, "detail": detail, "observe_required": True}

    def close(self):
        self.closed += 1


class _Result:
    def __init__(self, text="", structured=None, error=False) -> None:
        self.content = [SimpleNamespace(text=text)] if text else []
        self.structuredContent = structured
        self.isError = error


class _PlaywrightClient:
    def __init__(self, page: _Page) -> None:
        self.page = page
        self.selected = 0
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name == "browser_snapshot":
            return _Result('- button "Save" [ref=e7]')
        return _Result("done")

    def close(self):
        pass


def test_official_playwright_adapter_binds_marker_and_routes_one_action(monkeypatch):
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSession,
    )
    from openprogram.programs.agentic_functions.browser_agent.mcp_backends import (
        OfficialMCPPageBackend,
    )
    from openprogram.programs.functions.browser import _chrome_bootstrap

    controller = _Controller()
    client = _PlaywrightClient(controller.page)
    monkeypatch.setattr(_chrome_bootstrap, "desktop_app_ws_url", lambda: "ws://cdp")
    commands = []
    adapter = OfficialMCPPageBackend(
        "playwright_mcp", lambda: controller,
        client_factory=lambda command: commands.append(command) or client,
    )
    session = ComputerUseSession("cs-1", "playwright_mcp", "binding-1")

    observed = adapter.observe(session, {})
    assert observed["aria_snapshot"] == '- button "Save" [ref=e7]'
    assert session.state["upstream_page"] == 0
    assert session.state["target_id"] == "target-1"
    assert all(name != "browser_tabs" for name, _ in client.calls)
    assert "target-1" in commands[0]
    assert not any("bringToFront" in part for part in commands[0])
    acted = adapter.act(session, {
        "action": "click", "expected_frame_id": "frame-1", "ref": "e7",
    })
    assert acted["ok"] is True
    assert ("browser_click", {"target": "e7"}) in client.calls
    assert controller.invalidated == 1


def test_registry_binds_session_to_owner_and_consumes_exact_page_capability():
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSessionRegistry,
    )

    adapters = {name: _Adapter(name) for name in (
        "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
    )}
    released = []
    registry = ComputerUseSessionRegistry(
        adapters=adapters,
        release_context=lambda context: released.append(context["context_id"]),
    )
    context = {
        "context_id": "ctx-1",
        "surfaces": [{
            "surface_key": "p1", "binding_id": "binding-1",
            "capabilities": ["observe", "interact"],
        }],
    }
    pages = registry.list_pages(context=context, owner_id="mcp:connection-a")
    token = pages["pages"][0]["page_context_token"]

    denied = registry.execute(
        command="observe", backend="open_claude_chrome",
        owner_id="mcp:connection-b", page_context_token=token,
    )
    assert denied["reason_code"] == "page_context_owner_mismatch"

    observed = registry.execute(
        command="observe", backend="open_claude_chrome",
        owner_id="mcp:connection-a", page_context_token=token,
    )
    session_id = observed["computer_session_id"]
    reused = registry.execute(
        command="observe", backend="open_claude_chrome",
        owner_id="mcp:connection-a", page_context_token=token,
    )
    assert reused["reason_code"] == "page_context_consumed"
    wrong_owner = registry.execute(
        command="act", computer_session_id=session_id,
        owner_id="mcp:connection-b", arguments={"action": "click"},
    )
    assert wrong_owner["reason_code"] == "computer_session_owner_mismatch"
    closed = registry.execute(
        command="close", computer_session_id=session_id,
        owner_id="mcp:connection-a",
    )
    assert closed["closed"] is True
    assert released == ["ctx-1"]


def test_registered_gui_agent_can_select_computer_use_backend(monkeypatch):
    from openprogram.programs import _runtime
    from openprogram.programs.agentic_functions import browser_agent as browser_module
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_computer_use,
    )

    calls = []

    def original(**kwargs):
        calls.append(("original", kwargs))
        return {"mode": "desktop"}

    monkeypatch.setattr(
        browser_module,
        "_run_browser_task_commands",
        lambda **kwargs: calls.append(("computer_use", kwargs)) or {
            "mode": "computer_use", "backend": kwargs["backend"],
        },
    )
    wrapped = install_gui_harness_computer_use(original)
    tool = _runtime.get("gui_agent")
    assert tool is not None
    assert tool.parameters["properties"]["backend"]["enum"] == [
        "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
    ]

    result = wrapped(
        task="click Save", backend="chrome_devtools_mcp",
        runtime=SimpleNamespace(),
    )
    assert result == {"mode": "computer_use", "backend": "chrome_devtools_mcp"}
    assert calls[0][0] == "computer_use"


def test_official_backend_rejects_stale_frame_before_upstream_call(monkeypatch):
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSession,
    )
    from openprogram.programs.agentic_functions.browser_agent.mcp_backends import (
        OfficialMCPPageBackend,
    )

    controller = _Controller()
    client = _PlaywrightClient(controller.page)
    adapter = OfficialMCPPageBackend(
        "playwright_mcp", lambda: controller,
        client_factory=lambda _command: client,
    )
    session = ComputerUseSession("cs-1", "playwright_mcp", "binding-1")
    session.controller = controller
    session.state["mcp_client"] = client
    before = list(client.calls)
    result = adapter.act(session, {
        "action": "click", "expected_frame_id": "old", "ref": "e7",
    })
    assert result["reason_code"] == "stale_observation"
    assert client.calls == before


def test_gui_agent_harness_uses_selected_computer_use_backend(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.agentic_functions import browser_agent as module
    from openprogram.programs.agentic_functions.browser_agent import (
        computer_use_runtime,
    )

    class _Registry:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["command"] == "observe":
                return {
                    "ok": True, "frame_id": "frame-1",
                    "computer_session_id": "cs-1",
                    "backend": kwargs.get("backend") or "chrome_devtools_mcp",
                }
            if kwargs["command"] == "verify":
                return {"ok": True, "passed": True, "backend": "chrome_devtools_mcp"}
            return {"ok": True}

    registry = _Registry()
    monkeypatch.setattr(computer_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: {"surfaces": [{}]})
    monkeypatch.setattr(surface_context, "resolve_binding", lambda _page="": "binding-1")

    class _Runtime:
        def exec(self, **kwargs):
            tool = kwargs["tools"][0]
            asyncio.run(tool.execute(
                "call-1",
                {
                    "action": "verify", "expected_frame_id": "frame-1",
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
    assert registry.calls[0]["backend"] == "chrome_devtools_mcp"
    assert registry.calls[-1] == {
        "command": "close", "computer_session_id": "cs-1",
    }


def test_gui_harness_screenshot_capability_is_one_request_only(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs import ToolReturn
    from openprogram.programs.agentic_functions import browser_agent as module
    from openprogram.programs.agentic_functions.browser_agent import (
        computer_use_runtime,
    )

    class _Registry:
        def __init__(self):
            self.revoked = 0

        def execute(self, **kwargs):
            command = kwargs["command"]
            if command == "observe":
                return {"frame_id": "f1", "computer_session_id": "cs1"}
            if command == "act" and kwargs["arguments"]["action"] == "screenshot":
                return ToolReturn(images=[b"png"], json_data={"frame_id": "f1"})
            if command == "verify":
                return {"passed": True}
            return {"ok": True}

        def revoke_screenshot(self, _session_id):
            self.revoked += 1

    registry = _Registry()
    monkeypatch.setattr(computer_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: {"context_id": "ctx"})
    monkeypatch.setattr(surface_context, "resolve_binding", lambda _page="": "b1")

    class _Runtime:
        def __init__(self):
            self.requests = []

        def exec(self, **kwargs):
            self.requests.append([block["type"] for block in kwargs["content"]])
            index = len(self.requests)
            if index == 1:
                asyncio.run(kwargs["tools"][0].execute(
                    "c1", {"action": "screenshot", "expected_frame_id": "f1"},
                    asyncio.Event(), None,
                ))
            elif index == 3:
                asyncio.run(kwargs["tools"][0].execute(
                    "c3", {
                        "action": "verify", "expected_frame_id": "f1",
                        "assertion": "text_contains", "value": "done",
                    }, asyncio.Event(), None,
                ))
            return ""

    runtime = _Runtime()
    result = module._run_browser_task_commands(
        task="visual task", backend="open_claude_chrome",
        max_steps=1, max_seconds=30, runtime=runtime,
    )
    assert result["status"] == "succeeded"
    assert runtime.requests == [["text"], ["text", "image"], ["text"]]
    assert registry.revoked == 1

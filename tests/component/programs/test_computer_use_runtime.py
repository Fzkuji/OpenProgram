from __future__ import annotations

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
        if name == "browser_tabs" and arguments["action"] == "list":
            return _Result("- 0: [Chat](about:blank)\n- 1: [Web](https://example.test/)")
        if name == "browser_tabs":
            self.selected = arguments["index"]
            return _Result("selected")
        if name == "browser_evaluate":
            return _Result(self.page.marker_value if self.selected == 1 else "null")
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
    adapter = OfficialMCPPageBackend(
        "playwright_mcp", lambda: controller,
        client_factory=lambda _command: client,
    )
    session = ComputerUseSession("cs-1", "playwright_mcp", "binding-1")

    observed = adapter.observe(session, {})
    assert observed["aria_snapshot"] == '- button "Save" [ref=e7]'
    assert session.state["upstream_page"] == 1
    acted = adapter.act(session, {
        "action": "click", "expected_frame_id": "frame-1", "ref": "e7",
    })
    assert acted["ok"] is True
    assert ("browser_click", {"target": "e7"}) in client.calls
    assert controller.invalidated == 1


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

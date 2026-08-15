from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class _WS:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


def test_surface_context_captures_preview_from_the_originating_socket(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui.ws_actions import webtab

    ws = _WS()

    def request(bound_ws, command, timeout=5.0):
        assert bound_ws is ws
        assert command == {
            "op": "preview", "window_id": "window-1", "tab_id": "w:right",
        }
        return {
            "ok": True,
            "window_id": "window-1",
            "tab_id": "w:right",
            "target_id": "target-right",
            "url": "https://example.com/path?token=secret",
            "title": "Right page",
            "preview": {
                "visible_text_excerpt": "Visible page text",
                "aria_landmarks": [{"role": "main", "name": "Main"}],
                "interactive_count": 4,
            },
        }

    monkeypatch.setattr(webtab, "request_on_ws", request)
    context = surface_context.capture({
        "version": 1,
        "window_id": "window-1",
        "tab_id": "w:right",
        "region": "right",
        "access": "enabled",
    }, ws)

    assert context["primary_surface_key"] == "s1"
    assert context["alias_map"]["right"] == "s1"
    assert context["surfaces"][0]["preview"]["visible_text_excerpt"] == "Visible page text"
    assert context["surfaces"][0]["origin"] == "https://example.com"
    assert "token=secret" not in surface_context.render_for_model(context)
    assert "preview_status" in surface_context.render_for_model(context)
    assert "do not claim that the page is invisible" in surface_context.render_for_model(context)
    binding_id = context["surfaces"][0]["binding_id"]
    assert webtab._bindings[binding_id][2] == "w:right"
    webtab.release_binding(binding_id)


def test_disabled_surface_is_visible_to_model_but_has_no_preview_or_binding():
    from openprogram.agent import surface_context

    context = surface_context.capture({
        "version": 1,
        "window_id": "window-1",
        "tab_id": "w:right",
        "region": "right",
        "access": "disabled",
        "title": "Example",
        "url": "https://example.com/private?q=secret",
    }, _WS())

    surface = context["surfaces"][0]
    assert surface["capabilities"] == []
    assert surface["preview_status"] == "disabled"
    assert "binding_id" not in surface
    assert surface_context.tool_enabled(context) is False
    rendered = surface_context.render_for_model(context)
    assert "Agent access is disabled" in rendered
    assert "q=secret" not in rendered


def test_bound_webtab_request_uses_only_the_registered_socket(monkeypatch):
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    binding_id = webtab.register_binding(
        owner, "window-1", "w:right", "target-right",
    )
    seen = []
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda ws, command, timeout=5.0: seen.append((ws, command)) or {
            "ok": True,
            "window_id": "window-1",
            "tab_id": command.get("tab_id"),
            "target_id": "target-right",
        },
    )

    assert webtab.request_bound_tab(binding_id)["ok"] is True
    assert seen == [(owner, {
        "op": "activate", "window_id": "window-1", "tab_id": "w:right",
    })]
    webtab.release_binding(binding_id)
    assert webtab.request_bound_tab(binding_id)["ok"] is False


def test_webtab_result_is_claimed_only_by_expected_socket():
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    other = _WS()
    event = threading.Event()
    holder: dict = {}
    webtab._pending["req"] = (event, holder, owner)

    asyncio.run(webtab.handle_webtab_result(other, {
        "req_id": "req", "ok": True, "tab_id": "wrong",
    }))
    assert holder == {}
    asyncio.run(webtab.handle_webtab_result(owner, {
        "req_id": "req", "ok": True, "window_id": "window-1", "tab_id": "right",
    }))
    assert holder["result"]["tab_id"] == "right"
    assert holder["result"]["window_id"] == "window-1"
    webtab._pending.clear()


def test_frontend_and_electron_expose_turn_surface_preview_contract():
    send = (REPO_ROOT / "web/components/chat/composer/legacy-send.ts").read_text()
    bridge = (REPO_ROOT / "web/lib/desktop-bridge.ts").read_text()
    preload = (REPO_ROOT / "desktop/preload.js").read_text()
    main = (REPO_ROOT / "desktop/main.js").read_text()
    chip = (REPO_ROOT / "web/components/chat/composer/surface-chip.tsx").read_text()

    assert "surfaceRefForChat(sessionId, toolsEnabled)" in send
    assert "payload.surface = surface" in send
    assert "export function surfaceRefForChat" in bridge
    assert 'd.op === "preview"' in bridge
    assert "webTab.preview(tab.id)" in bridge
    assert 'preview: (id) => ipcRenderer.invoke("webtab:preview", id)' in preload
    assert 'ipcMain.handle("webtab:preview"' in main
    assert "visible_text_excerpt" in main
    assert "Agent can access" in chip
    assert "surfaceRefForChat(sessionId, toolsEnabled)" in chip


def test_computer_use_is_registered_as_surface_aware_public_tool():
    from openprogram.programs import (
        DEFERRED_DEFAULT_TOOLS,
        DEFAULT_TOOLS,
        agent_tools,
        apply_tool_policy,
    )

    assert "computer_use" in DEFAULT_TOOLS
    assert "computer_use" not in DEFERRED_DEFAULT_TOOLS
    tool = next(item for item in agent_tools(names=["computer_use"]) if item.name == "computer_use")
    assert "surface" in tool.parameters["properties"]
    assert "pattern" not in tool.parameters["properties"]["url"]
    assert apply_tool_policy([tool], source="plan") == []


def test_surface_tool_is_injected_after_tools_are_resolved():
    source = (REPO_ROOT / "openprogram/agent/dispatcher/loop_runner.py").read_text()

    resolve_at = source.index(
        "tools = _resolve_tools(agent_profile, req.tools_override, source=req.source)"
    )
    inject_at = source.index('if not any(tool.name == "computer_use"')
    assert resolve_at < inject_at


def test_bound_browser_task_bypasses_only_the_nested_default_ask(monkeypatch):
    from types import SimpleNamespace

    from openprogram.agent.dispatcher import TurnRequest
    from openprogram.agent.turn_request_context import (
        get_turn_request,
        reset_turn_request,
        set_turn_request,
    )
    from openprogram.programs.agentic_functions import browser_agent as module

    class _Controller:
        tool = SimpleNamespace(name="browser_page")
        initial_url = ""
        binding_id = ""
        max_steps = 0
        _terminal_reason = ""
        _frame = {"frame_id": "frame-1", "url": "http://localhost/"}
        _last_result = None

        def execute(self, **_kwargs):
            return self._frame

        def tool_for_actions(self, _actions):
            return self.tool

        def final_result(self, *, summary: str, reason_code: str | None = None):
            return {
                "status": "failed",
                "reason_code": reason_code or "verification_missing",
                "summary": summary,
            }

        def close(self):
            return None

    controller = _Controller()
    monkeypatch.setattr(module, "_new_controller", lambda: controller)

    seen_modes = []

    class _Runtime:
        def exec(self, **_kwargs):
            seen_modes.append(get_turn_request().permission_mode)
            return "not verified"

    outer = TurnRequest(
        session_id="session-1",
        user_text="",
        agent_id="main",
        source="web",
        permission_mode="ask",
    )
    token = set_turn_request(outer)
    try:
        module._run_browser_task(
            task="Click the link",
            url="",
            max_steps=3,
            max_seconds=30,
            runtime=_Runtime(),
            binding_id="binding-1",
        )
        assert len(seen_modes) == 12
        assert set(seen_modes) == {"bypass"}
        assert get_turn_request() is outer
    finally:
        reset_turn_request(token)


def test_turn_surface_grant_allows_only_computer_use_after_rules(monkeypatch):
    from types import SimpleNamespace

    from openprogram.agent.authority import local_owner_authority
    from openprogram.agent.dispatcher import TurnRequest
    from openprogram.agent.internals._approval import wrap_with_approval
    from openprogram.agent.types import AgentTool, AgentToolResult
    from openprogram.providers.types import TextContent

    calls = []

    async def execute(_call_id, _args, _cancel, _on_update):
        calls.append("executed")
        return AgentToolResult(content=[TextContent(text="ok")])

    tool = AgentTool(
        name="computer_use",
        description="Bound in-app web control",
        parameters={"type": "object"},
        label="computer_use",
        execute=execute,
    )
    req = TurnRequest(
        session_id="session-1",
        user_text="",
        agent_id="main",
        source="web",
        permission_mode="ask",
        surface_context={
            "surfaces": [{
                "surface_key": "s1",
                "binding_id": "surface-1",
                "capabilities": ["observe", "interact"],
            }],
        },
        **local_owner_authority(),
    )

    async def unexpected_approval(**_kwargs):
        raise AssertionError("unexpected approval")

    monkeypatch.setattr(
        "openprogram.agent.internals._approval.await_user_approval",
        unexpected_approval,
    )

    result = asyncio.run(
        wrap_with_approval(tool, req, lambda _event: None).execute(
            "call-1", {"task": "click"}, SimpleNamespace(), lambda _event: None,
        )
    )

    assert calls == ["executed"]
    assert result.is_error is False


def test_subprocess_permission_snapshot_denies_nested_browser_page_before_bypass():
    from dataclasses import replace
    from types import SimpleNamespace

    from openprogram.agent.authority import local_owner_authority
    from openprogram.agent.dispatcher import TurnRequest
    from openprogram.agent.internals._approval import wrap_with_approval
    from openprogram.agent.process_runner import _permission_rules_from_snapshot
    from openprogram.agent.types import AgentTool, AgentToolResult
    from openprogram.providers.types import TextContent

    calls = []

    async def execute(_call_id, _args, _cancel, _on_update):
        calls.append("executed")
        return AgentToolResult(content=[TextContent(text="ok")])

    rules = _permission_rules_from_snapshot({
        "allow": [], "deny": ["browser_page"], "ask": [],
    })
    child_request = replace(TurnRequest(
        session_id="session-1",
        user_text="",
        agent_id="main",
        source="web",
        permission_rules=rules,
        **local_owner_authority(),
    ), permission_mode="bypass")
    tool = AgentTool(
        name="browser_page",
        description="Nested browser action",
        parameters={"type": "object"},
        label="browser_page",
        execute=execute,
    )

    result = asyncio.run(
        wrap_with_approval(tool, child_request, lambda _event: None).execute(
            "call-1", {"action": "click"}, SimpleNamespace(), lambda _event: None,
        )
    )

    assert calls == []
    assert result.is_error is True
    assert result.details["reason_code"] == "PERMISSION_RULE_DENY"


def test_chat_query_owner_always_releases_captured_surface_bindings():
    source = (REPO_ROOT / "openprogram/webui/_execute/chat.py").read_text()
    capture_at = source.index("surface_context = _capture_surface")
    finally_at = source.index("finally:", capture_at)
    release_at = source.index("_release_surface_bindings(surface_context)", finally_at)
    finish_at = source.index("_s._finish_owned_run", finally_at)

    assert capture_at < finally_at < release_at < finish_at


def test_electron_bound_surface_control_does_not_focus_the_app_window():
    source = (REPO_ROOT / "desktop/main.js").read_text()
    start = source.index("async function activateView")
    end = source.index("const SURFACE_PREVIEW_SCRIPT", start)
    activate_source = source[start:end]

    assert "devToolsTargetId(record.view.webContents)" in activate_source
    assert ".focus(" not in activate_source
    assert "BrowserWindow.getFocusedWindow" not in activate_source

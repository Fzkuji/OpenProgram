"""surface surface contracts tests."""
from __future__ import annotations
import asyncio
import pytest
import threading
from ._support import _WS


def test_webtab_connection_and_target_replacement_advance_page_revision(monkeypatch):
    from openprogram.interfaces.server.ws_actions import webtab

    owner = _WS()
    first = webtab.register_binding(owner, "window-1", "tab-1", "target-1")
    first_key = webtab.binding_page_key(first)
    monkeypatch.setattr(webtab, "request_on_ws", lambda *_args, **_kwargs: {
        "ok": True,
        "window_id": "window-1",
        "tab_id": "tab-1",
        "target_id": "target-replaced",
    })
    changed = webtab.request_bound_tab(first)
    assert changed["reason_code"] == "page_context_stale"
    assert first not in webtab._bindings

    replacement = webtab.register_binding(
        owner, "window-1", "tab-1", "target-1",
    )
    replacement_key = webtab.binding_page_key(replacement)
    assert replacement_key != first_key

    webtab.release_connection(owner)
    reconnected = webtab.register_binding(
        owner, "window-1", "tab-1", "target-1",
    )
    assert webtab.binding_page_key(reconnected) != replacement_key
    webtab.release_connection(owner)



def test_webtab_result_is_claimed_only_by_expected_socket():
    from openprogram.interfaces.server.ws_actions import webtab

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



def test_open_page_preserves_parent_cleanup_failure(monkeypatch):
    from openprogram.runtime.conversation import surface_context
    from openprogram.interfaces.server.ws_actions import webtab

    monkeypatch.setenv("OPENPROGRAM_IN_AGENTIC_SUBPROCESS", "1")
    monkeypatch.setattr(webtab, "_request", lambda *_args, **_kwargs: {
        "ok": False,
        "status": "infeasible",
        "success": False,
        "infeasible_declared": True,
        "reason_code": "page_cleanup_failed",
        "error": "close rejected",
        "handoff_instruction": "Close the remaining background Page.",
    })

    result = surface_context.open_page(
        "https://example.test/", window_id="window-1", background=True,
    )

    assert result["status"] == "infeasible"
    assert result["success"] is False
    assert result["infeasible_declared"] is True
    assert result["reason_code"] == "page_cleanup_failed"
    assert result["handoff_instruction"] == (
        "Close the remaining background Page."
    )



@pytest.mark.parametrize("open_result", [
    {
        "ok": True,
        "window_id": "window-2",
        "tab_id": "tab-unusable",
        "target_id": "target-unusable",
        "created": True,
        "reused": False,
    },
    {
        "ok": True,
        "tab_id": "tab-unusable",
        "target_id": "target-unusable",
        "created": True,
        "reused": False,
    },
    {
        "ok": True,
        "window_id": "window-1",
        "tab_id": "tab-unusable",
        "created": True,
        "reused": False,
    },
])
def test_open_page_rolls_back_unusable_success_result(
    monkeypatch, open_result,
):
    from openprogram.runtime.conversation import surface_context
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions import webtab

    owner = _WS()
    sent = []
    monkeypatch.setattr(server, "_ws_connections", [owner])
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: [(owner, "window-1", 7)],
    )

    def request(_ws, command, timeout=15.0):
        sent.append((command, timeout))
        if command["op"] == "open":
            return open_result
        return {"ok": True, "tab_id": command["tab_id"]}

    monkeypatch.setattr(webtab, "request_on_ws", request)
    monkeypatch.setattr(
        webtab,
        "register_binding",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an unusable Page must not be bound")
        ),
    )

    result = surface_context.open_page(
        "https://example.test/", window_id="window-1", background=True,
    )

    assert result["ok"] is False
    assert result["reason_code"] == "page_context_stale"
    assert sent == [
        ({
            "op": "open",
            "url": "https://example.test/",
            "window_id": "window-1",
            "background": True,
        }, 15.0),
        ({
            "op": "close",
            "window_id": "window-1",
            "tab_id": "tab-unusable",
        }, 15.0),
    ]



@pytest.mark.parametrize("close_results", [[False, True], [False, False]])
def test_open_page_retries_rejected_identity_rollback(
    monkeypatch, close_results,
):
    from openprogram.runtime.conversation import surface_context
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions import webtab

    owner = _WS()
    monkeypatch.setattr(server, "_ws_connections", [owner])
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: [(owner, "window-1", 7)],
    )
    close_calls = []

    def request(_ws, command, timeout=15.0):
        del timeout
        if command["op"] == "open":
            return {
                "ok": True,
                "window_id": "window-2",
                "tab_id": "tab-unusable",
                "target_id": "target-unusable",
                "created": True,
                "reused": False,
            }
        close_calls.append(command["tab_id"])
        succeeded = close_results[len(close_calls) - 1]
        return {"ok": succeeded, "error": "close rejected"}

    monkeypatch.setattr(webtab, "request_on_ws", request)

    result = surface_context.open_page(
        "https://example.test/", window_id="window-1", background=True,
    )

    assert result["ok"] is False
    assert close_calls == ["tab-unusable", "tab-unusable"]
    if close_results[-1]:
        assert result["reason_code"] == "page_context_stale"
    else:
        assert result["status"] == "infeasible"
        assert result["success"] is False
        assert result["reason_code"] == "page_cleanup_failed"
        assert "Close the remaining background Page" in result[
            "handoff_instruction"
        ]



def test_close_page_forwards_exact_identity_through_child_bridge(monkeypatch):
    from openprogram.runtime.conversation import surface_context
    from openprogram.interfaces.server.ws_actions import webtab

    sent = []
    monkeypatch.setenv("OPENPROGRAM_IN_AGENTIC_SUBPROCESS", "1")
    monkeypatch.setattr(
        webtab,
        "_request",
        lambda command, timeout: sent.append((command, timeout)) or {
            "ok": True,
            "tab_id": "tab-background",
        },
    )

    result = surface_context.close_page({
        "window_id": "window-1",
        "surfaces": [{
            "window_id": "window-1",
            "tab_id": "tab-background",
            "agent_owned": True,
        }],
    })

    assert result["ok"] is True
    assert sent == [({
        "op": "close",
        "window_id": "window-1",
        "tab_id": "tab-background",
    }, 5.0)]



@pytest.mark.parametrize("close_result", [
    {"ok": False, "reason_code": "desktop_unavailable", "error": "rejected"},
    None,
])
def test_close_page_standardizes_cleanup_failure(monkeypatch, close_result):
    from openprogram.runtime.conversation import surface_context
    from openprogram.interfaces.server.ws_actions import webtab

    monkeypatch.setenv("OPENPROGRAM_IN_AGENTIC_SUBPROCESS", "1")
    monkeypatch.setattr(webtab, "_request", lambda *_args, **_kwargs: close_result)

    result = surface_context.close_page({
        "window_id": "window-1",
        "surfaces": [{
            "window_id": "window-1",
            "tab_id": "tab-background",
            "agent_owned": True,
        }],
    })

    assert result["ok"] is False
    assert result["status"] == "infeasible"
    assert result["success"] is False
    assert result["infeasible_declared"] is True
    assert result["reason_code"] == "page_cleanup_failed"
    assert "Close the remaining background Page" in result[
        "handoff_instruction"
    ]



def test_close_page_missing_exact_identity_requires_manual_cleanup():
    from openprogram.runtime.conversation import surface_context

    result = surface_context.close_page({
        "window_id": "window-1",
        "surfaces": [{"agent_owned": True}],
    })

    assert result["ok"] is False
    assert result["status"] == "infeasible"
    assert result["success"] is False
    assert result["infeasible_declared"] is True
    assert result["reason_code"] == "page_cleanup_failed"
    assert "Close the remaining background Page" in result[
        "handoff_instruction"
    ]



def test_open_page_rejects_non_http_scheme_without_desktop_ipc(monkeypatch):
    from openprogram.runtime.conversation import surface_context
    from openprogram.interfaces.server.ws_actions import webtab

    def fail_request(*_args, **_kwargs):
        raise AssertionError("invalid scheme must not reach desktop")

    monkeypatch.setattr(webtab, "request_on_ws", fail_request)
    result = surface_context.open_page("javascript:alert(1)")
    assert result["ok"] is False
    assert result["reason_code"] == "unsupported_url"
    assert "SCHEME_FORBIDDEN" in result["error"]



def test_open_page_reports_missing_desktop(monkeypatch):
    from openprogram.runtime.conversation import surface_context
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions import webtab

    monkeypatch.setattr(server, "_ws_connections", [])
    monkeypatch.setattr(webtab, "registered_desktop_windows", lambda: [])
    result = surface_context.open_page("https://example.test/")
    assert result["ok"] is False
    assert result["reason_code"] == "desktop_unavailable"
    assert result["error"] == surface_context.DESKTOP_UNAVAILABLE_ERROR



def test_open_page_requires_origin_window_when_multiple_desktops(monkeypatch):
    from openprogram.runtime.conversation import surface_context
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions import webtab

    owners = [_WS(), _WS()]
    for index, owner in enumerate(owners, start=1):
        webtab.ensure_connection_revision(owner)
        webtab._desktop_windows[owner] = f"window-{index}"
    monkeypatch.setattr(server, "_ws_connections", owners)
    try:
        result = surface_context.open_page(
            "https://example.test/", background=True,
        )
        assert result["ok"] is False
        assert result["reason_code"] == "desktop_unavailable"
    finally:
        for owner in owners:
            webtab.release_connection(owner)



def test_open_page_goes_to_the_turns_own_window_among_several(monkeypatch):
    from openprogram.runtime.conversation import surface_context
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions import webtab

    owners = [_WS(), _WS()]
    for index, owner in enumerate(owners, start=1):
        webtab.ensure_connection_revision(owner)
        webtab._desktop_windows[owner] = f"window-{index}"
    monkeypatch.setattr(server, "_ws_connections", owners)
    asked = []
    monkeypatch.setattr(webtab, "request_on_ws", lambda ws, command, timeout=5.0: asked.append(
        (ws, command.get("window_id"))) or {"ok": False, "error": "stop here"})
    token = surface_context.bind(surface_context.window_context("window-2"))
    try:
        surface_context.open_page("https://example.test/", background=True)
    finally:
        surface_context.reset(token)
        for owner in owners:
            webtab.release_connection(owner)
    assert asked == [(owners[1], None)]


def test_the_browser_is_available_with_no_app_window_where_one_can_be_started(monkeypatch):
    from openprogram.host.resources import background_browser, desktop_link
    from openprogram.runtime.conversation import surface_context

    monkeypatch.delenv("OPENPROGRAM_IN_AGENTIC_SUBPROCESS", raising=False)
    monkeypatch.setattr(desktop_link, "registered_desktop_windows", lambda: [])
    monkeypatch.setattr(background_browser, "available", lambda: True)
    assert surface_context.browser_pages_available(None) is True
    monkeypatch.setattr(background_browser, "available", lambda: False)
    assert surface_context.browser_pages_available(None) is False
    # The user's switch on the page of this turn still turns the browser off.
    monkeypatch.setattr(background_browser, "available", lambda: True)
    assert surface_context.browser_pages_available(
        {"surfaces": [{"preview_status": "disabled"}]}) is False


def test_turn_surface_grant_allows_only_computer_use_after_rules(monkeypatch):
    from types import SimpleNamespace

    from openprogram.runtime.permissions.authority import local_owner_authority
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.permissions.approval import wrap_with_approval
    from openprogram.models.providers.types import AgentTool, AgentToolResult
    from openprogram.models.providers.types import TextContent

    calls = []

    async def execute(_call_id, _args, _cancel, _on_update):
        calls.append("executed")
        return AgentToolResult(content=[TextContent(text="ok")])

    tool = AgentTool(
        name="computer_use_agent",
        description="Operates the built-in browser's tabs",
        parameters={"type": "object"},
        label="computer_use_agent",
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
        "openprogram.runtime.permissions.approval.await_user_approval",
        unexpected_approval,
    )

    result = asyncio.run(
        wrap_with_approval(tool, req, lambda _event: None).execute(
            "call-1", {"target": "browser", "action": "click", "args": {"ref": "e1"}}, SimpleNamespace(), lambda _event: None,
        )
    )

    assert calls == ["executed"]
    assert result.is_error is False


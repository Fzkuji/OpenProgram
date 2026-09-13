"""surface capture tests."""
from __future__ import annotations
from ._support import (
    _WS,
    asyncio,
    json,
    pytest,
)


def test_surface_context_captures_preview_from_the_originating_socket(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui.ws_actions import webtab

    ws = _WS()

    def request(bound_ws, command, timeout=5.0):
        assert bound_ws is ws
        assert command == {
            "op": "preview", "window_id": "window-1", "tab_id": "w:right",
            "expected_geometry_revision": 7,
        }
        return {
            "ok": True,
            "window_id": "window-1",
            "tab_id": "w:right",
            "target_id": "target-right",
            "url": "https://example.com/path?token=secret",
            "title": "Right page",
            "geometry_revision": 7,
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
        "geometry_revision": 7,
    }, ws)

    assert context["primary_surface_key"] == "s1"
    assert context["alias_map"]["right"] == "s1"
    assert context["surfaces"][0]["preview"]["visible_text_excerpt"] == "Visible page text"
    assert context["surfaces"][0]["origin"] == "https://example.com"
    assert context["surfaces"][0]["page_revision"] > 0
    assert context["surfaces"][0]["access_revision"] > 0
    assert context["surfaces"][0]["geometry_revision"] == 7
    assert "token=secret" not in surface_context.render_for_model(context)
    assert "preview_status" in surface_context.render_for_model(context)
    assert "do not claim that the page is invisible" in surface_context.render_for_model(context)
    binding_id = context["surfaces"][0]["binding_id"]
    assert context["surfaces"][0]["page_key"] == webtab.binding_page_key(binding_id)
    assert webtab._bindings[binding_id][2] == "w:right"
    webtab.release_binding(binding_id)



def test_webtab_page_key_is_shared_across_recaptures_of_the_same_target():
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    first = webtab.register_binding(
        owner, "window-1", "tab-1", "target-1",
    )
    moved = webtab.register_binding(
        owner, "window-2", "tab-2", "target-1",
    )
    other_owner = webtab.register_binding(
        _WS(), "window-1", "tab-1", "target-1",
    )
    try:
        assert webtab.binding_page_key(first) == webtab.binding_page_key(moved)
        assert webtab.binding_page_key(first) != webtab.binding_page_key(other_owner)
        first_revisions = webtab.binding_revisions(first)
        moved_revisions = webtab.binding_revisions(moved)
        assert first_revisions["page_revision"] == moved_revisions["page_revision"]
        assert first_revisions["access_revision"] < moved_revisions["access_revision"]
    finally:
        for binding_id in (first, moved, other_owner):
            webtab.release_binding(binding_id)



def test_direct_mcp_page_capture_requires_one_desktop_connection(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    monkeypatch.setattr(server, "_ws_connections", [owner])
    monkeypatch.setattr(webtab, "request_on_ws", lambda ws, command, timeout=5.0: {
        "ok": True,
        "window_id": "window-1",
        "tab_id": "tab-1",
        "target_id": "target-1",
        "url": "https://example.test/",
        "title": "Example",
    })
    context = surface_context.capture_active()
    surface = context["surfaces"][0]
    assert surface["surface_key"] == "p1"
    assert surface["page_key"] == webtab.binding_page_key(surface["binding_id"])
    assert webtab._bindings[surface["binding_id"]][0] is owner
    webtab.release_binding(surface["binding_id"])

    monkeypatch.setattr(server, "_ws_connections", [owner, _WS()])
    try:
        surface_context.capture_active()
    except RuntimeError as exc:
        assert "one desktop connection" in str(exc)
    else:
        raise AssertionError("multiple desktop connections must be rejected")



def test_capture_active_without_page_tells_model_to_navigate(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    monkeypatch.setattr(server, "_ws_connections", [owner])
    monkeypatch.setattr(webtab, "request_on_ws", lambda *a, **k: {"ok": False})
    with pytest.raises(RuntimeError, match="use navigate to open a URL first"):
        surface_context.capture_active()



def test_capture_preserves_origin_window_without_granting_page_access():
    from openprogram.agent import surface_context
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    other = _WS()
    asyncio.run(webtab.handle_webtab_register(owner, {
        "action": "webtab_register", "window_id": "window-2",
    }))
    try:
        context = surface_context.capture({
            "version": 1,
            "window_id": "window-2",
            "access": "enabled",
        }, owner)

        assert context["origin_window_id"] == "window-2"
        assert context["window_id"] == "window-2"
        assert context["surfaces"] == []
        assert surface_context.tool_enabled(context) is False
        assert surface_context.capture({
            "version": 1,
            "window_id": "window-2",
            "access": "enabled",
        }, other) is None
    finally:
        webtab.release_connection(owner)



def test_origin_window_page_inventory_only_lists_that_window(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    other = _WS()
    monkeypatch.setattr(server, "_ws_connections", [owner, other])
    asyncio.run(webtab.handle_webtab_register(owner, {
        "action": "webtab_register", "window_id": "window-1",
    }))
    asyncio.run(webtab.handle_webtab_register(other, {
        "action": "webtab_register", "window_id": "window-2",
    }))
    calls = []

    def inventory(ws, command, timeout=5.0):
        calls.append((ws, command, timeout))
        return {
            "ok": True,
            "window_id": "window-1",
            "pages": [{
                "tab_id": "tab-existing",
                "target_id": "target-existing",
                "url": "https://example.test/",
                "title": "Existing",
                "visible": True,
                "focused": True,
                "region": "center",
            }],
        }

    monkeypatch.setattr(webtab, "request_on_ws", inventory)
    try:
        context = surface_context.capture_pages(
            surface_context.window_context("window-1")
        )

        assert calls == [(owner, {"op": "list", "window_id": "window-1"}, 5.0)]
        assert [window["window_id"] for window in context["windows"]] == [
            "window-1",
        ]
        assert context["surfaces"][0]["tab_id"] == "tab-existing"
        assert webtab.binding_connection(
            context["surfaces"][0]["binding_id"]
        ) is owner
        surface_context.release_bindings(context)
    finally:
        webtab.release_connection(owner)
        webtab.release_connection(other)



def test_origin_window_page_inventory_uses_parent_bridge_in_child(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui.ws_actions import webtab

    expected = {
        "context_id": "page-context",
        "window_id": "window-1",
        "surfaces": [{
            "window_id": "window-1",
            "tab_id": "tab-existing",
            "binding_id": "surface-existing",
        }],
    }
    sent = []
    monkeypatch.setenv("OPENPROGRAM_IN_AGENTIC_SUBPROCESS", "1")
    monkeypatch.setattr(
        webtab,
        "_request",
        lambda command, timeout: sent.append((command, timeout)) or {
            "ok": True, "context": expected,
        },
    )
    monkeypatch.setattr(
        webtab,
        "register_binding",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("child must not create parent Page bindings")
        ),
    )

    context = surface_context.capture_pages(
        surface_context.window_context("window-1")
    )

    assert context is expected
    assert sent == [({
        "op": "capture_pages",
        "window_id": "window-1",
    }, 5.0)]



def test_direct_page_inventory_includes_background_and_popup_provenance(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    monkeypatch.setattr(server, "_ws_connections", [owner])
    asyncio.run(webtab.handle_webtab_register(owner, {
        "action": "webtab_register", "window_id": "window-1",
    }))
    monkeypatch.setattr(webtab, "request_on_ws", lambda ws, command, timeout=5.0: {
        "ok": True,
        "window_id": "window-1",
        "pages": [
            {
                "tab_id": "tab-a", "target_id": "target-a",
                "url": "https://a.example/path", "title": "A",
                "focused": True, "visible": True, "region": "center",
            },
            {
                "tab_id": "tab-b", "target_id": "target-b",
                "url": "https://b.example/path", "title": "B",
                "focused": False, "visible": False, "region": "background",
            },
            {
                "tab_id": "tab-c", "target_id": "target-c",
                "url": "https://c.example/path", "title": "C",
                "focused": False, "visible": False, "region": "background",
                "opener_tab_id": "tab-a",
            },
        ],
    })

    context = surface_context.capture_pages()
    assert context["primary_surface_key"] == "p1"
    assert [page["title"] for page in context["surfaces"]] == ["A", "B", "C"]
    assert context["surfaces"][1]["visible"] is False
    assert context["surfaces"][2]["opener_tab_id"] == "tab-a"
    background_binding = context["surfaces"][1]["binding_id"]

    seen = []
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda ws, command, timeout=5.0: seen.append((ws, command)) or {
            "ok": True,
            "window_id": "window-1",
            "tab_id": "tab-b",
            "target_id": "target-b",
        },
    )
    assert webtab.request_bound_tab(background_binding)["ok"] is True
    assert seen == [(owner, {
        "op": "resolve", "window_id": "window-1", "tab_id": "tab-b",
    })]
    surface_context.release_bindings(context)
    webtab.release_connection(owner)



def test_page_inventory_rejects_unregistered_web_client(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    browser_only = _WS()
    calls = []
    monkeypatch.setattr(server, "_ws_connections", [browser_only])
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="registered Desktop window"):
        surface_context.capture_pages()
    assert calls == []



def test_page_inventory_aggregates_registered_desktop_windows(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    primary = _WS()
    secondary = _WS()
    browser_only = _WS()
    monkeypatch.setattr(server, "_ws_connections", [primary, secondary, browser_only])
    asyncio.run(webtab.handle_webtab_register(primary, {
        "action": "webtab_register", "window_id": "window-1",
    }))
    asyncio.run(webtab.handle_webtab_register(secondary, {
        "action": "webtab_register", "window_id": "window-2",
    }))

    def inventory(ws, command, timeout=5.0):
        assert command == {"op": "list", "window_id": (
            "window-1" if ws is primary else "window-2"
        )}
        window_id = command["window_id"]
        suffix = "a" if ws is primary else "b"
        tab_id = "tab-shared"
        return {
            "ok": True,
            "window_id": window_id,
            "inventory_revision": 1,
            "active_tab_entry_id": f"tab:{tab_id}",
            "focused_tab_id": tab_id,
            "tab_entries": [{
                "id": f"tab:{tab_id}", "mode": "single",
                "tab_ids": [tab_id],
            }],
            "pages": [{
                "tab_id": tab_id,
                "target_id": f"target-{suffix}",
                "url": f"https://{suffix}.test/",
                "title": suffix.upper(),
                "visible": True,
                "focused": True,
                "region": "center",
                "tab_entry_id": f"tab:{tab_id}",
                "placement": {"mode": "single"},
            }],
        }

    monkeypatch.setattr(webtab, "request_on_ws", inventory)
    context = surface_context.capture_pages()

    assert context["window_id"] == "window-1"
    assert [window["window_id"] for window in context["windows"]] == [
        "window-1", "window-2",
    ]
    assert [page["window_id"] for page in context["surfaces"]] == [
        "window-1", "window-2",
    ]
    assert context["windows"][0]["pages"] == ["p1"]
    assert context["windows"][1]["pages"] == ["p2"]
    assert context["alias_map"]["window:window-1:tab:tab-shared"] == "p1"
    assert context["alias_map"]["window:window-2:tab:tab-shared"] == "p2"
    assert webtab.binding_connection(context["surfaces"][0]["binding_id"]) is primary
    assert webtab.binding_connection(context["surfaces"][1]["binding_id"]) is secondary
    assert browser_only.messages == []

    surface_context.release_bindings(context)
    webtab.release_connection(primary)
    webtab.release_connection(secondary)



def test_context_page_inventory_stays_in_originating_window(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    primary = _WS()
    secondary = _WS()
    monkeypatch.setattr(server, "_ws_connections", [secondary, primary])
    asyncio.run(webtab.handle_webtab_register(secondary, {
        "action": "webtab_register", "window_id": "window-2",
    }))
    binding = webtab.register_binding(
        primary, "window-1", "tab-a", "target-a", allow_background=True,
    )
    accepted = {
        "context_id": "accepted",
        "surfaces": [{"binding_id": binding}],
    }

    def inventory(ws, command, timeout=5.0):
        window_id = "window-1" if ws is primary else "window-2"
        assert command == {"op": "list", "window_id": window_id}
        suffix = "a" if ws is primary else "b"
        return {
            "ok": True, "window_id": window_id,
            "pages": [{
                "tab_id": f"tab-{suffix}", "target_id": f"target-{suffix}",
                "url": f"https://{suffix}.test/", "title": suffix.upper(),
                "visible": True, "focused": True, "region": "center",
            }],
        }

    monkeypatch.setattr(webtab, "request_on_ws", inventory)
    context = surface_context.capture_pages(accepted)

    assert context["window_id"] == "window-1"
    assert [window["window_id"] for window in context["windows"]] == ["window-1"]
    assert [page["window_id"] for page in context["surfaces"]] == ["window-1"]
    surface_context.release_bindings(context)
    webtab.release_connection(secondary)



def test_context_page_inventory_keeps_submitted_page_primary(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    monkeypatch.setattr(server, "_ws_connections", [owner])
    binding = webtab.register_binding(
        owner,
        "window-1",
        "tab-submitted",
        "target-submitted",
        allow_background=True,
    )
    accepted = {
        "context_id": "accepted",
        "window_id": "window-1",
        "surfaces": [{
            "binding_id": binding,
            "window_id": "window-1",
            "tab_id": "tab-submitted",
        }],
    }
    monkeypatch.setattr(webtab, "request_on_ws", lambda *_args, **_kwargs: {
        "ok": True,
        "window_id": "window-1",
        "focused_tab_id": "tab-other",
        "pages": [
            {
                "tab_id": "tab-other",
                "target_id": "target-other",
                "url": "https://other.test/",
                "title": "Other",
                "visible": True,
                "focused": True,
                "region": "center",
            },
            {
                "tab_id": "tab-submitted",
                "target_id": "target-submitted",
                "url": "https://submitted.test/",
                "title": "Submitted",
                "visible": True,
                "focused": False,
                "region": "center",
            },
        ],
    })
    try:
        context = surface_context.capture_pages(accepted)
        primary = next(
            page for page in context["surfaces"]
            if page["surface_key"] == context["primary_surface_key"]
        )
        assert primary["tab_id"] == "tab-submitted"
        surface_context.release_bindings(context)
    finally:
        webtab.release_connection(owner)



def test_context_page_inventory_does_not_replace_live_primary_on_timeout(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    primary = _WS()
    secondary = _WS()
    monkeypatch.setattr(server, "_ws_connections", [primary, secondary])
    binding = webtab.register_binding(
        primary, "window-1", "tab-primary", "target-primary",
        allow_background=True,
    )
    accepted = {
        "context_id": "accepted",
        "surfaces": [{"binding_id": binding}],
    }
    asyncio.run(webtab.handle_webtab_register(secondary, {
        "action": "webtab_register", "window_id": "window-2",
    }))
    monkeypatch.setattr(
        webtab,
        "request_page_inventory",
        lambda binding_id: {"ok": False, "reason_code": "timeout"},
    )
    secondary_calls = []
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda ws, command, timeout=5.0: secondary_calls.append((ws, command)) or {
            "ok": True,
            "window_id": "window-2",
            "pages": [{
                "tab_id": "tab-secondary",
                "target_id": "target-secondary",
                "url": "https://secondary.test/",
                "title": "Secondary",
                "visible": True,
                "focused": True,
                "region": "center",
            }],
        },
    )

    with pytest.raises(RuntimeError, match="Page inventory is unavailable"):
        surface_context.capture_pages(accepted)
    assert secondary_calls == []

    webtab.release_connection(primary)
    webtab.release_connection(secondary)



def test_direct_page_inventory_preserves_tab_entries_and_split_panes(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    monkeypatch.setattr(server, "_ws_connections", [owner])
    asyncio.run(webtab.handle_webtab_register(owner, {
        "action": "webtab_register", "window_id": "window-1",
    }))
    monkeypatch.setattr(webtab, "request_on_ws", lambda ws, command, timeout=5.0: {
        "ok": True,
        "window_id": "window-1",
        "inventory_revision": 9,
        "active_tab_entry_id": "group:g3",
        "focused_tab_id": "tab-d",
        "tab_entries": [
            {"id": "tab:tab-a", "mode": "single", "tab_ids": ["tab-a"]},
            {"id": "tab:tab-b", "mode": "single", "tab_ids": ["tab-b"]},
            {
                "id": "group:g3", "mode": "split",
                "tab_ids": ["tab-c", "tab-d"],
                "split": {
                    "axis": "horizontal", "ratio": 0.5,
                    "panes": [
                        {"pane_id": "pane:g3:0", "order": 0, "tab_id": "tab-c"},
                        {"pane_id": "pane:g3:1", "order": 1, "tab_id": "tab-d"},
                    ],
                },
            },
        ],
        "pages": [
            {"tab_id": "tab-a", "target_id": "target-a", "url": "https://a.test", "title": "A", "visible": False, "focused": False, "region": "background", "tab_entry_id": "tab:tab-a", "placement": {"mode": "single"}},
            {"tab_id": "tab-b", "target_id": "target-b", "url": "https://b.test", "title": "B", "visible": False, "focused": False, "region": "background", "tab_entry_id": "tab:tab-b", "placement": {"mode": "single"}},
            {"tab_id": "tab-c", "target_id": "target-c", "url": "https://c.test", "title": "C", "visible": True, "focused": False, "region": "left", "tab_entry_id": "group:g3", "placement": {"mode": "split", "pane_id": "pane:g3:0", "order": 0}},
            {"tab_id": "tab-d", "target_id": "target-d", "url": "https://d.test", "title": "D", "visible": True, "focused": True, "region": "right", "tab_entry_id": "group:g3", "placement": {"mode": "split", "pane_id": "pane:g3:1", "order": 1}},
        ],
    })

    context = surface_context.capture_pages()

    assert context["inventory_revision"] == 9
    assert context["active_tab_entry_id"] == "group:g3"
    assert context["focused_page"] == "p4"
    assert context["tab_entries"] == [
        {"id": "tab:tab-a", "mode": "single", "pages": ["p1"]},
        {"id": "tab:tab-b", "mode": "single", "pages": ["p2"]},
        {
            "id": "group:g3", "mode": "split", "pages": ["p3", "p4"],
            "split": {
                "axis": "horizontal", "ratio": 0.5,
                "panes": [
                    {"pane_id": "pane:g3:0", "order": 0, "page": "p3"},
                    {"pane_id": "pane:g3:1", "order": 1, "page": "p4"},
                ],
            },
        },
    ]
    assert context["surfaces"][2]["tab_entry_id"] == "group:g3"
    assert context["surfaces"][3]["placement"] == {
        "mode": "split", "pane_id": "pane:g3:1", "order": 1,
    }
    surface_context.release_bindings(context)
    webtab.release_connection(owner)



def test_registered_page_inventory_keeps_web_use_when_preview_is_unavailable(
    monkeypatch,
):
    from types import SimpleNamespace

    from openprogram.agent import surface_context
    from openprogram.agent.dispatcher.loop_runner import _configure_web_use_tools
    from openprogram.webui.ws_actions import webtab

    monkeypatch.setattr(
        webtab, "registered_desktop_windows", lambda: [(object(), "main", 1)],
    )
    context = {
        "surfaces": [{
            "surface_key": "s1",
            "preview_status": "unavailable",
            "capabilities": [],
        }],
    }
    tools = [
        SimpleNamespace(name="agent_browser"),
        SimpleNamespace(name="browser_agent"),
        SimpleNamespace(name="playwright_browser"),
        SimpleNamespace(name="web_search"),
    ]

    configured, enabled = _configure_web_use_tools(tools, context)
    names = [tool.name for tool in configured]

    assert enabled is True
    assert "web_use" in names
    assert "web_search" in names
    assert not ({"agent_browser", "browser_agent", "playwright_browser"} & set(names))
    prompt = surface_context.render_for_model(
        context, web_use_enabled=enabled,
    )
    assert "web_use list_pages" in prompt
    assert "Never put a URL in page" in prompt



def test_explicitly_disabled_surface_does_not_expose_registered_page_inventory(
    monkeypatch,
):
    from types import SimpleNamespace

    from openprogram.agent.dispatcher.loop_runner import _configure_web_use_tools
    from openprogram.webui.ws_actions import webtab

    monkeypatch.setattr(
        webtab, "registered_desktop_windows", lambda: [(object(), "main", 1)],
    )
    context = {
        "surfaces": [{
            "surface_key": "s1",
            "preview_status": "disabled",
            "capabilities": [],
        }],
    }

    configured, enabled = _configure_web_use_tools(
        [SimpleNamespace(name="web_use"), SimpleNamespace(name="web_search")],
        context,
    )

    assert enabled is False
    assert [tool.name for tool in configured] == ["web_search"]



def test_registered_page_inventory_does_not_override_tools_off(monkeypatch):
    from openprogram.agent.dispatcher.loop_runner import _configure_web_use_tools
    from openprogram.webui.ws_actions import webtab

    monkeypatch.setattr(
        webtab, "registered_desktop_windows", lambda: [(object(), "main", 1)],
    )

    configured, enabled = _configure_web_use_tools([], None)

    assert configured == []
    assert enabled is False



def test_subprocess_permission_snapshot_denies_nested_browser_page_before_bypass():
    from dataclasses import replace
    from types import SimpleNamespace

    from openprogram.agent.authority import local_owner_authority, runtime_authority
    from openprogram.agent.dispatcher import TurnRequest
    from openprogram.agent.permissions.approval import wrap_with_approval
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
    parent_request = TurnRequest(
        session_id="session-1", user_text="", agent_id="main", source="web",
        **local_owner_authority(),
    )
    # Match runtime_attach -> process_runner: child authority is non-interactive,
    # so its admitted snapshot is distinct from the owner's live project rules.
    child_request = replace(TurnRequest(
        session_id="session-1",
        user_text="",
        agent_id="main",
        source="web",
        permission_rules=rules,
        **runtime_authority(parent_request, "agentic/browser_agent"),
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



def test_self_update_capture_payload_preserves_its_exact_protocol_fields():
    import json
    from openprogram.agent.run_control import set_current_session_id, reset_current_session_id
    from openprogram.webui.ws_actions import webtab

    token = set_current_session_id("conversation-a")
    try:
        command = {"op": "self_update_capture", "window_id": "main", "nonce": "a" * 64}
        assert json.loads(webtab._payload(command, "request"))["data"] == {
            **command, "req_id": "request",
        }
    finally:
        reset_current_session_id(token)


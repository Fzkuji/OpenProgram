from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


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


def test_surface_preview_cannot_recreate_binding_after_disconnect(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui.ws_actions import webtab

    owner = _WS()

    def disconnect_before_result(*_args, **_kwargs):
        webtab.release_connection(owner)
        return {
            "ok": True,
            "window_id": "window-1",
            "tab_id": "tab-1",
            "target_id": "target-1",
            "geometry_revision": 1,
        }

    monkeypatch.setattr(webtab, "request_on_ws", disconnect_before_result)
    context = surface_context.capture({
        "version": 1,
        "window_id": "window-1",
        "tab_id": "tab-1",
        "region": "right",
        "access": "enabled",
        "geometry_revision": 1,
    }, owner)

    assert context["surfaces"][0]["preview_status"] == "unavailable"
    assert "binding_id" not in context["surfaces"][0]
    assert all(entry[0] is not owner for entry in webtab._bindings.values())


def test_active_page_capture_cannot_recreate_binding_after_disconnect(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    monkeypatch.setattr(server, "_ws_connections", [owner])

    def disconnect_before_result(*_args, **_kwargs):
        webtab.release_connection(owner)
        return {
            "ok": True,
            "window_id": "window-1",
            "tab_id": "tab-1",
            "target_id": "target-1",
        }

    monkeypatch.setattr(webtab, "request_on_ws", disconnect_before_result)
    with pytest.raises(RuntimeError, match="connection changed during Page binding"):
        surface_context.capture_active()
    assert all(entry[0] is not owner for entry in webtab._bindings.values())


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


def test_bound_webtab_request_rejects_stale_expected_revision_before_ipc(
    monkeypatch,
):
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    binding_id = webtab.register_binding(
        owner, "window-1", "tab-1", "target-1",
    )
    revisions = webtab.binding_revisions(binding_id)
    sent = []
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True},
    )
    try:
        result = webtab.request_bound_tab(
            binding_id,
            expected_page_revision=revisions["page_revision"],
            expected_access_revision=revisions["access_revision"] + 1,
        )
    finally:
        webtab.release_binding(binding_id)

    assert result["reason_code"] == "page_context_stale"
    assert sent == []


def test_bound_webtab_request_forwards_geometry_revision_to_exact_renderer(
    monkeypatch,
):
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    binding_id = webtab.register_binding(
        owner, "window-1", "tab-1", "target-1", geometry_revision=9,
    )
    seen = []
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda ws, command, timeout=5.0: seen.append((ws, command)) or {
            "ok": False,
            "error": "web tab geometry changed",
            "reason_code": "page_context_stale",
        },
    )

    result = webtab.request_bound_tab(
        binding_id,
        expected_geometry_revision=9,
    )

    assert result["reason_code"] == "page_context_stale"
    assert seen == [(owner, {
        "op": "activate",
        "window_id": "window-1",
        "tab_id": "tab-1",
        "expected_geometry_revision": 9,
    })]
    assert binding_id not in webtab._bindings


def test_bound_webtab_request_rejects_geometry_changed_during_activation(
    monkeypatch,
):
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    binding_id = webtab.register_binding(
        owner, "window-1", "tab-1", "target-1", geometry_revision=9,
    )
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda *_args, **_kwargs: {
            "ok": True,
            "window_id": "window-1",
            "tab_id": "tab-1",
            "target_id": "target-1",
            "geometry_revision": 10,
        },
    )

    result = webtab.request_bound_tab(
        binding_id,
        expected_geometry_revision=9,
    )

    assert result["reason_code"] == "page_context_stale"
    assert binding_id not in webtab._bindings


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


def test_webtab_disconnect_revokes_owned_bindings_and_wakes_waiters():
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    other = _WS()
    owned = webtab.register_binding(owner, "window-1", "tab-1", "target-1")
    retained = webtab.register_binding(other, "window-2", "tab-2", "target-2")
    event = threading.Event()
    holder: dict = {}
    webtab._pending["owned-request"] = (event, holder, owner)
    try:
        webtab.release_connection(owner)

        assert owned not in webtab._bindings
        assert retained in webtab._bindings
        assert event.is_set()
        assert holder["result"] == {
            "ok": False,
            "error": "originating desktop connection disconnected",
        }
    finally:
        webtab._pending.pop("owned-request", None)
        webtab.release_binding(owned)
        webtab.release_binding(retained)


def test_webtab_connection_and_target_replacement_advance_page_revision(monkeypatch):
    from openprogram.webui.ws_actions import webtab

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


def test_webtab_result_preserves_geometry_stale_reason():
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    event = threading.Event()
    holder: dict = {}
    webtab._pending["geometry"] = (event, holder, owner)
    try:
        asyncio.run(webtab.handle_webtab_result(owner, {
            "req_id": "geometry",
            "ok": False,
            "error": "web tab geometry changed",
            "reason_code": "page_context_stale",
            "geometry_revision": 12,
        }))
        assert holder["result"]["reason_code"] == "page_context_stale"
        assert holder["result"]["geometry_revision"] == 12
    finally:
        webtab._pending.pop("geometry", None)


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


def test_open_page_opens_background_tab_on_registered_desktop(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = "window-1"
    sent = []
    monkeypatch.setattr(server, "_ws_connections", [owner])
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda ws, command, timeout=15.0: sent.append((ws, command)) or {
            "ok": True,
            "window_id": "window-1",
            "tab_id": "w:https://example.test/",
            "target_id": "target-opened",
            "url": "https://example.test/",
            "title": "Example",
        },
    )
    context = {}
    try:
        context = surface_context.open_page("https://example.test/")
        surface = context["surfaces"][0]
        assert sent == [(owner, {"op": "open", "url": "https://example.test/"})]
        assert surface["binding_id"] in webtab._bindings
        assert webtab._bindings[surface["binding_id"]][8] is True
    finally:
        for surface in context.get("surfaces") or []:
            webtab.release_binding(surface["binding_id"])
        webtab.release_connection(owner)


def test_open_page_rejects_non_http_scheme_without_desktop_ipc(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui.ws_actions import webtab

    def fail_request(*_args, **_kwargs):
        raise AssertionError("invalid scheme must not reach desktop")

    monkeypatch.setattr(webtab, "request_on_ws", fail_request)
    result = surface_context.open_page("javascript:alert(1)")
    assert result["ok"] is False
    assert result["reason_code"] == "unsupported_url"
    assert "SCHEME_FORBIDDEN" in result["error"]


def test_open_page_reports_missing_desktop(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    monkeypatch.setattr(server, "_ws_connections", [])
    monkeypatch.setattr(webtab, "registered_desktop_windows", lambda: [])
    result = surface_context.open_page("https://example.test/")
    assert result["ok"] is False
    assert result["reason_code"] == "desktop_unavailable"
    assert result["error"] == surface_context.DESKTOP_UNAVAILABLE_ERROR


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


def test_context_page_inventory_keeps_originating_window_primary(monkeypatch):
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
    assert [window["window_id"] for window in context["windows"]] == [
        "window-1", "window-2",
    ]
    surface_context.release_bindings(context)
    webtab.release_connection(secondary)


def test_context_page_inventory_survives_originating_window_disconnect(monkeypatch):
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
    webtab.release_connection(primary)
    monkeypatch.setattr(server, "_ws_connections", [secondary])

    calls = []
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda ws, command, timeout=5.0: calls.append((ws, command)) or {
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

    context = surface_context.capture_pages(accepted)
    assert [window["window_id"] for window in context["windows"]] == ["window-2"]
    assert context["surfaces"][0]["window_id"] == "window-2"
    assert calls == [(secondary, {"op": "list", "window_id": "window-2"})]

    surface_context.release_bindings(context)
    webtab.release_connection(secondary)


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


def test_page_inventory_cannot_recreate_binding_after_disconnect(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    monkeypatch.setattr(server, "_ws_connections", [owner])
    binding = webtab.register_binding(
        owner, "window-1", "tab-1", "target-1", allow_background=True,
    )
    accepted = {
        "context_id": "accepted",
        "surfaces": [{"binding_id": binding}],
    }
    monkeypatch.setattr(
        webtab,
        "request_page_inventory",
        lambda binding_id: {
            "ok": True,
            "window_id": "window-1",
            "pages": [{
                "tab_id": "tab-1",
                "target_id": "target-1",
                "url": "https://example.test/",
                "title": "Example",
                "visible": True,
                "focused": True,
                "region": "center",
            }],
        },
    )
    original_owner_revision = webtab.binding_owner_revision

    def disconnect_after_owner_read(binding_id):
        result = original_owner_revision(binding_id)
        webtab.release_connection(owner)
        return result

    monkeypatch.setattr(
        webtab, "binding_owner_revision", disconnect_after_owner_read,
    )

    with pytest.raises(RuntimeError, match="connection changed during Page binding"):
        surface_context.capture_pages(accepted)
    assert all(entry[0] is not owner for entry in webtab._bindings.values())


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


def test_frontend_and_electron_expose_turn_surface_preview_contract():
    send = (
        REPO_ROOT / "apps/web/components/chat/composer/submit/send-chat-message.ts"
    ).read_text(encoding="utf-8")
    bridge = (REPO_ROOT / "apps/web/lib/desktop-bridge.ts").read_text(encoding="utf-8")
    preload = (REPO_ROOT / "apps/desktop/preload.js").read_text(encoding="utf-8")
    main = (REPO_ROOT / "apps/desktop/main.js").read_text(encoding="utf-8")
    use_ws = (REPO_ROOT / "apps/web/lib/net/use-ws.ts").read_text(encoding="utf-8")
    chip = (
        REPO_ROOT
        / "apps/web/components/chat/composer/environment-row/chips/web-surface-chip.tsx"
    ).read_text(encoding="utf-8")

    assert "surfaceRefForChat(sessionId, toolsEnabled)" in send
    assert "payload.surface = surface" in send
    assert "export function surfaceRefForChat" in bridge
    assert 'd.op === "preview"' in bridge
    assert "webTab.preview(tab.id)" in bridge
    control = bridge[bridge.index("export function installDesktopMenuHandlers"):]
    geometry_guard = control.index("if (d.expected_geometry_revision")
    assert geometry_guard < control.index("bridge.webTab.preview(tab.id)")
    assert geometry_guard < control.index("bridge.webTab.activate(tab.id, d.url, true)")
    assert 'preview: (id) => ipcRenderer.invoke("webtab:preview", id)' in preload
    assert 'ipcMain.handle("webtab:preview"' in main
    assert 'action: "webtab_register", window_id: desktopWindowId' in use_ws
    assert "visible_text_excerpt" in main
    assert "Agent can access" in chip
    assert "surfaceRefForChat(sessionId, toolsEnabled)" in chip
    assert "surface.region" in chip
    assert "· right ·" not in chip
    assert 'aria-label={`${stateLabel}: ${regionLabel} · ${title}`}' in chip


def test_web_use_is_registered_as_surface_aware_public_tool():
    from openprogram.programs import (
        DEFERRED_DEFAULT_TOOLS,
        DEFAULT_TOOLS,
        agent_tools,
        apply_tool_policy,
    )

    assert "web_use" in DEFAULT_TOOLS
    assert "web_use" not in DEFERRED_DEFAULT_TOOLS
    tool = next(item for item in agent_tools(names=["web_use"]) if item.name == "web_use")
    assert "page" in tool.parameters["properties"]
    assert "command" in tool.parameters["properties"]
    assert "arguments" in tool.parameters["properties"]
    assert apply_tool_policy([tool], source="plan") == []


def test_surface_tool_is_injected_after_tools_are_resolved():
    source = (REPO_ROOT / "openprogram/agent/dispatcher/loop_runner.py").read_text(
        encoding="utf-8"
    )

    resolve_at = source.index(
        "tools = _resolve_tools(agent_profile, req.tools_override, source=req.source)"
    )
    inject_at = source.index(
        "tools, web_use_enabled = _configure_web_use_tools"
    )
    assert resolve_at < inject_at


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


def test_bound_browser_task_bypasses_only_the_nested_default_ask(monkeypatch):
    from types import SimpleNamespace

    from openprogram.agent.dispatcher import TurnRequest
    from openprogram.agent.turn_request_context import (
        get_turn_request,
        reset_turn_request,
        set_turn_request,
    )
    from openprogram.programs.workflow import browser as module

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
        name="web_use",
        description="Bound in-app web control",
        parameters={"type": "object"},
        label="web_use",
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
    source = (
        REPO_ROOT
        / "apps/server/openprogram_server/_webui/_execute/chat.py"
    ).read_text(encoding="utf-8")
    capture_at = source.index("surface_context = _capture_surface")
    finally_at = source.index("finally:", capture_at)
    release_at = source.index("_release_surface_bindings(surface_context)", finally_at)
    finish_at = source.index("_s._finish_owned_run", finally_at)

    assert capture_at < finally_at < release_at < finish_at


def test_websocket_disconnect_releases_owned_surface_bindings():
    source = (
        REPO_ROOT / "apps/server/openprogram_server/server.py"
    ).read_text(encoding="utf-8")
    finally_at = source.index("    finally:\n", source.index("async def _websocket_handler"))
    remove_at = source.index("_ws_connections.remove(ws)", finally_at)
    release_at = source.index("release_connection(ws)", finally_at)

    assert finally_at < release_at < remove_at


def test_electron_bound_surface_control_does_not_focus_the_app_window():
    source = (REPO_ROOT / "apps/desktop/main.js").read_text(encoding="utf-8")
    start = source.index("async function activateView")
    end = source.index("const SURFACE_PREVIEW_SCRIPT", start)
    activate_source = source[start:end]

    assert "devToolsTargetId(record.view.webContents)" in activate_source
    assert ".focus(" not in activate_source
    assert "BrowserWindow.getFocusedWindow" not in activate_source


def test_electron_bound_surface_activation_requires_existing_visibility():
    bridge = (REPO_ROOT / "apps/web/lib/desktop-bridge.ts").read_text(encoding="utf-8")
    preload = (REPO_ROOT / "apps/desktop/preload.js").read_text(encoding="utf-8")
    main = (REPO_ROOT / "apps/desktop/main.js").read_text(encoding="utf-8")

    assert "bridge.webTab.activate(tab.id, d.url, true)" in bridge
    assert 'ipcRenderer.invoke("webtab:activate", id, url, requireVisible)' in preload
    start = main.index("async function activateView")
    end = main.index("const SURFACE_PREVIEW_SCRIPT", start)
    activate_source = main[start:end]
    assert "requireVisible" in activate_source
    assert "!ctx.visibleViewIds.has(id)" in activate_source

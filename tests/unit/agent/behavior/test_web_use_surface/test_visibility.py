"""surface visibility tests."""
from __future__ import annotations
from ._support import (
    _WS,
    asyncio,
    threading,
)


def test_surface_context_forwards_background_preview_without_activate(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui.ws_actions import webtab

    ws = _WS()
    sent = []

    def request(bound_ws, command, timeout=5.0):
        sent.append(command)
        return {
            "ok": True,
            "window_id": "window-1",
            "tab_id": "w:right",
            "target_id": "target-right",
            "url": "https://example.com/mirror",
            "title": "Mirror",
            "geometry_revision": 3,
            "preview": {"visible_text_excerpt": "PiP", "aria_landmarks": [], "interactive_count": 0},
        }

    monkeypatch.setattr(webtab, "request_on_ws", request)
    context = surface_context.capture({
        "version": 1,
        "window_id": "window-1",
        "tab_id": "w:right",
        "region": "right",
        "access": "enabled",
        "geometry_revision": 3,
        "background": True,
    }, ws)
    assert sent == [{
        "op": "preview", "window_id": "window-1", "tab_id": "w:right",
        "expected_geometry_revision": 3, "background": True,
    }]
    binding_id = context["surfaces"][0]["binding_id"]
    assert webtab._bindings[binding_id][8] is True
    webtab.release_binding(binding_id)



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



def test_open_page_forwards_background_contract_through_child_bridge(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui.ws_actions import webtab

    sent = []
    monkeypatch.setenv("OPENPROGRAM_IN_AGENTIC_SUBPROCESS", "1")
    monkeypatch.setattr(
        webtab,
        "_request",
        lambda command, timeout: sent.append((command, timeout)) or {
            "ok": True,
            "window_id": "window-1",
            "tab_id": "tab-background",
            "target_id": "target-background",
            "created": True,
            "reused": False,
            "url": "https://example.test/",
            "title": "Example",
            "binding_id": "surface-background",
            "page_key": "page-background",
            "page_revision": 3,
            "access_revision": 4,
            "geometry_revision": 5,
        },
    )

    context = surface_context.open_page(
        "https://example.test/", window_id="window-1", background=True,
    )

    assert sent == [({
        "op": "open",
        "url": "https://example.test/",
        "window_id": "window-1",
        "background": True,
    }, 15.0)]
    surface = context["surfaces"][0]
    assert surface["binding_id"] == "surface-background"
    assert surface["page_key"] == "page-background"
    assert surface["aliases"] == ["web:1"]
    assert surface["region"] == "background"
    assert surface["visible"] is False
    assert surface["focused"] is False
    assert surface["agent_owned"] is True



def test_open_page_background_response_timeout_requires_manual_cleanup(
    monkeypatch,
):
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
        lambda ws, command, timeout=15.0: sent.append(
            (ws, command, timeout)
        ) or {
            "ok": False,
            "reason_code": webtab.RESPONSE_TIMEOUT_REASON_CODE,
            "error": "timeout: no desktop shell replied within 15s",
        },
    )
    try:
        result = surface_context.open_page(
            "https://example.test/", window_id="window-1", background=True,
        )
    finally:
        webtab.release_connection(owner)

    assert sent == [(owner, {
        "op": "open",
        "url": "https://example.test/",
        "window_id": "window-1",
        "background": True,
    }, 15.0)]
    assert result["status"] == "infeasible"
    assert result["success"] is False
    assert result["infeasible_declared"] is True
    assert result["reason_code"] == "page_cleanup_failed"
    assert "Close the remaining background Page" in result[
        "handoff_instruction"
    ]


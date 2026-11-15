"""surface transport contracts tests."""
from __future__ import annotations

import re

from tests.support.desktop_source import (
    read_desktop_bridge_source,
    read_desktop_source,
    read_server_source,
)
from ._support import REPO_ROOT, _WS


def test_bound_webtab_request_forwards_geometry_revision_to_exact_renderer(
    monkeypatch,
):
    from openprogram.interfaces.server.ws_actions import webtab

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



def test_open_page_standardizes_renderer_cleanup_failure(monkeypatch):
    from openprogram.runtime.conversation import surface_context
    from openprogram.interfaces.server.ws_actions import webtab

    monkeypatch.setenv("OPENPROGRAM_IN_AGENTIC_SUBPROCESS", "1")
    monkeypatch.setattr(webtab, "_request", lambda *_args, **_kwargs: {
        "ok": False,
        "reason_code": "page_cleanup_failed",
        "error": "agent-created Page cleanup failed",
        "created": True,
        "reused": False,
    })

    result = surface_context.open_page(
        "https://example.test/", window_id="window-1", background=True,
    )

    assert result["status"] == "infeasible"
    assert result["success"] is False
    assert result["infeasible_declared"] is True
    assert result["reason_code"] == "page_cleanup_failed"
    assert "Close the remaining background Page" in result[
        "handoff_instruction"
    ]



def test_frontend_and_electron_expose_turn_surface_preview_contract():
    send = (
        REPO_ROOT / "apps/web/components/chat/composer/submit/send-chat-message.ts"
    ).read_text(encoding="utf-8")
    bridge = read_desktop_bridge_source(REPO_ROOT)
    preload = (REPO_ROOT / "apps/desktop/main/preload.js").read_text(encoding="utf-8")
    main = read_desktop_source(REPO_ROOT)
    use_ws = (REPO_ROOT / "apps/web/lib/net/ws/use-ws.ts").read_text(encoding="utf-8")
    chip = (
        REPO_ROOT
        / "apps/web/components/chat/composer/environment-row/chips/web-surface-chip.tsx"
    ).read_text(encoding="utf-8")

    assert "surfaceOriginForChat(sessionId)" in send
    assert "payload.surface = surface" in send
    assert "export function surfaceRefForChat" in bridge
    assert "export function surfaceOriginForChat" in bridge
    assert 'd.op === "preview"' in bridge
    assert "webTab.preview(tab.id, d.background === true)" in bridge
    control = bridge[bridge.index("export function installDesktopMenuHandlers"):]
    geometry_guard = control.index("if (d.expected_geometry_revision")
    assert geometry_guard < control.index("bridge.webTab.preview(tab.id, d.background === true)")
    assert geometry_guard < control.index("bridge.webTab.activate(tab.id, d.url, true)")
    assert 'ipcRenderer.invoke("webtab:preview", id, allowBackground)' in preload
    assert 'ipcMain.handle("webtab:preview"' in main
    assert 'action: "webtab_register", window_id: desktopWindowId' in use_ws
    assert "visible_text_excerpt" in main
    assert "Agent can access" in chip
    assert "surfaceRefForChat(sessionId)" in chip
    assert "· right ·" not in chip
    # The chip never toggles agent access (tools come from the Agent); its
    # only clicks show or close the floating page preview, and the plain
    # access chip is not clickable at all.
    assert re.findall(r"onClick=\{([^}]*)\}", chip) == [
        "() => restorePagePreview(restoreId, backgroundOwnerTabId)",
        "hidePagePreview",
    ]
    access_chip = chip[chip.rindex("<span"):chip.index("Agent can access")]
    assert "onClick" not in access_chip



def test_websocket_disconnect_releases_owned_surface_bindings():
    source = read_server_source(REPO_ROOT)
    finally_at = source.index("    finally:\n", source.index("async def _websocket_handler"))
    remove_at = source.index("_ws_connections.remove(ws)", finally_at)
    release_at = source.index("release_connection(ws)", finally_at)

    assert finally_at < release_at < remove_at



def test_electron_bound_surface_control_does_not_focus_the_app_window():
    source = read_desktop_source(REPO_ROOT)
    start = source.index("async function activateView")
    end = source.index("const SURFACE_PREVIEW_SCRIPT", start)
    activate_source = source[start:end]

    assert "devToolsTargetId(record.view.webContents)" in activate_source
    assert ".focus(" not in activate_source
    assert "BrowserWindow.getFocusedWindow" not in activate_source



def test_electron_bound_surface_activation_requires_existing_visibility():
    bridge = read_desktop_bridge_source(REPO_ROOT)
    preload = (REPO_ROOT / "apps/desktop/main/preload.js").read_text(encoding="utf-8")
    main = read_desktop_source(REPO_ROOT)

    assert "bridge.webTab.activate(tab.id, d.url, true)" in bridge
    assert 'ipcRenderer.invoke("webtab:activate", id, url, requireVisible)' in preload
    start = main.index("async function activateView")
    end = main.index("const SURFACE_PREVIEW_SCRIPT", start)
    activate_source = main[start:end]
    assert "requireVisible" in activate_source
    assert "!ctx.visibleViewIds.has(id)" in activate_source



def test_renderer_command_payload_carries_runtime_session_for_private_page_access():
    import json
    from openprogram.runtime.execution.run_control import (
        set_current_session_id,
        reset_current_session_id,
    )
    from openprogram.interfaces.server.ws_actions import webtab

    token = set_current_session_id("conversation-a")
    try:
        for op in ("list", "resolve", "activate", "screenshot", "preview", "close", "active"):
            payload = json.loads(webtab._payload({"op": op, "tab_id": "page-a"}, "request"))
            assert payload["data"]["session_id"] == "conversation-a"
    finally:
        reset_current_session_id(token)

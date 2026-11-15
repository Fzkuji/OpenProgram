from __future__ import annotations

from tests.support.desktop_source import read_desktop_bridge_source, read_desktop_source

import asyncio
import json
from pathlib import Path

import pytest

from openprogram.interfaces.server.ws_actions import webtab


REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _clean_pending():
    webtab._pending.clear()
    webtab._bindings.clear()
    webtab._desktop_windows.clear()
    yield
    webtab._pending.clear()
    webtab._bindings.clear()
    webtab._desktop_windows.clear()


_ROUNDTRIP_WS = object()
_PNG_DATA_URL = "data:image/png;base64,iVBORw0KGgo="


def _install_roundtrip(monkeypatch, reply: dict):
    from openprogram.interfaces.server import server

    monkeypatch.setattr(server, "_ws_connections", {_ROUNDTRIP_WS})

    def broadcast(payload: str):
        command = json.loads(payload)["data"]
        asyncio.run(
            webtab.handle_webtab_result(
                _ROUNDTRIP_WS,
                {"req_id": command["req_id"], **reply},
            ),
        )

    monkeypatch.setattr(server, "_broadcast", broadcast)


def test_active_tab_roundtrip_preserves_identity(monkeypatch):
    _install_roundtrip(
        monkeypatch,
        {
            "ok": True,
            "url": "https://example.com/active",
            "tab_id": "w:https://example.com/",
            "target_id": "target-active",
        },
    )
    assert webtab.request_active_tab(timeout=0.1) == {
        "ok": True,
        "error": None,
        "url": "https://example.com/active",
        "tab_id": "w:https://example.com/",
        "target_id": "target-active",
    }


def test_open_tab_roundtrip_preserves_result_url(monkeypatch):
    _install_roundtrip(
        monkeypatch,
        {
            "ok": True,
            "url": "https://example.com/",
            "tab_id": "w:https://example.com/",
            "target_id": "target-opened",
            "created": False,
            "reused": True,
        },
    )
    result = webtab.request_open_tab("https://example.com/", timeout=0.1)
    assert result["url"] == "https://example.com/"
    assert result["tab_id"] == "w:https://example.com/"
    assert result["target_id"] == "target-opened"
    assert result["created"] is False
    assert result["reused"] is True


def test_webtab_reply_timeout_has_a_structured_reason_after_send():
    sent = []

    result = webtab._wait_for_reply(
        {"op": "open", "url": "https://example.com/", "background": True},
        0,
        send=sent.append,
    )

    assert len(sent) == 1
    assert result == {
        "ok": False,
        "reason_code": webtab.RESPONSE_TIMEOUT_REASON_CODE,
        "error": "timeout: no desktop shell replied within 0s",
    }
    assert webtab._pending == {}


@pytest.mark.parametrize("ownership", [
    {"created": True},
    {"reused": False},
    {"created": True, "reused": True},
    {"created": False, "reused": False},
    {"created": 1, "reused": False},
    {"created": True, "reused": 0},
])
def test_open_tab_roundtrip_drops_invalid_ownership(monkeypatch, ownership):
    _install_roundtrip(
        monkeypatch,
        {
            "ok": True,
            "url": "https://example.com/",
            "tab_id": "w:https://example.com/",
            "target_id": "target-opened",
            **ownership,
        },
    )

    result = webtab.request_open_tab("https://example.com/", timeout=0.1)

    assert "created" not in result
    assert "reused" not in result


class _Page:
    def __init__(self, url: str, target_id: str):
        self.url = url
        self.target_id = target_id
        self.context = _Context()

    def set_default_timeout(self, _ms: int) -> None:
        return None


class _CDPSession:
    def __init__(self, target_id: str):
        self.target_id = target_id
        self.detached = False

    def send(self, method: str):
        assert method == "Target.getTargetInfo"
        return {"targetInfo": {"targetId": self.target_id}}

    def detach(self):
        self.detached = True


class _Context:
    def new_cdp_session(self, page: _Page):
        return _CDPSession(page.target_id)


def test_request_open_tab_registers_binding_on_success(monkeypatch):
    _install_roundtrip(
        monkeypatch,
        {
            "ok": True,
            "window_id": "win-1",
            "url": "https://example.com/",
            "tab_id": "w:https://example.com/",
            "target_id": "target-opened",
        },
    )
    result = webtab.request_open_tab("https://example.com/", timeout=0.1)
    binding_id = result["binding_id"]
    assert binding_id in webtab._bindings
    assert webtab._bindings[binding_id][1:4] == (
        "win-1",
        "w:https://example.com/",
        "target-opened",
    )


def test_bound_screenshot_preserves_exact_page_without_activation(monkeypatch):
    owner = object()
    binding_id = webtab.register_binding(
        owner,
        "win-1",
        "tab-1",
        "target-1",
        geometry_revision=9,
        allow_background=True,
    )
    revisions = webtab.binding_revisions(binding_id)
    sent = []
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda ws, command, timeout=5.0: sent.append(
            (ws, command, timeout)
        ) or {
            "ok": True,
            "window_id": "win-1",
            "tab_id": "tab-1",
            "geometry_revision": 9,
            "image_data_url": "data:image/png;base64,cG5n",
        },
    )

    result = webtab.request_bound_screenshot(
        binding_id,
        expected_page_revision=revisions["page_revision"],
        expected_access_revision=revisions["access_revision"],
        expected_geometry_revision=9,
    )

    assert result["ok"] is True
    assert result["image_data_url"] == "data:image/png;base64,cG5n"
    assert sent == [(owner, {
        "op": "screenshot",
        "window_id": "win-1",
        "tab_id": "tab-1",
        "expected_geometry_revision": 9,
    }, 5.0)]


def test_bound_screenshot_roundtrip_preserves_validated_png(monkeypatch):
    owner = object()
    binding_id = webtab.register_binding(
        owner,
        "win-1",
        "tab-1",
        "target-1",
        geometry_revision=9,
        allow_background=True,
    )
    revisions = webtab.binding_revisions(binding_id)

    def request_on_ws(ws, command, timeout=5.0):
        def send(payload: str):
            data = json.loads(payload)["data"]
            asyncio.run(webtab.handle_webtab_result(ws, {
                "req_id": data["req_id"],
                "ok": True,
                "window_id": "win-1",
                "tab_id": "tab-1",
                "geometry_revision": 9,
                "image_data_url": _PNG_DATA_URL,
            }))

        return webtab._wait_for_reply(
            command,
            timeout,
            expected_ws=ws,
            send=send,
        )

    monkeypatch.setattr(webtab, "request_on_ws", request_on_ws)

    result = webtab.request_bound_screenshot(
        binding_id,
        expected_page_revision=revisions["page_revision"],
        expected_access_revision=revisions["access_revision"],
        expected_geometry_revision=9,
    )

    assert result == {
        "ok": True,
        "error": None,
        "window_id": "win-1",
        "tab_id": "tab-1",
        "image_data_url": _PNG_DATA_URL,
        "geometry_revision": 9,
    }


@pytest.mark.parametrize("value", [
    "data:image/jpeg;base64,iVBORw0KGgo=",
    "data:image/png;base64,not-base64!",
    "data:image/png;base64,cG5n",
])
def test_webtab_result_rejects_invalid_png_data_urls(value):
    result = webtab._wait_for_reply(
        {"op": "screenshot"},
        0.1,
        expected_ws=_ROUNDTRIP_WS,
        send=lambda payload: asyncio.run(webtab.handle_webtab_result(
            _ROUNDTRIP_WS,
            {
                "req_id": json.loads(payload)["data"]["req_id"],
                "ok": True,
                "image_data_url": value,
            },
        )),
    )

    assert result["ok"] is False
    assert result["error"] == "invalid desktop web tab screenshot"
    assert "image_data_url" not in result


def test_png_data_url_size_is_bounded(monkeypatch):
    monkeypatch.setattr(
        webtab,
        "_MAX_SCREENSHOT_DATA_URL_CHARS",
        len(_PNG_DATA_URL) - 1,
    )
    assert webtab._validated_png_data_url(_PNG_DATA_URL) is None


def test_desktop_activation_waits_for_navigation_before_target_receipt():
    source = read_desktop_source(REPO_ROOT)
    start = source.index("async function activateView")
    end = source.index("function withView", start)
    activate = source[start:end]
    assert "await navigateView(ctx, id, url)" in activate
    assert activate.index("await navigateView(ctx, id, url)") < activate.index(
        "devToolsTargetId(record.view.webContents)"
    )


def test_desktop_target_id_uses_electron_debugger_api():
    source = read_desktop_source(REPO_ROOT)
    assert "getOrCreateDevToolsTargetId" not in source
    assert 'client.sendCommand("Target.getTargetInfo")' in source


def test_desktop_navigation_deduplicates_same_pending_url():
    source = read_desktop_source(REPO_ROOT)
    start = source.index("function loadView")
    end = source.index("function ensureView", start)
    load_view = source[start:end]
    assert "record.navigation" in load_view
    assert "pending.url === url" in load_view
    assert "return pending.promise" in load_view


def test_desktop_activation_does_not_restore_a_tab_changed_while_loading():
    source = read_desktop_source(REPO_ROOT)
    start = source.index("async function activateView")
    end = source.index("function withView", start)
    activate = source[start:end]
    show_index = activate.index("showView(ctx, id)")
    navigate_index = activate.index("await navigateView(ctx, id, url)")
    guard_index = activate.index(
        "if (recordFor(ctx, id) !== record || !ctx.visibleViewIds.has(id)) return null"
    )
    target_index = activate.index("devToolsTargetId(record.view.webContents)")
    assert show_index < navigate_index < guard_index < target_index


def test_desktop_native_navigation_helpers_clear_pending_records():
    source = read_desktop_source(REPO_ROOT)
    # The actual reload callback is exercised by Desktop's VM check.
    # Both native helpers must explicitly invalidate their pending record.
    start = source.index("function destroyView")
    end = source.index("function clearOwnedViews", start)
    assert "record.navigation = null" in source[start:end]
    start = source.index("function runNativeNavigation")
    end = source.index("\nfunction", start + 1)
    assert "record.navigation = null" in source[start:end]


def test_desktop_transfer_acceptance_page_uses_csp_compatible_handlers():
    source = (REPO_ROOT / "apps" / "web" / "public" / "desktop-transfer-acceptance.html").read_text(
        encoding="utf-8"
    )
    assert "onclick=" not in source
    assert "pushBtn').addEventListener('click'" in source
    assert "backBtn').addEventListener('click'" in source


def test_request_close_tab_sends_on_bound_ws_and_drops_binding(monkeypatch):
    ws = object()
    binding_id = webtab.register_binding(ws, "win-1", "tab-1", "target-1")
    seen = []

    def request(owner, command, timeout=5.0):
        seen.append((owner, command, timeout))
        return {"ok": True, "tab_id": command["tab_id"]}

    monkeypatch.setattr(webtab, "request_on_ws", request)
    try:
        result = webtab.request_close_tab(binding_id, timeout=0.2)
        assert result == {"ok": True, "tab_id": "tab-1"}
        assert seen == [(ws, {"op": "close", "tab_id": "tab-1"}, 0.2)]
        assert binding_id not in webtab._bindings
    finally:
        webtab.release_binding(binding_id)


def test_request_close_tab_rejects_missing_binding():
    result = webtab.request_close_tab("surface_missing", timeout=0.1)
    assert result["ok"] is False
    assert result["error"] == "surface binding is unavailable"
    assert result["reason_code"] == "page_context_stale"


def test_renderer_control_contract_targets_ready_session_split():
    source = read_desktop_bridge_source(REPO_ROOT)
    assert "id = state.openWebTabInSplit(d.url)" in source
    assert "await waitForWebTabReady(id, 2000)" in source
    assert "if (ready && tab?.kind === \"web\")" in source
    assert "bridge.webTab.activate(tab.id, tab.url)" in source
    assert 'd.op === "close"' in source
    assert "closeAgentWebTabResult" in source

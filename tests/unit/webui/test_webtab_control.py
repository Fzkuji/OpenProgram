from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openprogram.programs.tools.web.browser._actions import open_action
from openprogram.webui.ws_actions import webtab


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


def _install_roundtrip(monkeypatch, reply: dict):
    from openprogram.webui import server

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
        },
    )
    result = webtab.request_open_tab("https://example.com/", timeout=0.1)
    assert result["url"] == "https://example.com/"
    assert result["tab_id"] == "w:https://example.com/"
    assert result["target_id"] == "target-opened"


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


def test_app_page_selection_matches_control_plane_target_id():
    expected = _Page("https://example.com/", "target-expected")
    other = _Page("https://example.org/", "target-other")
    page, error = open_action._choose_app_page(
        [other, expected],
        target_id="target-expected",
    )
    assert page is expected
    assert error is None


def test_app_page_selection_rejects_duplicate_target_ids():
    first = _Page("https://example.com/", "target-duplicate")
    second = _Page("https://example.org/", "target-duplicate")
    page, error = open_action._choose_app_page(
        [first, second],
        target_id="target-duplicate",
    )
    assert page is None
    assert "ambiguous" in (error or "")


def test_app_page_selection_rejects_missing_target_id():
    old = _Page("https://example.org/", "target-old")
    created = _Page("https://example.com/redirected", "target-created")
    page, error = open_action._choose_app_page(
        [old, created],
        target_id="target-missing",
    )
    assert page is None
    assert error is None


def test_app_page_selection_ignores_one_concurrent_new_page():
    active = _Page("https://example.com/active", "target-active")
    concurrent = _Page("https://example.org/concurrent", "target-concurrent")
    page, error = open_action._choose_app_page(
        [active, concurrent],
        target_id="target-active",
    )
    assert page is active
    assert error is None


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


def test_open_url_then_close_sends_request_close_tab(monkeypatch):
    from unittest.mock import MagicMock
    import sys

    from openprogram.programs.tools.web.browser import browser as tool
    from openprogram.programs.tools.web.browser import _chrome_bootstrap as boot
    from openprogram.programs.tools.web.browser._actions import lifecycle
    from openprogram.programs.tools.web.browser._actions import open_action

    page = _Page("https://example.com/", "target-opened")

    class _Browser:
        contexts = [type("Ctx", (), {"pages": [page]})()]

    class _PW:
        chromium = type("Cr", (), {
            "connect_over_cdp": staticmethod(lambda _endpoint: _Browser()),
        })()

        def stop(self):
            self.stopped = True

    class _Sync:
        def start(self):
            return _PW()

    fake_module = MagicMock()
    fake_module.sync_playwright = lambda: _Sync()
    monkeypatch.setitem(sys.modules, "playwright", MagicMock())
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)
    monkeypatch.setattr(boot, "desktop_app_ws_url", lambda: "ws://app")

    closed: list[str] = []
    monkeypatch.setattr(
        webtab,
        "request_open_tab",
        lambda url, timeout=15.0: {
            "ok": True,
            "url": url,
            "tab_id": "w:https://example.com/",
            "target_id": "target-opened",
            "window_id": "win-1",
            "binding_id": "surface_from_open",
        },
    )
    monkeypatch.setattr(
        webtab,
        "request_close_tab",
        lambda binding_id, timeout=5.0: closed.append(binding_id) or {"ok": True},
    )

    out = open_action._open_app_session(
        "http://cdp",
        url="https://example.com/",
        timeout_ms=1000,
        strict=True,
    )
    assert out is not None and out.startswith("Opened")
    sid = out.split("`")[1]
    session = tool._sessions[sid]
    assert session["app_agent_opened"] is True
    assert session["app_binding_id"] == "surface_from_open"

    close_out = lifecycle._close(sid)
    assert closed == ["surface_from_open"]
    assert "Closed the desktop page" in close_out
    assert sid not in tool._sessions


def test_app_attach_matches_control_plane_target_across_all_electron_pages():
    source = (
        REPO_ROOT
        / "openprogram"
        / "programs"
        / "tools"
        / "web"
        / "browser"
        / "_actions"
        / "open_action.py"
    ).read_text(encoding="utf-8")
    start = source.index("def _open_app_session")
    end = source.index("\ndef _open(", start)
    attach = source[start:end]
    assert "_choose_app_page(\n            _all_pages()," in attach


def test_desktop_activation_waits_for_navigation_before_target_receipt():
    source = (REPO_ROOT / "apps" / "desktop" / "main.js").read_text(encoding="utf-8")
    start = source.index("async function activateView")
    end = source.index("\nfunction withView", start)
    activate = source[start:end]
    assert "await navigateView(ctx, id, url)" in activate
    assert activate.index("await navigateView(ctx, id, url)") < activate.index(
        "devToolsTargetId(record.view.webContents)"
    )


def test_desktop_target_id_uses_electron_debugger_api():
    source = (REPO_ROOT / "apps" / "desktop" / "main.js").read_text(encoding="utf-8")
    assert "getOrCreateDevToolsTargetId" not in source
    assert 'client.sendCommand("Target.getTargetInfo")' in source


def test_desktop_navigation_deduplicates_same_pending_url():
    source = (REPO_ROOT / "apps" / "desktop" / "main.js").read_text(encoding="utf-8")
    start = source.index("function loadView")
    end = source.index("function ensureView", start)
    load_view = source[start:end]
    assert "record.navigation" in load_view
    assert "pending.url === url" in load_view
    assert "return pending.promise" in load_view


def test_desktop_activation_does_not_restore_a_tab_changed_while_loading():
    source = (REPO_ROOT / "apps" / "desktop" / "main.js").read_text(encoding="utf-8")
    start = source.index("async function activateView")
    end = source.index("\nfunction withView", start)
    activate = source[start:end]
    show_index = activate.index("showView(ctx, id)")
    navigate_index = activate.index("await navigateView(ctx, id, url)")
    guard_index = activate.index(
        "if (recordFor(ctx, id) !== record || !ctx.visibleViewIds.has(id)) return null"
    )
    target_index = activate.index("devToolsTargetId(record.view.webContents)")
    assert show_index < navigate_index < guard_index < target_index


def test_desktop_renderer_reload_discards_pending_native_navigations():
    source = (REPO_ROOT / "apps" / "desktop" / "main.js").read_text(encoding="utf-8")
    # Renderer reload destroys every view owned by the window, which
    # drops their pending-navigation records with them (destroyView
    # nulls record.navigation). runNativeNavigation likewise clears the
    # record before reload/history calls that bypass loadView.
    assert 'win.webContents.on("did-navigate", () => clearOwnedViews(ctx))' in source
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


def test_close_app_agent_opened_page_requests_close_tab(monkeypatch):
    from openprogram.programs.tools.web.browser import browser as tool
    from openprogram.programs.tools.web.browser._actions import lifecycle

    class _PW:
        def stop(self):
            self.stopped = True

    seen = []
    monkeypatch.setattr(
        webtab,
        "request_close_tab",
        lambda binding_id, timeout=5.0: seen.append(binding_id) or {"ok": True},
    )
    tool._sessions["br_agent"] = {
        "is_cdp": True,
        "is_app": True,
        "app_tab_id": "w:https://example.com/",
        "app_agent_opened": True,
        "app_binding_id": "surface_abc",
        "playwright": _PW(),
    }
    try:
        out = lifecycle._close("br_agent")
        assert seen == ["surface_abc"]
        assert "Closed the desktop page" in out
        assert "br_agent" not in tool._sessions
    finally:
        tool._sessions.pop("br_agent", None)


def test_close_app_reused_page_detaches_only(monkeypatch):
    from openprogram.programs.tools.web.browser import browser as tool
    from openprogram.programs.tools.web.browser._actions import lifecycle

    class _PW:
        def stop(self):
            self.stopped = True

    seen = []
    monkeypatch.setattr(
        webtab,
        "request_close_tab",
        lambda binding_id, timeout=5.0: seen.append(binding_id) or {"ok": True},
    )
    tool._sessions["br_reuse"] = {
        "is_cdp": True,
        "is_app": True,
        "app_tab_id": "w:https://example.com/",
        "app_agent_opened": False,
        "app_binding_id": "surface_abc",
        "playwright": _PW(),
    }
    try:
        out = lifecycle._close("br_reuse")
        assert seen == []
        assert "stays open" in out
        assert "br_reuse" not in tool._sessions
    finally:
        tool._sessions.pop("br_reuse", None)


def test_renderer_control_contract_targets_ready_session_split():
    source = (REPO_ROOT / "apps" / "web" / "lib" / "desktop-bridge.ts").read_text(
        encoding="utf-8"
    )
    assert "id = state.openWebTabInSplit(d.url)" in source
    assert "await waitForWebTabReady(id, 2000)" in source
    assert "if (ready && tab?.kind === \"web\")" in source
    assert "bridge.webTab.activate(tab.id, tab.url)" in source
    assert 'd.op === "close"' in source
    assert "closeAgentWebTabResult" in source

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openprogram.functions.tools.browser._actions import open_action
from openprogram.webui.ws_actions import webtab


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_pending():
    webtab._pending.clear()
    yield
    webtab._pending.clear()


def _install_roundtrip(monkeypatch, reply: dict):
    from openprogram.webui import server

    monkeypatch.setattr(server, "_ws_connections", {object()})

    def broadcast(payload: str):
        command = json.loads(payload)["data"]
        asyncio.run(
            webtab.handle_webtab_result(
                None,
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


def test_desktop_activation_waits_for_navigation_before_target_receipt():
    source = (REPO_ROOT / "desktop" / "main.js").read_text(encoding="utf-8")
    start = source.index("async function activateView")
    end = source.index("\nfunction withView", start)
    activate = source[start:end]
    assert "await navigateView(id, url)" in activate
    assert activate.index("await navigateView(id, url)") < activate.index(
        "getOrCreateDevToolsTargetId()"
    )


def test_desktop_navigation_deduplicates_same_pending_url():
    source = (REPO_ROOT / "desktop" / "main.js").read_text(encoding="utf-8")
    start = source.index("function loadView")
    end = source.index("\nfunction ensureView", start)
    load_view = source[start:end]
    assert "viewNavigations.get(id)" in load_view
    assert "pending.url === url" in load_view
    assert "return pending.promise" in load_view


def test_desktop_activation_does_not_restore_a_tab_changed_while_loading():
    source = (REPO_ROOT / "desktop" / "main.js").read_text(encoding="utf-8")
    start = source.index("async function activateView")
    end = source.index("\nfunction withView", start)
    activate = source[start:end]
    show_index = activate.index("showView(id)")
    navigate_index = activate.index("await navigateView(id, url)")
    guard_index = activate.index("if (visibleViewId !== id) return null")
    target_index = activate.index("getOrCreateDevToolsTargetId()")
    assert show_index < navigate_index < guard_index < target_index


def test_desktop_renderer_reload_discards_pending_native_navigations():
    source = (REPO_ROOT / "desktop" / "main.js").read_text(encoding="utf-8")
    start = source.index('mainWindow.webContents.on("did-navigate"')
    end = source.index("\n  mainWindow.loadURL", start)
    reload_cleanup = source[start:end]
    assert "viewNavigations.clear()" in reload_cleanup


def test_renderer_control_contract_targets_ready_session_split():
    source = (REPO_ROOT / "web" / "lib" / "desktop-bridge.ts").read_text(
        encoding="utf-8"
    )
    assert "state.openWebTabInSplit(d.url)" in source
    assert "state.splitWebTabId" in source
    assert "isWebTabReady(split.id)" in source
    assert "waitForWebTabReady(id, 2000)" in source

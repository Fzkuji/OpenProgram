from __future__ import annotations

import asyncio

import pytest

from openprogram.interfaces.server.ws_actions import webtab


@pytest.fixture(autouse=True)
def isolated_page_inventory(monkeypatch):
    for name in ("_bindings", "_connection_revisions", "_page_revisions", "_desktop_windows"):
        monkeypatch.setattr(webtab, name, {})


class _WS:
    def __init__(self, name="owner", *, trusted=False):
        from openprogram.runtime.permissions.authority import local_owner_authority
        self.scope = {"state": {"authority": local_owner_authority()}} if trusted else {"state": {}}
        self.name = name
        self.window_id = name


def test_register_binding_adopts_restoring_predecessor(tmp_path, monkeypatch):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    webtab._bindings.clear()
    webtab._connection_revisions.clear()
    webtab._page_revisions.clear()
    webtab._desktop_windows.clear()
    store = BrowserResourceStore()
    store.retain(
        page_key="page:dead:9", window_id="win", tab_id="tab-a",
        title="arXiv", target="https://arxiv.org/abs/1",
        session_id="parent", conversation_session_id="parent", live=True,
    )
    store.mark_restoring("page:dead:9")
    owner = _WS()
    webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = "win"
    binding_id = webtab.register_binding(owner, "win", "tab-a", "target-live")
    new_key = webtab.binding_page_key(binding_id)
    assert new_key != "page:dead:9"
    successor = store.get_resource(new_key)
    pred = store.get_resource("page:dead:9")
    assert successor["lifecycle"] == "connected"
    assert successor["control_state"] == "idle"
    assert successor["title"] == "arXiv"
    assert pred["lifecycle"] == "superseded"
    webtab.register_binding(owner, "win", "tab-a", "target-live")
    assert store.get_resource(new_key)["lifecycle"] == "connected"
    webtab.release_binding(binding_id)


def test_closed_rebind_does_not_adopt(tmp_path, monkeypatch):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    webtab._bindings.clear()
    webtab._connection_revisions.clear()
    webtab._page_revisions.clear()
    webtab._desktop_windows.clear()
    owner = _WS()
    webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = "win"
    binding_id = webtab.register_binding(owner, "win", "tab-a", "target-1")
    page_key = webtab.binding_page_key(binding_id)
    store = BrowserResourceStore()
    store.retain(
        page_key=page_key, window_id="win", tab_id="tab-a", title="Plans",
        target="https://example.test/",
        session_id="parent", conversation_session_id="parent", live=True,
    )
    owner = _WS(trusted=True)
    webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = "win"
    binding_id = webtab.register_binding(owner, "win", "tab-a", "target-1")
    page_key = webtab.binding_page_key(binding_id)
    store = BrowserResourceStore()
    store.retain(
        page_key=page_key, window_id="win", tab_id="tab-a", title="Plans",
        target="https://example.test/",
        session_id="parent", conversation_session_id="parent", live=True,
    )
    asyncio.run(webtab.handle_webtab_closed(owner, {"window_id": "win", "tab_id": "tab-a"}))
    webtab._desktop_windows[owner] = "win"
    rebound = webtab.register_binding(owner, "win", "tab-a", "target-1")
    new_key = webtab.binding_page_key(rebound)
    assert store.get_resource(page_key)["lifecycle"] == "closed"
    assert store.associations_for_page(new_key) == [] or all(
        item.get("resource_id") != page_key for item in store.associations_for_page(new_key)
    )
    webtab.release_binding(rebound)



def test_paused_same_target_register_keeps_control(tmp_path, monkeypatch):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    webtab._bindings.clear()
    webtab._connection_revisions.clear()
    webtab._page_revisions.clear()
    webtab._desktop_windows.clear()
    owner = _WS()
    webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = "win"
    binding_id = webtab.register_binding(owner, "win", "tab-a", "target-1")
    page_key = webtab.binding_page_key(binding_id)
    store = BrowserResourceStore()
    store.set_control_state(page_key, "paused", execution_id="exec-1")
    again = webtab.register_binding(owner, "win", "tab-a", "target-1")
    assert webtab.binding_page_key(again) == page_key
    row = store.get_resource(page_key)
    assert row["control_state"] == "paused"
    assert row["lifecycle"] == "connected"
    webtab.release_binding(again)


def test_untrusted_register_does_not_mint_restore(tmp_path, monkeypatch):
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    called = []
    monkeypatch.setattr(webtab, "restore_window_pages", lambda *a, **k: called.append(True))
    owner = _WS()
    asyncio.run(webtab.handle_webtab_register(owner, {"window_id": "win"}))
    assert called == []


def test_trusted_restore_skips_without_native_target(tmp_path, monkeypatch):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    webtab._bindings.clear()
    webtab._connection_revisions.clear()
    webtab._page_revisions.clear()
    webtab._desktop_windows.clear()
    store = BrowserResourceStore()
    store.retain(
        page_key="page:dead:1", window_id="win", tab_id="tab-a",
        title="X", target="https://example.test/",
        session_id="parent", conversation_session_id="parent", live=True,
    )
    store.mark_restoring("page:dead:1")
    from openprogram.runtime.permissions.authority import local_owner_authority
    owner = _WS()
    owner.scope = {"state": {"authority": local_owner_authority()}}
    webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = "win"
    monkeypatch.setattr(webtab, "request_on_ws", lambda *a, **k: {
        "ok": True,
        "pages": [{"tab_id": "tab-a", "target_id": None}],
    })
    webtab.restore_window_pages(owner, "win")
    assert store.get_resource("page:dead:1")["lifecycle"] == "restore_failed"

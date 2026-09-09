from __future__ import annotations

import asyncio
from types import SimpleNamespace

from openprogram.webui.ws_actions import webtab


class _WS:
    def __init__(self, name="owner", *, trusted=False):
        from openprogram.agent.authority import local_owner_authority
        self.scope = {"state": {"authority": local_owner_authority()}} if trusted else {"state": {}}
        self.name = name
        self.window_id = name


def test_register_binding_adopts_restoring_predecessor(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
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
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
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


def test_fresh_successor_accepts_first_human_input(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore, writes_fenced

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    webtab._bindings.clear()
    webtab._connection_revisions.clear()
    webtab._page_revisions.clear()
    webtab._desktop_windows.clear()
    owner = _WS(trusted=True)
    webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = "win"
    old = webtab.register_binding(owner, "win", "tab-a", "target-old")
    old_key = webtab.binding_page_key(old)
    store = BrowserResourceStore()
    store.retain(
        page_key=old_key, window_id="win", tab_id="tab-a", title="X",
        target="https://example.test/", session_id="parent",
        conversation_session_id="parent", live=True,
    )
    store.set_control_state(old_key, "idle", input_seq=50)
    store.mark_restoring(old_key)
    webtab.release_binding(old)
    pauses = []
    fenced_during_pause = []
    rebound = webtab.register_binding(owner, "win", "tab-a", "target-new")
    new_key = webtab.binding_page_key(rebound)
    assert new_key != old_key
    assert int((store.get_resource(new_key) or {}).get("last_input_seq") or 0) == 0

    async def fake_pause(*args, **kwargs):
        pauses.append(kwargs)
        fenced_during_pause.append(writes_fenced(new_key))
        return None

    monkeypatch.setattr("openprogram.browser_resources.request_page_pause", fake_pause)
    asyncio.run(webtab.handle_webtab_human_input(owner, {
        "window_id": "win", "tab_id": "tab-a", "sequence": 1, "kind": "pointer",
    }))
    assert fenced_during_pause == [True]
    assert len(pauses) == 1
    assert int((store.get_resource(new_key) or {}).get("last_input_seq") or 0) == 1
    stale = _WS(trusted=True)
    webtab.ensure_connection_revision(stale)
    webtab._desktop_windows[stale] = "other"
    asyncio.run(webtab.handle_webtab_human_input(stale, {
        "window_id": "win", "tab_id": "tab-a", "sequence": 1, "kind": "pointer",
    }))
    assert len(pauses) == 1
    webtab.release_binding(rebound)


def test_paused_same_target_register_keeps_control(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
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
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    called = []
    monkeypatch.setattr(webtab, "restore_window_pages", lambda *a, **k: called.append(True))
    owner = _WS()
    asyncio.run(webtab.handle_webtab_register(owner, {"window_id": "win"}))
    assert called == []


def test_trusted_restore_skips_without_native_target(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
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
    from openprogram.agent.authority import local_owner_authority
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

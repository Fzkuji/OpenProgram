import asyncio
from types import SimpleNamespace

import pytest

from openprogram.webui.ws_actions import webtab


class _WS:
    def __init__(self, window_id="win"):
        from openprogram.agent.authority import local_owner_authority
        self.scope = {"state": {"authority": local_owner_authority()}}
        self.sent = []
        self.window_id = window_id

    async def send_text(self, payload: str):
        self.sent.append(payload)


@pytest.fixture(autouse=True)
def _clean_webtab():
    webtab._pending.clear()
    webtab._bindings.clear()
    webtab._desktop_windows.clear()
    webtab._connection_revisions.clear()
    webtab._page_revisions.clear()
    webtab._restore_jobs.clear()
    yield
    webtab._pending.clear()
    webtab._bindings.clear()
    webtab._desktop_windows.clear()
    webtab._connection_revisions.clear()
    webtab._page_revisions.clear()
    webtab._restore_jobs.clear()


@pytest.mark.parametrize("kind", ["pointer", "key", "scroll", "navigate"])
def test_human_input_uses_originating_socket_and_tab_not_client_owner(monkeypatch, tmp_path, kind):
    from openprogram.browser_resources import writes_fenced

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    owner = _WS()
    other = _WS("other")
    webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = "win"
    webtab._desktop_windows[other] = "other"
    binding_id = webtab.register_binding(owner, "win", "tab-a", "target-1")
    page_key = webtab.binding_page_key(binding_id)
    from openprogram.browser_resources import BrowserResourceStore
    BrowserResourceStore().retain(
        page_key=page_key, window_id="win", tab_id="tab-a",
        session_id="parent", conversation_session_id="parent", execution_id="exec-1",
        live=True,
    )
    pauses = []

    async def fake_pause(*args, **kwargs):
        pauses.append(kwargs)
        return SimpleNamespace(delivered=True, execution=SimpleNamespace(
            status=SimpleNamespace(value="pausing"),
        ))

    monkeypatch.setattr(
        "openprogram.browser_resources.request_page_pause", fake_pause,
    )
    asyncio.run(webtab.handle_webtab_human_input(other, {
        "window_id": "win", "tab_id": "tab-a", "sequence": 1, "kind": kind,
        "execution_id": "forged", "resource_id": "forged",
    }))
    assert writes_fenced(page_key) is False
    asyncio.run(webtab.handle_webtab_human_input(owner, {
        "window_id": "win", "tab_id": "tab-a", "kind": kind,
    }))
    assert writes_fenced(page_key) is False
    asyncio.run(webtab.handle_webtab_human_input(owner, {
        "window_id": "win", "tab_id": "tab-a", "sequence": 1, "kind": "bogus",
    }))
    assert writes_fenced(page_key) is False
    asyncio.run(webtab.handle_webtab_human_input(owner, {
        "window_id": "win", "tab_id": "tab-a", "sequence": 1, "kind": kind,
        "execution_id": "forged", "resource_id": "forged",
    }))
    assert writes_fenced(page_key) is True
    asyncio.run(webtab.handle_webtab_human_input(owner, {
        "window_id": "win", "tab_id": "tab-a", "sequence": 2, "kind": kind,
    }))
    assert len(pauses) == 1


def test_webtab_closed_marks_descriptor_closed(monkeypatch, tmp_path):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    owner = _WS()
    webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = "win"
    binding_id = webtab.register_binding(owner, "win", "tab-a", "target-1")
    page_key = webtab.binding_page_key(binding_id)
    asyncio.run(webtab.handle_webtab_closed(owner, {"window_id": "win", "tab_id": "tab-a"}))
    row = BrowserResourceStore().get_resource(page_key)
    assert row is not None
    assert row["live"] == 0
    assert row["control_state"] == "closed"


def test_webtab_closed_rebind_mints_new_page_identity(monkeypatch, tmp_path):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    owner = _WS()
    webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = "win"
    binding_id = webtab.register_binding(owner, "win", "tab-a", "target-1")
    page_key = webtab.binding_page_key(binding_id)
    store = BrowserResourceStore()
    store.retain(
        page_key=page_key, window_id="win", tab_id="tab-a", title="Plans",
        target="https://example.test/", connection_generation=1,
        session_id="parent", conversation_session_id="parent", execution_id="exec-1",
        live=True,
    )
    asyncio.run(webtab.handle_webtab_closed(owner, {"window_id": "win", "tab_id": "tab-a"}))
    closed = store.get_resource(page_key)
    assert int(closed["live"] or 0) == 0
    assert closed["control_state"] == "closed"
    webtab._desktop_windows[owner] = "win"
    rebound = webtab.register_binding(owner, "win", "tab-a", "target-1")
    new_key = webtab.binding_page_key(rebound)
    assert new_key != page_key
    revived = store.get_resource(page_key)
    assert int(revived["live"] or 0) == 0
    assert revived["control_state"] == "closed"
    webtab.release_binding(rebound)


def _owner_page(tmp_path, monkeypatch, *, tab_id="tab-a", target_id="target-1"):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    owner = _WS()
    webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = "win"
    binding_id = webtab.register_binding(owner, "win", tab_id, target_id)
    page_key = webtab.binding_page_key(binding_id)
    store = BrowserResourceStore()
    store.retain(
        page_key=page_key, window_id="win", tab_id=tab_id, title="Plans",
        target="https://example.test/",
        connection_generation=webtab._connection_revisions[owner],
        session_id="parent", conversation_session_id="parent", execution_id="exec-1",
        live=True,
    )
    return owner, binding_id, page_key, store


def test_release_binding_keeps_retained_page_live(monkeypatch, tmp_path):
    from openprogram.browser_resources import writes_fenced

    owner, binding_id, page_key, store = _owner_page(tmp_path, monkeypatch)
    webtab.release_binding(binding_id)
    row = store.get_resource(page_key)
    assert int(row["live"] or 0) == 1
    assert row["lifecycle"] == "connected"
    assert row["control_state"] != "closed"
    assert writes_fenced(page_key) is False
    assert page_key in store.page_keys_for_tab("win", "tab-a")
    assert binding_id not in webtab._bindings


@pytest.mark.parametrize("kind", ["pointer", "key", "scroll", "navigate"])
def test_human_input_fences_retained_page_after_release_binding(
    monkeypatch, tmp_path, kind,
):
    from openprogram.browser_resources import writes_fenced

    owner, binding_id, page_key, store = _owner_page(tmp_path, monkeypatch)
    pauses = []

    async def fake_pause(*args, **kwargs):
        pauses.append(kwargs)
        return SimpleNamespace(delivered=True, execution=SimpleNamespace(
            status=SimpleNamespace(value="pausing"),
        ))

    monkeypatch.setattr(
        "openprogram.browser_resources.request_page_pause", fake_pause,
    )
    webtab.release_binding(binding_id)
    asyncio.run(webtab.handle_webtab_human_input(owner, {
        "window_id": "win", "tab_id": "tab-a", "sequence": 1, "kind": kind,
    }))
    assert writes_fenced(page_key) is True
    assert len(pauses) == 1
    assert int(store.get_resource(page_key)["live"] or 0) == 1


def test_webtab_closed_marks_retained_page_after_release_binding(monkeypatch, tmp_path):
    owner, binding_id, page_key, store = _owner_page(tmp_path, monkeypatch)
    webtab.release_binding(binding_id)
    asyncio.run(webtab.handle_webtab_closed(owner, {
        "window_id": "win", "tab_id": "tab-a",
    }))
    closed = store.get_resource(page_key)
    assert int(closed["live"] or 0) == 0
    assert closed["control_state"] == "closed"
    assert closed["lifecycle"] == "closed"


def test_closed_after_release_binding_rebind_mints_new_page_identity(
    monkeypatch, tmp_path,
):
    owner, binding_id, page_key, store = _owner_page(tmp_path, monkeypatch)
    webtab.release_binding(binding_id)
    asyncio.run(webtab.handle_webtab_closed(owner, {
        "window_id": "win", "tab_id": "tab-a",
    }))
    webtab._desktop_windows[owner] = "win"
    rebound = webtab.register_binding(owner, "win", "tab-a", "target-1")
    new_key = webtab.binding_page_key(rebound)
    assert new_key != page_key
    revived = store.get_resource(page_key)
    assert int(revived["live"] or 0) == 0
    assert revived["control_state"] == "closed"
    webtab.release_binding(rebound)


def test_foreign_socket_cannot_fence_or_close_retained_page(monkeypatch, tmp_path):
    from openprogram.browser_resources import writes_fenced

    owner, binding_id, page_key, store = _owner_page(tmp_path, monkeypatch)
    other = _WS("other")
    webtab.ensure_connection_revision(other)
    webtab._desktop_windows[other] = "other"
    pauses = []

    async def fake_pause(*args, **kwargs):
        pauses.append(kwargs)
        return None

    monkeypatch.setattr(
        "openprogram.browser_resources.request_page_pause", fake_pause,
    )
    webtab.release_binding(binding_id)
    asyncio.run(webtab.handle_webtab_human_input(other, {
        "window_id": "win", "tab_id": "tab-a", "sequence": 1, "kind": "pointer",
    }))
    asyncio.run(webtab.handle_webtab_closed(other, {
        "window_id": "win", "tab_id": "tab-a",
    }))
    row = store.get_resource(page_key)
    assert writes_fenced(page_key) is False
    assert pauses == []
    assert int(row["live"] or 0) == 1
    assert row["control_state"] != "closed"


def test_unregistered_socket_does_not_use_persisted_tab_row(monkeypatch, tmp_path):
    from openprogram.browser_resources import writes_fenced

    owner, binding_id, page_key, store = _owner_page(tmp_path, monkeypatch)
    stranger = _WS()
    webtab.release_binding(binding_id)
    asyncio.run(webtab.handle_webtab_human_input(stranger, {
        "window_id": "win", "tab_id": "tab-a", "sequence": 1, "kind": "pointer",
    }))
    asyncio.run(webtab.handle_webtab_closed(stranger, {
        "window_id": "win", "tab_id": "tab-a",
    }))
    row = store.get_resource(page_key)
    assert writes_fenced(page_key) is False
    assert int(row["live"] or 0) == 1


def test_closed_retained_row_does_not_regain_authority(monkeypatch, tmp_path):
    from openprogram.browser_resources import writes_fenced

    owner, binding_id, page_key, store = _owner_page(tmp_path, monkeypatch)
    webtab.release_binding(binding_id)
    store.mark_closed(page_key)
    pauses = []

    async def fake_pause(*args, **kwargs):
        pauses.append(kwargs)
        return None

    monkeypatch.setattr(
        "openprogram.browser_resources.request_page_pause", fake_pause,
    )
    asyncio.run(webtab.handle_webtab_human_input(owner, {
        "window_id": "win", "tab_id": "tab-a", "sequence": 1, "kind": "pointer",
    }))
    assert writes_fenced(page_key) is False
    assert pauses == []
    closed = store.get_resource(page_key)
    assert int(closed["live"] or 0) == 0
    assert closed["control_state"] == "closed"

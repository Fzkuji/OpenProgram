import asyncio

import pytest

from openprogram.interfaces.server.ws_actions import webtab


class _WS:
    def __init__(self, window_id="win"):
        from openprogram.runtime.permissions.authority import local_owner_authority
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



def test_webtab_closed_marks_descriptor_closed(monkeypatch, tmp_path):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
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


def test_page_close_projection_targets_every_association_without_conversation_scan(
    monkeypatch, tmp_path,
):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore, project_page_resource_rows

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    store = BrowserResourceStore()
    store.retain(
        page_key="page:shared", window_id="win", tab_id="tab-a", title="Plans",
        target="https://example.test/", connection_generation=1,
        session_id="parent", conversation_session_id="parent", execution_id="exec-a",
        live=True,
    )
    store.retain(
        page_key="page:shared", window_id="win", tab_id="tab-a", title="Plans",
        target="https://example.test/", connection_generation=1,
        session_id="parent", conversation_session_id="parent", execution_id="exec-b",
        live=True,
    )
    rows = project_page_resource_rows("page:shared")
    assert len(rows) == 2
    assert {row["execution_id"] for row in rows} == {"exec-a", "exec-b"}

    owner = _WS()
    webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = "win"
    webtab.register_binding(owner, "win", "tab-a", "target-1")
    emitted = []
    monkeypatch.setattr(
        "openprogram.programs.applications.browser.pages.emit_browser_resource",
        lambda row, **kwargs: emitted.append(row),
    )
    monkeypatch.setattr(
        "openprogram.programs.applications.browser.pages.project_conversation_resources",
        lambda *_args: (_ for _ in ()).throw(AssertionError("conversation scan")),
    )
    monkeypatch.setattr(
        "openprogram.programs.applications.browser.pages.page_keys_for_socket_tab",
        lambda *_args: ["page:shared"],
    )
    asyncio.run(webtab.handle_webtab_closed(owner, {"window_id": "win", "tab_id": "tab-a"}))
    assert len(emitted) == 2
    assert {row["status"] for row in emitted} == {"closed"}


def test_webtab_closed_rebind_mints_new_page_identity(monkeypatch, tmp_path):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
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
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
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
    owner, binding_id, page_key, store = _owner_page(tmp_path, monkeypatch)
    webtab.release_binding(binding_id)
    row = store.get_resource(page_key)
    assert int(row["live"] or 0) == 1
    assert row["lifecycle"] == "connected"
    assert row["control_state"] != "closed"
    assert page_key in store.page_keys_for_tab("win", "tab-a")
    assert binding_id not in webtab._bindings



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


def test_foreign_socket_cannot_close_retained_page(monkeypatch, tmp_path):
    owner, binding_id, page_key, store = _owner_page(tmp_path, monkeypatch)
    other = _WS("other")
    webtab.ensure_connection_revision(other)
    webtab._desktop_windows[other] = "other"
    webtab.release_binding(binding_id)
    asyncio.run(webtab.handle_webtab_closed(other, {
        "window_id": "win", "tab_id": "tab-a",
    }))
    row = store.get_resource(page_key)
    assert int(row["live"] or 0) == 1
    assert row["control_state"] != "closed"


def test_unregistered_socket_does_not_use_persisted_tab_row(monkeypatch, tmp_path):
    owner, binding_id, page_key, store = _owner_page(tmp_path, monkeypatch)
    stranger = _WS()
    webtab.release_binding(binding_id)
    asyncio.run(webtab.handle_webtab_closed(stranger, {
        "window_id": "win", "tab_id": "tab-a",
    }))
    row = store.get_resource(page_key)
    assert int(row["live"] or 0) == 1

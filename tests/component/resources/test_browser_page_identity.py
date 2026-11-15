"""Exact native Page identity through the retained Page projection."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.interfaces.server.ws_actions import webtab


NATIVE_TAB_ID = "fcb18270-5f88-4f5f-ae23-efb5ed0f4b61"
OTHER_TAB_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
FORGED_TAB_ID = "00000000-1111-2222-3333-444444444444"


@pytest.fixture(autouse=True)
def _clean_webtab(monkeypatch):
    webtab._pending.clear()
    webtab._bindings.clear()
    webtab._desktop_windows.clear()
    webtab._connection_revisions.clear()
    webtab._page_revisions.clear()

    def request_on_ws(_ws, command, timeout=5.0):
        del timeout
        window_id = command.get("window_id")
        tab_id = command.get("tab_id")
        for entry in webtab._bindings.values():
            if entry[1] == window_id and entry[2] == tab_id:
                return {
                    "ok": True,
                    "window_id": entry[1],
                    "tab_id": entry[2],
                    "target_id": entry[3],
                    "geometry_revision": entry[7],
                }
        return {"ok": False, "reason_code": "page_context_stale"}

    monkeypatch.setattr(webtab, "request_on_ws", request_on_ws)
    yield
    webtab._pending.clear()
    webtab._bindings.clear()
    webtab._desktop_windows.clear()
    webtab._connection_revisions.clear()
    webtab._page_revisions.clear()


def _bind_owner(window_id, tab_id, target_id):
    owner = object()
    webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = window_id
    binding_id = webtab.register_binding(owner, window_id, tab_id, target_id)
    return owner, binding_id, webtab.binding_page_key(binding_id)


def _binding_generation(binding_id):
    return int(
        webtab.binding_page_descriptor(binding_id).get("connection_generation") or 0
    )


def _own_execution(monkeypatch, session_id="parent", execution_id="exec-native"):
    monkeypatch.setattr(
        "openprogram.runtime.execution.run_control.get_current_execution_id",
        lambda: execution_id,
    )
    monkeypatch.setattr(
        "openprogram.runtime.execution.run_control.get_current_session_id",
        lambda: session_id,
    )
    monkeypatch.setattr(
        "openprogram.host.processes.current_owner",
        lambda: (session_id, execution_id, None),
    )
    monkeypatch.setattr(
        "openprogram.runtime.execution.default_store",
        lambda: SimpleNamespace(
            get_execution_input=lambda _eid: SimpleNamespace(
                user_message_id="u1", assistant_message_id="a1",
            ),
            get_execution=lambda _eid: None,
        ),
    )


def test_closed_row_display_stays_frozen_after_later_epoch(tmp_path, monkeypatch):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    _own_execution(monkeypatch)
    owner, binding_id, page_key = _bind_owner("win", NATIVE_TAB_ID, "target-1")
    store = BrowserResourceStore()
    store.update_display(
        page_key, title="Kept", target="https://example.com",
        connection_generation=_binding_generation(binding_id),
    )
    webtab.release_binding(binding_id)
    store.mark_closed(page_key)
    closed = store.get_resource(page_key)
    assert closed["lifecycle"] == "closed"
    store.update_display(
        page_key, title="Hacked", target="http://evil.test/",
        connection_generation=99,
    )
    store.retain(
        page_key=page_key, window_id="win-other", tab_id=OTHER_TAB_ID,
        title="Hacked", target="http://evil.test/", connection_generation=99,
        session_id="parent", live=True,
    )
    row = store.get_resource(page_key)
    assert row["lifecycle"] == "closed"
    assert row["tab_id"] == NATIVE_TAB_ID
    assert row["window_id"] == "win"
    assert row["title"] == "Kept"
    assert row["target"] == "https://example.com"
    rebound = webtab.register_binding(owner, "win", NATIVE_TAB_ID, "target-1")
    assert webtab.binding_page_key(rebound) == page_key
    still = store.get_resource(page_key)
    assert still["lifecycle"] == "closed"
    assert int(still["live"] or 0) == 0
    assert still["title"] == "Kept"
    assert still["tab_id"] == NATIVE_TAB_ID


@pytest.mark.parametrize("stale_generation", [0, 1])
def test_retain_zero_or_older_epoch_cannot_overwrite_display(
    tmp_path, monkeypatch, stale_generation,
):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    store.retain(
        page_key="page:epoch", window_id="win", tab_id=NATIVE_TAB_ID,
        title="Current", target="https://current.test/",
        connection_generation=4, live=True,
    )
    store.retain(
        page_key="page:epoch", window_id="win-stale", tab_id=OTHER_TAB_ID,
        title="Stale", target="https://stale.test/",
        connection_generation=stale_generation, live=True,
    )
    row = store.get_resource("page:epoch")
    assert int(row["connection_generation"] or 0) == 4
    assert row["title"] == "Current"
    assert row["target"] == "https://current.test/"
    assert row["tab_id"] == NATIVE_TAB_ID
    assert row["window_id"] == "win"


def test_update_display_omitted_zero_or_older_cannot_overwrite_known_epoch(
    tmp_path, monkeypatch,
):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    store.retain(
        page_key="page:epoch", window_id="win", tab_id=NATIVE_TAB_ID,
        title="Current", target="https://current.test/",
        connection_generation=4, live=True,
    )
    store.update_display(
        "page:epoch", title="Stale", target="https://stale.test/",
    )
    store.update_display(
        "page:epoch", title="Stale", target="https://stale.test/",
        connection_generation=0,
    )
    store.update_display(
        "page:epoch", title="Stale", target="https://stale.test/",
        connection_generation=1,
    )
    row = store.get_resource("page:epoch")
    assert row["title"] == "Current"
    assert row["target"] == "https://current.test/"
    store.update_display(
        "page:epoch", title="Newer", target="https://newer.test/",
        connection_generation=4,
    )
    row = store.get_resource("page:epoch")
    assert row["title"] == "Newer"
    assert row["target"] == "https://newer.test/"


def test_blank_epoch_display_initialization_still_allowed(tmp_path, monkeypatch):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    store.retain(page_key="page:blank", live=True)
    store.update_display(
        "page:blank", title="Initial", target="https://init.test/",
    )
    row = store.get_resource("page:blank")
    assert row["title"] == "Initial"
    assert row["target"] == "https://init.test/"
    store.retain(
        page_key="page:blank", title="Later", target="https://later.test/",
        connection_generation=3, live=True,
    )
    row = store.get_resource("page:blank")
    assert int(row["connection_generation"] or 0) == 3
    assert row["title"] == "Later"
    store.update_display(
        "page:blank", title="Stale", target="https://stale.test/",
    )
    row = store.get_resource("page:blank")
    assert row["title"] == "Later"


def test_surface_context_capture_carries_binding_epoch(tmp_path, monkeypatch):
    from openprogram.runtime.conversation import surface_context
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)

    def request_on_ws(_ws, command, timeout=5.0):
        del command, timeout
        return {
            "ok": True,
            "window_id": "win",
            "tab_id": NATIVE_TAB_ID,
            "target_id": "target-1",
            "url": "https://current.test/page",
            "title": "Current",
        }

    monkeypatch.setattr(webtab, "request_on_ws", request_on_ws)
    # An earlier connection came and went, so a writer still holding its
    # revision (1 in a fresh process) is older than this connection.
    next(webtab._next_revision)
    owner = object()
    context = surface_context.capture({
        "version": 1,
        "window_id": "win",
        "tab_id": NATIVE_TAB_ID,
        "region": "center",
        "access": "enabled",
    }, owner)
    surface = context["surfaces"][0]
    page_key = surface["page_key"]
    binding_id = surface["binding_id"]
    generation = _binding_generation(binding_id)
    assert generation > 1
    store = BrowserResourceStore()
    row = store.get_resource(page_key)
    assert row["title"] == "Current"
    assert row["target"] == "https://current.test/page"
    assert int(row["connection_generation"] or 0) == generation
    store.retain(
        page_key=page_key, title="Stale", target="https://stale.test/",
        connection_generation=0, live=True,
    )
    store.retain(
        page_key=page_key, title="Stale", target="https://stale.test/",
        connection_generation=1, live=True,
    )
    store.update_display(
        page_key, title="Stale", target="https://stale.test/",
    )
    row = store.get_resource(page_key)
    assert row["title"] == "Current"
    assert row["target"] == "https://current.test/page"
    assert int(row["connection_generation"] or 0) == generation
    webtab.release_binding(binding_id)


def test_tab_driver_results_update_the_page_title_and_url(tmp_path, monkeypatch):
    from openprogram.programs.applications.browser import tabs
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    _own_execution(monkeypatch)
    _owner, binding_id, page_key = _bind_owner("win", NATIVE_TAB_ID, "target-1")
    store = BrowserResourceStore()
    store.update_display(
        page_key, title="Example Domain", target="https://example.com",
        connection_generation=_binding_generation(binding_id),
    )
    driver = tabs.Driver()
    try:
        tab = tabs.Tab(
            id="tab-1", window_id="win", tab_id=NATIVE_TAB_ID,
            binding_id=binding_id, page_key=page_key, title="Example Domain",
        )
        driver._publish(tab, {"ok": True, "title": "Second page", "url": "https://example.com/second"})
        row = store.get_resource(page_key)
        assert row["title"] == "Second page"
        assert row["target"] == "https://example.com/second"

        # A result arriving after its binding is gone belongs to no live connection.
        webtab.release_binding(binding_id)
        driver._publish(tab, {"ok": True, "title": "Late", "url": "https://example.com/late"})
        row = store.get_resource(page_key)
        assert row["title"] == "Second page"
        assert row["target"] == "https://example.com/second"
    finally:
        driver._owner.shutdown()


def test_spawned_tab_driver_publishes_with_the_generation_it_was_listed_with(tmp_path, monkeypatch):
    """A program subprocess has no binding table; the listing told it the connection generation."""
    from openprogram.programs.applications.browser import tabs
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    _own_execution(monkeypatch)
    _owner, binding_id, page_key = _bind_owner("win", NATIVE_TAB_ID, "target-1")
    generation = _binding_generation(binding_id)
    store = BrowserResourceStore()
    store.update_display(page_key, title="Example Domain", target="https://example.com",
                         connection_generation=generation)
    driver = tabs.Driver()
    try:
        listed = driver._remember({
            "window_id": "win", "tab_id": NATIVE_TAB_ID, "target_id": "target-1",
            "binding_id": "binding-known-only-to-the-parent", "page_key": page_key,
            "connection_generation": generation, "title": "Example Domain",
        })
        assert listed.connection_generation == generation
        driver._publish(listed, {"ok": True, "title": "Second page", "url": "https://example.com/second"})
        row = store.get_resource(page_key)
        assert row["title"] == "Second page"
        assert row["target"] == "https://example.com/second"
    finally:
        driver._owner.shutdown()

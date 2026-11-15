from fastapi import FastAPI
from fastapi.testclient import TestClient


def _retain(store, page_key, tab_id, session_id="parent"):
    store.retain(
        page_key=page_key, window_id="win", tab_id=tab_id, title="Plans",
        target="https://example.test/", connection_generation=1,
        session_id=session_id, execution_id="exec_live",
        user_message_id="u1", assistant_message_id="a1",
        conversation_session_id=session_id, live=True,
    )


def _client(monkeypatch, tmp_path):
    from openprogram.interfaces.server.routes.execution import processes

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(processes, "_authorize", lambda *args, **kwargs: None)
    app = FastAPI()
    processes.register(app)
    return TestClient(app)


def test_close_by_resource_id_closes_a_page_whose_tab_is_gone(tmp_path, monkeypatch):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore
    from openprogram.interfaces.server.ws_actions import webtab

    client = _client(monkeypatch, tmp_path)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    page_key = webtab.page_key_for_revision(7)
    _retain(store, page_key, "tab-gone")
    emitted = []
    monkeypatch.setattr(
        "openprogram.programs.applications.browser.pages.emit_browser_resource",
        lambda row, *, page_key: emitted.append((page_key, row["status"])),
    )
    sentinel = object()
    webtab._bindings["bind-gone"] = (sentinel, "win", "tab-gone", "target", 1e18, 7, 0, 0, False)
    try:
        with client:
            response = client.post(f"/api/session/parent/resources/{page_key}/close")
        assert response.status_code == 200
        items = response.json()["items"]
        assert items and all(item["status"] == "closed" for item in items)
        assert store.get_resource(page_key)["lifecycle"] == "closed"
        assert emitted and all(status == "closed" for _key, status in emitted)
        assert "bind-gone" not in webtab._bindings
    finally:
        webtab._bindings.pop("bind-gone", None)


def test_close_by_resource_id_is_scoped_to_the_conversation(tmp_path, monkeypatch):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore

    client = _client(monkeypatch, tmp_path)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    _retain(store, "page:other", "tab-other", session_id="other")
    with client:
        foreign = client.post("/api/session/parent/resources/page:other/close")
        missing = client.post("/api/session/parent/resources/page:missing/close")
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert store.get_resource("page:other")["lifecycle"] != "closed"

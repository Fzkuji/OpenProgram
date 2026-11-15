"""The Versions routes read the store and start the conversation that undoes."""
import json

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from openprogram.interfaces.server.owner_auth import OwnerAuthMiddleware, OwnerAuthState
from openprogram.interfaces.server.routes.settings import versions
from openprogram.operations.self_update.versions import layout as paths
from openprogram.operations.self_update.versions import overview, pointer as pointers
from openprogram.operations.self_update.versions import store as sealed
from openprogram.runtime.permissions import authority

A, B = "20261101T100000Z-aaaaaaaaaaaa", "20261102T100000Z-bbbbbbbbbbbb"
BASE = "py3.12-0123456789abcdef"


@pytest.fixture
def api(tmp_path, monkeypatch):
    from openprogram.state import paths as state_paths
    monkeypatch.setattr(state_paths, "get_state_dir", lambda: tmp_path / "profile")
    authority._reset_owner_cache_for_tests()
    auth = OwnerAuthState.from_raw_token(bytes(range(32)), owner_principal_id=authority.owner_principal_id(),
                                        bind_host="127.0.0.1", port=18100, allowed_origins=())
    app = FastAPI()
    app.add_middleware(OwnerAuthMiddleware, auth_state=auth)
    versions.register(app)
    client = TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 12345))
    yield client, {"Authorization": f"Bearer {auth.token}"}
    client.close()
    authority._reset_owner_cache_for_tests()


@pytest.fixture
def managed(tmp_path, monkeypatch):
    layout = paths.Layout(tmp_path / "runtime")
    layout.root.mkdir()
    for identifier, parent in ((A, ""), (B, A)):
        directory = layout.version(identifier)
        directory.mkdir(parents=True)
        (directory / "version.json").write_text(json.dumps({
            "id": identifier, "commit": "c" * 40, "base": BASE, "shell_compat": 1, "built_by": "t",
            "built_at": "2026-11-08T10:42:00Z", "components": [], "parent": parent, "frontend_sha": "",
        }))
    pointers.create(layout, pointers.Pointer(generation=1, version=B, base=BASE, last_good=A, last_good_base=BASE,
                                             writer="controller"))
    monkeypatch.setattr(sealed, "committed_versions", lambda _layout: {A, B})
    monkeypatch.setattr(paths, "current_runtime", lambda: paths.CurrentRuntime(layout, B))
    started = []

    def start(title, text, *, tools):
        started.append((title, text, tools))
        return "versions_session"

    monkeypatch.setattr(versions, "_start_conversation", start)
    return layout, started


def test_owner_only(api):
    client, _headers = api
    assert client.get("/api/versions").status_code == 401


def test_an_install_without_versions_says_so_and_refuses_changes(api, monkeypatch):
    client, headers = api
    monkeypatch.setattr(paths, "current_runtime", lambda: None)
    assert client.get("/api/versions", headers=headers).json()["managed"] is False
    assert client.get("/api/versions/notices", headers=headers).json() == {"notices": []}
    response = client.post("/api/versions/undo", headers=headers, json={"version": A, "how": "go_back"})
    assert response.status_code == 409


def test_page_lists_the_store(api, managed):
    client, headers = api
    response = client.get("/api/versions", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["current"]["id"] == B


def test_go_back_targets_the_version_and_undo_the_commit(api, managed):
    client, headers = api
    _layout, started = managed
    response = client.post("/api/versions/undo", headers=headers, json={"version": A, "how": "go_back"})
    assert response.json() == {"session_id": "versions_session"}
    title, text, tools = started.pop()
    assert title == "Go back to Version 1" and f'target "{A}"' in text and tools == ["self_update"]
    client.post("/api/versions/undo", headers=headers, json={"version": B, "how": "undo_change"})
    _title, text, tools = started.pop()
    assert f'target "{"c" * 40}"' in text and tools == ["self_update"]


def test_a_version_no_longer_kept_and_a_bad_body_are_refused(api, managed):
    client, headers = api
    assert client.post("/api/versions/undo", headers=headers,
                       json={"version": "20200101T000000Z-ffffffffffff", "how": "go_back"}).status_code == 404
    assert client.post("/api/versions/undo", headers=headers, json={"version": A, "how": "other"}).status_code == 422


def test_apply_edits_points_the_agent_at_the_saved_patch(api, managed):
    client, headers = api
    layout, started = managed
    saved = layout.root / "unapplied" / "20261108T091200000000Z"
    saved.mkdir(parents=True)
    (saved / "manifest.json").write_text(json.dumps({"changed": ["a.py"]}))
    assert client.post("/api/versions/edits/apply", headers=headers, json={"stamp": "missing"}).status_code == 404
    response = client.post("/api/versions/edits/apply", headers=headers, json={"stamp": saved.name})
    assert response.status_code == 200, response.text
    _title, text, tools = started.pop()
    assert str(saved) in text and tools is None


def test_notices_are_acknowledged(api, managed):
    client, headers = api
    layout, _started = managed
    overview.acknowledge(layout, ["placeholder"])
    assert client.get("/api/versions/notices", headers=headers).json() == {"notices": []}
    assert client.post("/api/versions/notices/ack", headers=headers, json={"ids": ["direct_edits:x"]}).json() == {"ok": True}
    assert "direct_edits:x" in overview._seen(layout)

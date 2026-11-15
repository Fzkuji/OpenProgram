"""An Agent's avatar: validation, whole replacement, and tolerant loading."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.interfaces.server.routes.catalog import agents
from openprogram.runtime.agent_registry import manager


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    manager.create("main", name="Default Agent", make_default=True)
    app = FastAPI()
    agents.register(app)
    return TestClient(app)


def _patch(client: TestClient, avatar: object):
    revision = client.get("/api/agents/main").json()["agent"]["revision"]
    return client.patch("/api/agents/main", json={"expected_revision": revision, "avatar": avatar})


def test_a_new_agent_has_no_avatar_of_its_own(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/agents/main").json()["agent"]["avatar"] == {}


def test_a_character_avatar_saves_and_lists(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    saved = _patch(client, {"kind": "bot", "shape": "watering-can", "color": "teal"})
    assert saved.status_code == 200
    assert saved.json()["agent"]["avatar"] == {"kind": "bot", "shape": "watering-can", "color": "teal"}
    listed = client.get("/api/agents").json()["agents"]
    assert listed[0]["avatar"] == {"kind": "bot", "shape": "watering-can", "color": "teal"}


def test_one_avatar_replaces_another_whole(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    picture = "data:image/png;base64,AAAA"
    assert _patch(client, {"kind": "upload", "file": picture}).json()["agent"]["avatar"] == {
        "kind": "upload", "file": picture,
    }
    # The character keeps nothing of the picture it replaced.
    assert _patch(client, {"kind": "bot", "shape": "ghost", "color": "blue"}).json()["agent"]["avatar"] == {
        "kind": "bot", "shape": "ghost", "color": "blue",
    }
    # An empty avatar goes back to the picture derived from the id.
    assert _patch(client, {}).json()["agent"]["avatar"] == {}


def test_other_fields_leave_the_avatar_alone(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _patch(client, {"kind": "dicebear", "style": "openPeeps", "seed": "Atlas"})
    revision = client.get("/api/agents/main").json()["agent"]["revision"]
    renamed = client.patch("/api/agents/main", json={"expected_revision": revision, "name": "Renamed"})
    assert renamed.json()["agent"]["avatar"] == {"kind": "dicebear", "style": "openPeeps", "seed": "Atlas"}


@pytest.mark.parametrize("avatar", [
    "bot",
    {"kind": "robot"},
    {"shape": "ghost"},
    {"kind": "bot", "shape": "Not A Slug"},
    {"kind": "bot", "color": "#ff0000"},
    {"kind": "bot", "size": 40},
    {"kind": "upload", "file": "https://example.com/a.png"},
    {"kind": "upload", "file": "data:image/png;base64," + "A" * manager.AVATAR_FILE_MAX},
    {"kind": "letter", "letter": "ABC"},
    {"kind": "dicebear", "seed": "x" * 121},
])
def test_a_malformed_avatar_is_refused(tmp_path, monkeypatch, avatar) -> None:
    client = _client(tmp_path, monkeypatch)
    assert _patch(client, avatar).status_code == 400
    assert client.get("/api/agents/main").json()["agent"]["avatar"] == {}


def test_a_stored_avatar_that_no_longer_validates_does_not_hide_the_agent(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    path = manager.agent_dir("main") / "agent.json"
    raw = json.loads(path.read_text())
    raw["avatar"] = {"kind": "hologram", "shape": 3}
    path.write_text(json.dumps(raw))
    agent = client.get("/api/agents/main").json()["agent"]
    assert agent["id"] == "main"
    assert agent["avatar"] == {}


def test_a_copy_keeps_the_avatar(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _patch(client, {"kind": "bot", "shape": "rocket", "color": "green"})
    copy = manager.duplicate("main")
    assert copy.avatar == {"kind": "bot", "shape": "rocket", "color": "green"}

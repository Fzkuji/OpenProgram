"""list_projects wire contract — ``session_ids`` must be alive-filtered
and ``session_count`` must agree with it."""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from openprogram.store import project_store
from openprogram.webui.ws_actions import project as ws_project


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch):
    p1 = types.SimpleNamespace(
        id="p1", name="Alpha", path="/tmp/alpha", is_default=False,
        status="ready", session_ids=["s-live", "s-dead", "s-live2"],
    )
    default = types.SimpleNamespace(
        id="default", name="Default", path="/home/u", is_default=True,
        status="ready", session_ids=[],
    )
    monkeypatch.setattr(project_store, "get_default_project", lambda: default)
    monkeypatch.setattr(project_store, "prune_sessions", lambda alive: 0)
    monkeypatch.setattr(project_store, "list_projects", lambda: [default, p1])
    monkeypatch.setattr(
        ws_project, "_alive_session_ids", lambda: {"s-live", "s-live2"})
    return p1


def test_projects_list_session_ids_alive_filtered(fake_registry):
    ws = _FakeWS()
    asyncio.run(ws_project.handle_list_projects(ws, {}))
    frame = ws.sent[0]
    assert frame["type"] == "projects_list"
    by_id = {p["id"]: p for p in frame["data"]["projects"]}
    p1 = by_id["p1"]
    assert p1["session_ids"] == ["s-live", "s-live2"]  # s-dead dropped
    assert p1["session_count"] == 2                    # count == len(ids)
    assert by_id["default"]["session_ids"] == []
    assert by_id["default"]["is_default"] is True


def test_remove_project_action_is_unavailable_and_registry_unchanged(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.webui import server

    registry_path = tmp_path / "projects.json"
    before = {
        "proj_keep": {"id": "proj_keep", "name": "Keep"},
        "proj_remove": {"id": "proj_remove", "name": "Preserve"},
    }
    registry_path.write_text(json.dumps(before), encoding="utf-8")
    monkeypatch.setattr(project_store, "_registry_path", lambda: registry_path)

    ws = _FakeWS()
    asyncio.run(server._handle_ws_command(ws, {
        "action": "remove_project",
        "project_id": "proj_remove",
    }))

    assert ws.sent == []
    assert json.loads(registry_path.read_text(encoding="utf-8")) == before
    assert "remove_project" not in ws_project.ACTIONS
    assert "remove_project" not in server.WS_ACTIONS
    assert not hasattr(ws_project, "handle_remove_project")

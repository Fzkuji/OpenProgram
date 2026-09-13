"""Project location states on the public WS/execute boundary."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from openprogram.store.project import project_store as projects
from openprogram.store.session.session_store import SessionStore
from openprogram.webui.ws_actions import project as ws_project


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


def _store(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: str(state))
    store = SessionStore(state / "sessions")
    monkeypatch.setattr("openprogram.store.session.session_store.default_store", lambda: store)
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store)
    return store


def test_list_projects_reports_missing_and_counts(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    folder = tmp_path / "paper"
    folder.mkdir()
    store.create_session("s1", "main", project_path=str(folder))
    store.update_session("s1", archived=True)
    shutil.rmtree(folder)
    ws = FakeWS()
    asyncio.run(ws_project.handle_list_projects(ws, {"session_id": ""}))
    payload = ws.sent[0]["data"]
    row = next(p for p in payload["projects"] if not p["is_default"] and p["path"].endswith("paper"))
    assert row["path_missing"] is True
    assert row["location_state"] in {"missing", "replaced"}
    assert row["session_count"] == 1
    assert row["unarchived_session_count"] == 0


def test_relocate_rejects_another_projects_folder(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    pa = projects.resolve_project(a)
    pb = projects.resolve_project(b)
    ws = FakeWS()
    asyncio.run(ws_project.handle_relocate_project(ws, {
        "project_id": pa.id, "path": str(b), "replace_identity": True,
    }))
    data = next(f["data"] for f in ws.sent if f["type"] == "project_relocated")
    assert data["ok"] is False
    assert "another project" in (data["error"] or "")
    assert projects.get_project(pb.id).path.endswith("b")


def test_execute_refuses_bound_missing_project(tmp_path, monkeypatch):
    from openprogram.agent.internals._workdir import bound_project_execution_blocked

    store = _store(tmp_path, monkeypatch)
    folder = tmp_path / "paper"
    folder.mkdir()
    store.create_session("s1", "main", project_path=str(folder))
    shutil.rmtree(folder)
    assert bound_project_execution_blocked("s1") == "missing"

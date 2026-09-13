"""Project editing persists metadata without changing bindings or repository files."""
import asyncio
import json

from openprogram.store.project import project_store as store
from openprogram.webui.ws_actions import project as actions


class WS:
    def __init__(self):
        self.sent = []

    async def send_text(self, message):
        self.sent.append(json.loads(message))


def test_edit_project_round_trip_and_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_registry_path", lambda: tmp_path / "registry.json")
    main = tmp_path / "main"
    extra = tmp_path / "extra"
    main.mkdir()
    extra.mkdir()
    project = store.resolve_project(main)
    store.bind_session("existing", project.id)
    ws = WS()
    asyncio.run(actions.ACTIONS["update_project"](ws, {
        "project_id": project.id,
        "patch": {"name": "Renamed", "icon": "🔬", "description": "Research", "source_folders": [str(extra), str(extra)]},
    }))
    result = ws.sent[0]["data"]
    assert result["ok"] is True
    assert result["project"]["name"] == "Renamed"
    assert result["project"]["source_folders"] == [str(extra)]
    saved = store.get_project(project.id)
    assert saved.icon == "🔬" and saved.description == "Research"
    assert saved.path == str(main) and saved.session_ids == ["existing"]
    assert not (main / ".git").exists()
    before = (tmp_path / "registry.json").read_text()
    for patch in ({"name": " "}, {"source_folders": ["relative"]}, {"source_folders": [str(extra / "missing")]}, {"path": str(extra)}, {"icon": "x" * 33}):
        ws = WS()
        asyncio.run(actions.ACTIONS["update_project"](ws, {"project_id": project.id, "patch": patch}))
        assert ws.sent[0]["data"]["ok"] is False
        assert (tmp_path / "registry.json").read_text() == before
    ws = WS()
    asyncio.run(actions.ACTIONS["update_project"](ws, {"project_id": project.id, "patch": {"source_folders": []}}))
    assert ws.sent[0]["data"]["ok"] is True
    assert store.get_project(project.id).source_folders == []


def test_source_folders_are_defaults_not_overrides(tmp_path, monkeypatch):
    from openprogram.agent import session_db, session_config
    monkeypatch.setattr(store, "_registry_path", lambda: tmp_path / "registry.json")
    project = store.resolve_project(tmp_path)
    store.update_project(project.id, {"source_folders": [str(tmp_path.parent)]})
    store.bind_session("chat", project.id)
    row = {"id": "chat", "project_id": project.id}
    class DB:
        def get_session(self, _):
            return dict(row)
        def update_session(self, _, **fields):
            row.update(fields)
    monkeypatch.setattr(session_db, "default_db", lambda: DB())
    config = session_config.save_session_run_config("chat", agent_id="agent")
    assert config.additional_working_dirs == [str(tmp_path.parent)]
    config = session_config.save_session_run_config("chat", agent_id="agent", additional_working_dirs=[])
    assert config.additional_working_dirs == []
    assert session_config.save_session_run_config("chat", agent_id="agent").additional_working_dirs == []


def test_explicit_default_project_name_survives_legacy_backfill(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_registry_path", lambda: tmp_path / "registry.json")
    project = store.get_default_project()
    store.update_project(project.id, {"name": "Default"})
    assert store.get_default_project().name == "Default"

from pathlib import Path

import pytest

from openprogram.runtime.conversation.session_config import (
    SessionRunConfig,
    load_session_run_config,
    permission_from_config,
    reasoning_from_config,
    save_session_run_config,
    tools_override_from_config,
)
from openprogram.runtime.context.session_db import SessionDB


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    return db


def test_session_run_config_round_trip(tmp_db: SessionDB) -> None:
    # save_session_run_config is a no-op when the session row doesn't
    # exist (we don't ghost-create rows for a settings touch). Pre-create
    # so the persisted config sticks.
    tmp_db.create_session("c1", "main")
    cfg = save_session_run_config(
        "c1",
        agent_id="main",
        web_search=True,
        thinking_effort="off",
        permission_mode="bypass",
    )

    assert tools_override_from_config(cfg) == {"inherit": True, "web_search": True}
    assert reasoning_from_config(cfg) == "off"
    assert permission_from_config(cfg, default="auto") == "bypass"

    loaded = load_session_run_config("c1")
    assert loaded.web_search is True
    assert loaded.thinking_effort == "off"
    assert loaded.permission_mode == "bypass"


def test_skill_allowed_tools_round_trip(tmp_db: SessionDB) -> None:
    tmp_db.create_session("c1", "main")
    save_session_run_config("c1", agent_id="main", tools=["read", "grep"])
    assert tools_override_from_config(load_session_run_config("c1")) == ["read", "grep"]


def test_permission_from_config_defaults_to_unrestricted() -> None:
    empty = SessionRunConfig()
    assert permission_from_config(empty) == "bypass"
    assert permission_from_config(empty, default=None) == "bypass"
    assert permission_from_config(empty, default="bogus") == "bypass"
    assert permission_from_config(empty, default="auto") == "auto"
    assert permission_from_config(
        SessionRunConfig(permission_mode="acceptEdits"), default="ask",
    ) == "acceptEdits"


def test_thinking_aliases_normalize(tmp_db: SessionDB) -> None:
    tmp_db.create_session("c1", "main")
    cfg = save_session_run_config(
        "c1",
        agent_id="main",
        thinking_effort="none",
    )
    assert cfg.thinking_effort == "off"
    assert reasoning_from_config(cfg) == "off"

    cfg = save_session_run_config(
        "c1",
        agent_id="main",
        thinking_effort="max",
    )
    assert cfg.thinking_effort == "max"
    assert reasoning_from_config(cfg) == "max"


def test_additional_working_dirs_round_trip(tmp_db: SessionDB) -> None:
    # 额外工作目录 save/load 往返（additional-working-directories.md §3.6）。
    tmp_db.create_session("c1", "main")
    cfg = save_session_run_config(
        "c1",
        agent_id="main",
        additional_working_dirs=["/tmp/a", "/tmp/b"],
    )
    assert cfg.additional_working_dirs == ["/tmp/a", "/tmp/b"]
    assert load_session_run_config("c1").additional_working_dirs == ["/tmp/a", "/tmp/b"]

    # None = 不动（聊天路径不会误清既有配置）。
    cfg = save_session_run_config("c1", agent_id="main", thinking_effort="high")
    assert cfg.additional_working_dirs == ["/tmp/a", "/tmp/b"]

    # _as_str_list 清洗：空串被丢弃、非字符串转 str；[] 显式清空。
    cfg = save_session_run_config(
        "c1", agent_id="main", additional_working_dirs=["", "/tmp/c"],
    )
    assert cfg.additional_working_dirs == ["/tmp/c"]
    cfg = save_session_run_config("c1", agent_id="main", additional_working_dirs=[])
    assert cfg.additional_working_dirs == []


def test_sandbox_enabled_round_trip(tmp_db: SessionDB) -> None:
    tmp_db.create_session("c1", "main")
    assert load_session_run_config("c1").sandbox_enabled is None
    cfg = save_session_run_config("c1", agent_id="main", sandbox_enabled=False)
    assert cfg.sandbox_enabled is False
    assert load_session_run_config("c1").sandbox_enabled is False
    cfg = save_session_run_config("c1", agent_id="main", sandbox_enabled=True)
    assert cfg.sandbox_enabled is True


def _set_sandbox_payload(monkeypatch, tmp_db, cmd):
    import asyncio
    import json

    from openprogram.interfaces.server import server as srv
    from openprogram.interfaces.server.ws_actions.session import handle_set_sandbox

    monkeypatch.setattr(
        "openprogram.runtime.context.session_db.default_db", lambda: tmp_db)
    frames: list[dict] = []
    monkeypatch.setattr(srv, "_broadcast", lambda msg: frames.append(json.loads(msg)))

    class _WS:
        def __init__(self):
            self.sent: list[dict] = []

        async def send_text(self, text: str) -> None:
            self.sent.append(json.loads(text))

    ws = _WS()
    asyncio.run(handle_set_sandbox(ws, cmd))
    return ws.sent, frames


def test_set_sandbox_false_persists_on_existing_session(tmp_db, monkeypatch) -> None:
    tmp_db.create_session("s1", "main")
    sent, frames = _set_sandbox_payload(
        monkeypatch, tmp_db, {"session_id": "s1", "sandbox_enabled": False},
    )
    data = sent[0]["data"]
    # Off is what the configured default gives anyway: nothing is stored and
    # the conversation keeps following the default.
    assert data["sandbox"] is False
    assert (data["sandbox_enabled"], data["sandbox_source"]) == (None, "inherited")
    assert frames[0]["data"]["sandbox"] is False
    assert load_session_run_config("s1").sandbox_enabled is None
    # A choice that differs from the default is the conversation's own.
    sent, _frames = _set_sandbox_payload(
        monkeypatch, tmp_db, {"session_id": "s1", "sandbox_enabled": True},
    )
    assert (sent[0]["data"]["sandbox_enabled"], sent[0]["data"]["sandbox_source"]) == (True, "branch")
    assert load_session_run_config("s1").sandbox_enabled is True
    # Switching back off clears it again.
    _set_sandbox_payload(monkeypatch, tmp_db, {"session_id": "s1", "sandbox_enabled": False})
    assert load_session_run_config("s1").sandbox_enabled is None


def test_set_sandbox_false_echoes_before_session_exists(tmp_db, monkeypatch) -> None:
    sent, _frames = _set_sandbox_payload(
        monkeypatch, tmp_db, {"session_id": "local_draft", "sandbox_enabled": False},
    )
    assert tmp_db.get_session("local_draft") is None
    assert sent[0]["data"]["sandbox"] is False
    assert sent[0]["data"]["sandbox_enabled"] is False


def test_set_sandbox_false_echoes_without_session_id(tmp_db, monkeypatch) -> None:
    sent, _frames = _set_sandbox_payload(
        monkeypatch, tmp_db, {"sandbox_enabled": False},
    )
    assert sent[0]["data"]["sandbox"] is False
    assert sent[0]["data"]["sandbox_enabled"] is False


def test_project_source_defaults_persist_to_real_session(tmp_db, tmp_path, monkeypatch):
    from openprogram.state.store.project import project_store as projects
    monkeypatch.setattr(projects, "_registry_path", lambda: tmp_path / "projects.json")
    main = tmp_path / "project"
    extra = tmp_path / "sources"
    main.mkdir()
    extra.mkdir()
    project = projects.resolve_project(main)
    projects.update_project(project.id, {"source_folders": [str(extra)]})
    tmp_db.create_session("with-sources", "main", project_id=project.id)
    projects.bind_session("with-sources", project.id)
    cfg = save_session_run_config("with-sources", agent_id="main")
    assert cfg.additional_working_dirs == [str(extra)]
    assert load_session_run_config("with-sources").additional_working_dirs == [str(extra)]
    save_session_run_config("with-sources", agent_id="main", additional_working_dirs=[])
    assert save_session_run_config("with-sources", agent_id="main").additional_working_dirs == []

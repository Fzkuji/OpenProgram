from pathlib import Path

import pytest

from openprogram.agent.session_config import (
    SessionRunConfig,
    load_session_run_config,
    permission_from_config,
    reasoning_from_config,
    save_session_run_config,
    tools_override_from_config,
)
from openprogram.agent.session_db import SessionDB


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    return db


def test_session_run_config_round_trip(tmp_db: SessionDB) -> None:
    # save_session_run_config is a no-op when the session row doesn't
    # exist (we don't ghost-create rows for a settings touch). Pre-create
    # so the persisted config sticks.
    tmp_db.create_session("c1", "main")
    cfg = save_session_run_config(
        "c1",
        agent_id="main",
        tools=False,
        thinking_effort="off",
        permission_mode="bypass",
    )

    assert cfg.tools_enabled is False
    assert tools_override_from_config(cfg) == []
    assert reasoning_from_config(cfg) is None
    assert permission_from_config(cfg, default="auto") == "bypass"

    loaded = load_session_run_config("c1")
    assert loaded.tools_enabled is False
    assert loaded.thinking_effort == "off"
    assert loaded.permission_mode == "bypass"


def test_tools_enabled_yields_live_intent_not_snapshot(
    tmp_db: SessionDB,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # tools=True now stores INTENT and yields {enabled: True}, expanded live
    # against the registry each turn — NOT a frozen list(DEFAULT_TOOLS) snapshot.
    # This is the fix for "old sessions can't see newly-added tools".
    # See docs/design/runtime/tool-toggle-management.md.
    import openprogram.programs as tools_pkg

    monkeypatch.setattr(tools_pkg, "DEFAULT_TOOLS", ["read", "list"])
    tmp_db.create_session("c1", "main")

    cfg = save_session_run_config(
        "c1",
        agent_id="main",
        tools=True,
        thinking_effort="high",
        permission_mode="acceptEdits",
    )

    assert tools_override_from_config(cfg) == {"inherit": True}
    assert reasoning_from_config(cfg) == "high"
    assert permission_from_config(cfg, default="bypass") == "acceptEdits"


def test_permission_from_config_fails_safe_to_ask() -> None:
    empty = SessionRunConfig()
    assert permission_from_config(empty) == "ask"
    assert permission_from_config(empty, default=None) == "ask"
    assert permission_from_config(empty, default="bogus") == "ask"
    assert permission_from_config(empty, default="bypass") == "bypass"
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
    assert reasoning_from_config(cfg) is None

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

    from openprogram.webui import server as srv
    from openprogram.webui.ws_actions.session import handle_set_sandbox

    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db", lambda: tmp_db)
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
    assert data["sandbox"] is False
    assert data["sandbox_enabled"] is False
    assert frames[0]["data"]["sandbox"] is False
    assert load_session_run_config("s1").sandbox_enabled is False


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

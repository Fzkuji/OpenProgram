from pathlib import Path

import pytest

from openprogram.agent.session_config import (
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


def test_tools_enabled_uses_default_tool_names(
    tmp_db: SessionDB,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openprogram.tools as tools_pkg

    monkeypatch.setattr(tools_pkg, "DEFAULT_TOOLS", ["read", "list"])

    cfg = save_session_run_config(
        "c1",
        agent_id="main",
        tools=True,
        thinking_effort="high",
        permission_mode="auto",
    )

    assert tools_override_from_config(cfg) == ["read", "list"]
    assert reasoning_from_config(cfg) == "high"
    assert permission_from_config(cfg, default="bypass") == "auto"


def test_thinking_aliases_normalize(tmp_db: SessionDB) -> None:
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
    assert cfg.thinking_effort == "xhigh"
    assert reasoning_from_config(cfg) == "xhigh"

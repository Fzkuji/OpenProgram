from pathlib import Path

import pytest

from openprogram.agent.session_db import SessionDB
from openprogram.webui import persistence as P


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr(
        "openprogram.agents.manager.sessions_dir",
        lambda agent_id: tmp_path / "agents" / agent_id / "sessions",
    )
    return db


def test_save_meta_uses_explicit_session_identity(tmp_db: SessionDB) -> None:
    P.save_meta(
        "main",
        "conv-1",
        {
            "id": "wrong-id",
            "agent_id": "wrong-agent",
            "title": "Initial",
        },
    )

    row = tmp_db.get_session("conv-1")
    assert row is not None
    assert row["id"] == "conv-1"
    assert row["agent_id"] == "main"
    assert row["title"] == "Initial"

    P.save_meta(
        "main",
        "conv-1",
        {
            "id": "wrong-again",
            "agent_id": "wrong-agent",
            "title": "Updated",
        },
    )

    row = tmp_db.get_session("conv-1")
    assert row is not None
    assert row["id"] == "conv-1"
    assert row["agent_id"] == "main"
    assert row["title"] == "Updated"

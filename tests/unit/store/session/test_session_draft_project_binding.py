"""A draft's pre-send settings must not cost it the project it picked.

Choosing a model before the first message stores it with
``update_session``, which creates the session repo ahead of
``create_session``. The first message then names the picked project; the
conversation still has to land in that project, keeping the model pick.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.runtime.context.session_db import SessionDB


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: str(state))
    return db


def _project(tmp_path: Path, name: str):
    from openprogram.state.store.project import project_store as P
    (tmp_path / name).mkdir(exist_ok=True)
    return P.resolve_project(tmp_path / name, name=name)


def test_settings_stored_before_the_first_message_do_not_create_the_session(env):
    db = env
    db.update_session("local_draft", provider_override="deepseek", model_override="flash")

    assert db.is_created("local_draft") is False

    db.create_session("local_draft", "main")
    assert db.is_created("local_draft") is True


def test_a_draft_with_stored_settings_lands_in_the_picked_project(env, tmp_path: Path):
    db = env
    from openprogram.state.store.project import project_store as P
    proj = _project(tmp_path, "paper")
    db.update_session("local_draft", provider_override="deepseek", model_override="flash")

    db.create_session("local_draft", "main", project_id=proj.id)

    assert P.project_for_session("local_draft").id == proj.id
    assert db._session_dir("local_draft").parent.parent.name == proj.id
    meta = db.read_disk_meta("local_draft")
    assert meta["id"] == "local_draft"
    assert meta["provider_override"] == "deepseek"
    assert meta["model_override"] == "flash"


def test_a_conversation_with_history_counts_as_created_and_never_moves(env, tmp_path: Path):
    db = env
    db.update_session("s1", title="kept")
    db.append_message("s1", {
        "id": "u1", "role": "user", "content": "hi",
        "timestamp": 0, "predecessor": None,
    })
    before = db._session_dir("s1")

    assert db.is_created("s1") is True
    db._adopt_unclaimed_repo("s1", tmp_path / "elsewhere" / "s1")

    assert db._session_dir("s1") == before
    assert db.read_disk_meta("s1")["title"] == "kept"


def test_a_missing_session_is_not_created(env):
    assert env.is_created("nope") is False

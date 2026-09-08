"""Continuation bind skips project auto-init; a normal turn still snapshots."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from openprogram.agent.dispatcher.turn_context import TurnBindings
from openprogram.agent.dispatcher.types import TurnRequest
from openprogram.agent.session_db import SessionDB
from openprogram.store.project.project_store import ProjectGit


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr(
        "openprogram.store.session.session_store.default_store", lambda: db,
    )
    monkeypatch.setattr("openprogram.store.default_store", lambda: db)
    return db


def _bind_nonrepo_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from openprogram.store.project import project_commit

    proj_dir = tmp_path / "user-project"
    proj_dir.mkdir()
    (proj_dir / "notes.txt").write_text("user file\n", encoding="utf-8")
    monkeypatch.setattr(project_commit, "is_enabled", lambda: True)
    monkeypatch.setattr(
        project_commit, "_project_for",
        lambda _sid: SimpleNamespace(id="proj-1", path=str(proj_dir), is_default=False),
    )
    monkeypatch.setattr(project_commit, "_has_active_worktree", lambda _sid: False)
    return proj_dir


def test_continuation_bind_does_not_auto_init_nonrepo_project(
    tmp_db: SessionDB, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    proj_dir = _bind_nonrepo_project(tmp_path, monkeypatch)
    inits: list[str] = []
    monkeypatch.setattr(
        ProjectGit, "auto_init_for_agent",
        lambda self: inits.append(str(self.path)) or "ready",
    )
    tmp_db.create_session("s-resume", "main", source="test")
    binding = TurnBindings.bind(
        req=TurnRequest(session_id="s-resume", user_text="hi", agent_id="main", source="test"),
        assistant_msg_id="a-resume",
        db=tmp_db,
        snapshot_project_baseline=False,
    )
    try:
        assert binding.project_baseline is None
        assert inits == []
        assert not (proj_dir / ".git").exists()
    finally:
        binding.release()


def test_normal_bind_still_snapshots_nonrepo_project(
    tmp_db: SessionDB, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    proj_dir = _bind_nonrepo_project(tmp_path, monkeypatch)
    inits: list[str] = []
    monkeypatch.setattr(
        ProjectGit, "auto_init_for_agent",
        lambda self: inits.append(str(self.path)) or "ready",
    )
    tmp_db.create_session("s-new", "main", source="test")
    binding = TurnBindings.bind(
        req=TurnRequest(session_id="s-new", user_text="hi", agent_id="main", source="test"),
        assistant_msg_id="a-new",
        db=tmp_db,
    )
    try:
        assert inits == [str(proj_dir)]
    finally:
        binding.release()

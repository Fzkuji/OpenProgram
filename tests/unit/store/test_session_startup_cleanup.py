"""Startup cleanup never loses session data.

Design: docs/reference/design/runtime/session/operations.html "Process
startup". A directory without ``history/`` is kept when it holds session
data and otherwise moved to ``.quarantine``; anything uncertain is kept.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from openprogram.host._platform.filesystem import remove_tree
from openprogram.state.store.session.session_store import SessionStore


def _store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions")


def _old_session(store: SessionStore, sid: str) -> Path:
    store.create_session(sid, "main", created_at=0.0, updated_at=0.0)
    store._flush_index()
    return store._session_dir(sid)


def _reopen(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions")


def _strip_to_shell(sdir: Path) -> None:
    (sdir / "main.jsonl").unlink()


@pytest.fixture
def no_unbind(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    from openprogram.state.store.project import project_store
    monkeypatch.setattr(project_store, "unbind_session",
                        lambda sid, *a, **k: calls.append(sid))
    return calls


def test_new_session_has_history_before_any_message(tmp_path: Path):
    store = _store(tmp_path)
    sdir = _old_session(store, "s1")
    assert (sdir / "main.jsonl").is_file()
    assert "s1" in _reopen(tmp_path)._index


def test_session_missing_history_keeps_its_files(tmp_path: Path):
    store = _store(tmp_path)
    sdir = _old_session(store, "s1")
    (sdir / "workdir" / "notes.txt").write_text("user file")
    (sdir / "main.jsonl").unlink()
    (sdir / "events.jsonl").write_text("")

    reopened = _reopen(tmp_path)

    assert "s1" in reopened._index
    assert (sdir / "events.jsonl").is_file()
    assert (sdir / "workdir" / "notes.txt").read_text() == "user file"


def test_shell_moves_to_quarantine_with_its_files(tmp_path: Path, no_unbind):
    store = _store(tmp_path)
    sdir = _old_session(store, "s1")
    (sdir / "workdir" / "notes.txt").write_text("user file")
    _strip_to_shell(sdir)

    reopened = _reopen(tmp_path)

    assert "s1" not in reopened._index
    assert not sdir.exists()
    [moved] = list((tmp_path / "sessions" / ".quarantine").iterdir())
    assert (moved / "session" / "workdir" / "notes.txt").read_text() == "user file"
    reason = json.loads((moved / "reason.json").read_text())
    assert reason["session_id"] == "s1"
    assert reason["source"] == str(sdir)
    assert no_unbind == []


def test_recent_shell_is_left_alone(tmp_path: Path):
    store = _store(tmp_path)
    store.create_session("s1", "main")
    store._flush_index()
    sdir = store._session_dir("s1")
    _strip_to_shell(sdir)

    assert "s1" in _reopen(tmp_path)._index
    assert sdir.is_dir()


def test_unknown_age_is_left_alone(tmp_path: Path):
    store = _store(tmp_path)
    sdir = _old_session(store, "s1")
    _strip_to_shell(sdir)
    index = tmp_path / "sessions" / ".chats-index.json"
    rows = json.loads(index.read_text())
    rows["s1"].pop("created_at")
    index.write_text(json.dumps(rows))

    assert "s1" in _reopen(tmp_path)._index
    assert sdir.is_dir()


def test_missing_directory_drops_only_the_summary_row(tmp_path: Path, no_unbind):
    store = _store(tmp_path)
    sdir = _old_session(store, "s1")
    remove_tree(sdir)

    reopened = _reopen(tmp_path)

    assert "s1" not in reopened._index
    assert no_unbind == []


@pytest.mark.parametrize("journal", [
    "{not json",
    json.dumps({"sessions": {"s1": {"stage": "cleanup"}}}),
    json.dumps({"sessions": {"s1": {"stage": "location-repair"}}}),
])
def test_unsettled_or_unreadable_migration_keeps_the_shell(tmp_path: Path, journal: str):
    store = _store(tmp_path)
    sdir = _old_session(store, "s1")
    _strip_to_shell(sdir)
    path = tmp_path / "sessions" / ".migration" / "journal.json"
    path.parent.mkdir(parents=True)
    path.write_text(journal)

    assert "s1" in _reopen(tmp_path)._index
    assert sdir.is_dir()


def test_quarantine_is_not_a_session(tmp_path: Path):
    store = _store(tmp_path)
    sdir = _old_session(store, "s1")
    _strip_to_shell(sdir)
    _reopen(tmp_path)
    (tmp_path / "sessions" / ".chats-index.json").unlink()

    rebuilt = _reopen(tmp_path)

    assert "s1" not in rebuilt._index

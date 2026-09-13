"""Textless turns retain the same Git completion contract as text turns."""
from types import SimpleNamespace

import pytest

from openprogram.agent.dispatcher import finalize
from openprogram.agent.dispatcher.types import TurnRequest


@pytest.fixture
def commit_db(monkeypatch):
    commits = []
    db = SimpleNamespace(
        update_session=lambda *_a, **_kw: None,
        commit_turn=lambda *args: commits.append(args),
    )
    monkeypatch.setattr("openprogram.store.default_store", lambda: db)
    monkeypatch.setattr("openprogram.context.commit.store.load_commit_for_head", lambda *_: None)
    monkeypatch.setattr("openprogram.store.project.project_commit.commit_turn_changes", lambda *_a, **_kw: None)
    for name in ("_maybe_auto_title", "maybe_auto_name_branch", "persist_turn_file_summary",
                 "commit_turn_to_shadow_git", "_evict_old_snapshots"):
        monkeypatch.setattr(finalize, name, lambda *_a, **_kw: None)
    return db, commits


def finish(db, text, failed=False):
    kwargs = dict(
        db=db, req=TurnRequest(session_id="textless", agent_id="main", user_text=text, source="test"),
        session={}, assistant_msg_id="reply", _project_baseline=None, on_event=lambda _: None,
    )
    if failed:
        return finalize.finalize_error_turn(**kwargs)
    return finalize.finalize_turn(
        **kwargs, usage={}, assistant_msg={"content": "reply"}, agent_profile=None, ctx_win=None,
    )


@pytest.mark.parametrize("text,summary", [
    ("", "turn"), (" \n\t", "turn"), ("  first line\nsecond line", "first line"),
    ("x" * 80, "x" * 60),
])
@pytest.mark.parametrize("failed", [False, True])
def test_turn_commit_uses_safe_summary(commit_db, text, summary, failed):
    db, commits = commit_db
    acknowledged = finish(db, text, failed)
    assert commits == [("textless", f"turn{' (error)' if failed else ''}: {summary}")]
    if not failed:
        assert acknowledged is True


def test_commit_failure_is_not_acknowledged(commit_db):
    db, commits = commit_db

    def fail(*_args):
        raise OSError("repository unavailable")
    db.commit_turn = fail
    assert finish(db, "text") is False
    assert commits == []

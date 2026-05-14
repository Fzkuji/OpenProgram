"""DagSession + DagSessionManager: multi-session lifecycle over SQLite."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from openprogram.context.session import (
    DagSession,
    DagSessionManager,
    SessionMeta,
)
from openprogram.context.chat import chat_turn


class MockProvider:
    def __init__(self, replies):
        self.replies = list(replies)

    def __call__(self, *, messages, model, system, tools, **kw):
        if not self.replies:
            return "(no more replies)"
        return self.replies.pop(0)


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "chat.sqlite"


# ── Create ──────────────────────────────────────────────────────────


def test_create_session_writes_db_row(db_path):
    mgr = DagSessionManager(db_path)
    sess = mgr.create(
        provider_call=MockProvider(["x"]),
        model="claude-opus",
        title="my chat",
    )
    assert mgr.exists(sess.id)
    listed = mgr.list()
    assert len(listed) == 1
    assert listed[0].title == "my chat"
    assert listed[0].model == "claude-opus"


def test_create_session_explicit_id(db_path):
    mgr = DagSessionManager(db_path)
    sess = mgr.create(provider_call=MockProvider([]), session_id="my-fixed-id")
    assert sess.id == "my-fixed-id"


def test_create_session_refuses_duplicate(db_path):
    mgr = DagSessionManager(db_path)
    mgr.create(provider_call=MockProvider([]), session_id="dup")
    with pytest.raises(ValueError, match="already exists"):
        mgr.create(provider_call=MockProvider([]), session_id="dup")


# ── List ────────────────────────────────────────────────────────────


def test_list_empty(db_path):
    mgr = DagSessionManager(db_path)
    assert mgr.list() == []


def test_list_orders_by_updated_at_desc(db_path):
    mgr = DagSessionManager(db_path)
    mgr.create(provider_call=MockProvider([]), session_id="a", title="A")
    time.sleep(0.01)
    mgr.create(provider_call=MockProvider([]), session_id="b", title="B")
    time.sleep(0.01)
    mgr.create(provider_call=MockProvider([]), session_id="c", title="C")

    ids = [m.id for m in mgr.list()]
    assert ids == ["c", "b", "a"]


# ── Load ────────────────────────────────────────────────────────────


def test_load_restores_session_state(db_path):
    mgr = DagSessionManager(db_path)
    sess = mgr.create(
        provider_call=MockProvider(["reply 1"]),
        session_id="restore-me",
        title="original",
        model="claude-opus",
    )
    chat_turn("hi", runtime=sess.runtime)

    # Reopen in a fresh manager
    mgr2 = DagSessionManager(db_path)
    sess2 = mgr2.load("restore-me", provider_call=MockProvider(["reply 2"]))

    assert sess2.meta.title == "original"
    assert sess2.meta.model == "claude-opus"
    assert len(sess2.graph) == 2  # user + model

    chat_turn("more", runtime=sess2.runtime)
    assert len(sess2.graph) == 4


def test_load_nonexistent_raises(db_path):
    mgr = DagSessionManager(db_path)
    with pytest.raises(FileNotFoundError):
        mgr.load("does-not-exist", provider_call=MockProvider([]))


# ── Touch ────────────────────────────────────────────────────────────


def test_touch_updates_meta_and_persists(db_path):
    mgr = DagSessionManager(db_path)
    sess = mgr.create(provider_call=MockProvider([]), session_id="touch-me", title="old")
    original_updated = sess.meta.updated_at
    time.sleep(0.01)
    sess.touch(title="new title")
    assert sess.meta.title == "new title"
    assert sess.meta.updated_at > original_updated

    sess2 = mgr.load("touch-me", provider_call=MockProvider([]))
    assert sess2.meta.title == "new title"


# ── Rename ──────────────────────────────────────────────────────────


def test_rename_changes_title_not_id(db_path):
    mgr = DagSessionManager(db_path)
    mgr.create(provider_call=MockProvider([]), session_id="keep-id", title="before")
    mgr.rename("keep-id", "after")
    listed = mgr.list()
    assert listed[0].id == "keep-id"
    assert listed[0].title == "after"


# ── Delete ──────────────────────────────────────────────────────────


def test_delete_removes_session(db_path):
    mgr = DagSessionManager(db_path)
    mgr.create(provider_call=MockProvider([]), session_id="goner")
    assert mgr.exists("goner")
    assert mgr.delete("goner") is True
    assert not mgr.exists("goner")
    assert mgr.list() == []


def test_delete_missing_returns_false(db_path):
    mgr = DagSessionManager(db_path)
    assert mgr.delete("nope") is False


# ── End-to-end with chat_turn ───────────────────────────────────────


def test_end_to_end_chat_persists_across_load(db_path):
    mgr = DagSessionManager(db_path)
    sess = mgr.create(
        provider_call=MockProvider(["first reply"]),
        session_id="e2e",
        model="claude-opus",
    )
    chat_turn("first question", runtime=sess.runtime)

    mgr2 = DagSessionManager(db_path)
    sess2 = mgr2.load("e2e", provider_call=MockProvider(["second reply"]))

    types = [n.role for n in sess2.graph]
    assert types == ["user", "llm"]

    chat_turn("second question", runtime=sess2.runtime)
    types_after = [n.role for n in sess2.graph]
    assert types_after == ["user", "llm", "user", "llm"]


# ── Search ──────────────────────────────────────────────────────────


def test_session_search(db_path):
    mgr = DagSessionManager(db_path)
    sess = mgr.create(
        provider_call=MockProvider(["irrelevant model output"]),
        model="claude-opus",
    )
    chat_turn("the quick brown fox jumps", runtime=sess.runtime)
    chat_turn("hello world", runtime=sess.runtime)

    hits = sess.search("fox")
    assert len(hits) == 1
    # Should be the user message about fox
    matched_node = sess.graph.nodes[hits[0]]
    assert "fox" in matched_node.content


# ── Multiple sessions in one DB don't leak ─────────────────────────


def test_sessions_isolated_in_one_db(db_path):
    mgr = DagSessionManager(db_path)
    s1 = mgr.create(provider_call=MockProvider(["r1"]), session_id="alpha", model="claude")
    s2 = mgr.create(provider_call=MockProvider(["r2"]), session_id="beta", model="claude")

    chat_turn("alpha thing", runtime=s1.runtime)
    chat_turn("beta thing", runtime=s2.runtime)

    # Reload both independently
    mgr2 = DagSessionManager(db_path)
    s1_again = mgr2.load("alpha", provider_call=MockProvider([]))
    s2_again = mgr2.load("beta", provider_call=MockProvider([]))

    s1_user = next(n for n in s1_again.graph if hasattr(n, "content"))
    s2_user = next(n for n in s2_again.graph if hasattr(n, "content"))
    assert s1_user.content == "alpha thing"
    assert s2_user.content == "beta thing"

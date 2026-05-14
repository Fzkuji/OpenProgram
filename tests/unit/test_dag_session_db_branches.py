"""DagSessionDB: named branches + token stats + delete_branch_tail."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from openprogram.context.session_db import DagSessionDB


@pytest.fixture
def db(tmp_path: Path) -> DagSessionDB:
    return DagSessionDB(tmp_path / "x.sqlite")


def _append(db, sess, mid, *, role="user", parent=None, content="x",
            input_tokens=None, output_tokens=None, model=None,
            cache_read=None):
    msg = {
        "id": mid, "role": role, "content": content,
        "parent_id": parent, "timestamp": time.time(),
    }
    if input_tokens is not None:
        msg["input_tokens"] = input_tokens
    if output_tokens is not None:
        msg["output_tokens"] = output_tokens
    if model is not None:
        msg["token_model"] = model
    if cache_read is not None:
        msg["cache_read_tokens"] = cache_read
    db.append_message(sess, msg)


# ── Branch enumeration ───────────────────────────────────────────


def test_list_branches_finds_every_tip(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1", role="user")
    _append(db, "s1", "n2", role="assistant", parent="n1")
    # fork: n3 also branches off n1
    _append(db, "s1", "n3", role="user", parent="n1")
    tips = {b["head_msg_id"] for b in db.list_branches("s1")}
    assert tips == {"n2", "n3"}


def test_list_branches_includes_named_label(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    _append(db, "s1", "n2", parent="n1")
    db.set_branch_name("s1", "n2", "experiment-a")
    rows = db.list_branches("s1")
    assert rows[0]["head_msg_id"] == "n2"
    assert rows[0]["name"] == "experiment-a"


def test_set_branch_name_is_upsert(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    db.set_branch_name("s1", "n1", "first")
    db.set_branch_name("s1", "n1", "second")  # rename
    rows = db.list_branches("s1")
    assert rows[0]["name"] == "second"


def test_delete_branch_name(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    db.set_branch_name("s1", "n1", "label")
    db.delete_branch_name("s1", "n1")
    assert db.list_branches("s1")[0]["name"] is None


# ── delete_branch_tail ───────────────────────────────────────────


def test_delete_branch_tail_removes_subtree(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    _append(db, "s1", "n2", parent="n1")
    _append(db, "s1", "n3", parent="n2")
    _append(db, "s1", "n4", parent="n2")  # sibling to n3
    deleted = db.delete_branch_tail("s1", "n2")
    assert deleted == 3  # n2 + n3 + n4
    remaining = {m["id"] for m in db.get_messages("s1")}
    assert remaining == {"n1"}


def test_delete_branch_tail_missing_returns_zero(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    assert db.delete_branch_tail("s1", "ghost") == 0


# ── Token stats ──────────────────────────────────────────────────


def test_token_stats_sums_along_chain(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "u1", role="user")
    _append(db, "s1", "a1", role="assistant", parent="u1",
            input_tokens=100, output_tokens=20, model="claude-opus",
            cache_read=10)
    _append(db, "s1", "u2", role="user", parent="a1")
    _append(db, "s1", "a2", role="assistant", parent="u2",
            input_tokens=200, output_tokens=30, model="claude-opus",
            cache_read=50)
    stats = db.get_branch_token_stats("s1", head_id="a2")
    assert stats["input_tokens"] == 300
    assert stats["output_tokens"] == 50
    assert stats["cache_read_total"] == 60
    assert stats["messages_counted"] == 2
    # "current" = most recent input
    assert stats["current_tokens"] >= 200
    assert stats["model"] == "claude-opus"


def test_token_stats_filters_by_model(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "u1", role="user")
    _append(db, "s1", "a1", role="assistant", parent="u1",
            input_tokens=100, model="opus")
    _append(db, "s1", "u2", role="user", parent="a1")
    _append(db, "s1", "a2", role="assistant", parent="u2",
            input_tokens=200, model="sonnet")
    stats = db.get_branch_token_stats("s1", head_id="a2", model="sonnet")
    assert stats["input_tokens"] == 200
    assert stats["messages_counted"] == 1


# ── message_exists ───────────────────────────────────────────────


def test_message_exists(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    assert db.message_exists("s1", "n1") is True
    assert db.message_exists("s1", "ghost") is False
    assert db.message_exists("nope", "n1") is False

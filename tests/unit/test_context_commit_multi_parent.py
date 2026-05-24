"""ContextCommit multi-parent round-trip (task D)."""
from __future__ import annotations

from openprogram.context.commit.types import ContextCommit


def _make(**kw):
    base = dict(
        id="commit_abc",
        session_id="s1",
        parent_id=None,
        created_at=1.0,
        head_node_id="n1",
        rules_version="v1",
        total_tokens=0,
    )
    base.update(kw)
    return ContextCommit(**base)


def test_single_parent_back_compat():
    c = _make(parent_id="commit_p1")
    assert c.parent_id == "commit_p1"
    assert c.parent_ids == ["commit_p1"]
    d = c.to_dict()
    assert d["parent_id"] == "commit_p1"
    assert d["parent_ids"] == ["commit_p1"]


def test_multi_parent_merge():
    c = _make(parent_ids=["a", "b", "c"])
    assert c.parent_ids == ["a", "b", "c"]
    assert c.parent_id == "a"
    d = c.to_dict()
    assert d["parent_ids"] == ["a", "b", "c"]
    assert d["parent_id"] == "a"


def test_none_parent_first_commit():
    c = _make(parent_id=None)
    assert c.parent_ids == []
    assert c.parent_id is None


def test_legacy_payload_loads():
    from openprogram.context.commit.store import _payload_to_commit
    payload = {
        "id": "commit_old",
        "session_id": "s1",
        "parent_id": "commit_prev",
        "created_at": 1.0,
        "head_node_id": "n1",
        "rules_version": "v1",
        "total_tokens": 0,
        "items": [],
    }
    c = _payload_to_commit(payload)
    assert c.parent_id == "commit_prev"
    assert c.parent_ids == ["commit_prev"]


def test_new_payload_loads():
    from openprogram.context.commit.store import _payload_to_commit
    payload = {
        "id": "commit_new",
        "session_id": "s1",
        "parent_id": "a",
        "parent_ids": ["a", "b"],
        "created_at": 1.0,
        "head_node_id": "n1",
        "rules_version": "v1",
        "total_tokens": 0,
        "items": [],
    }
    c = _payload_to_commit(payload)
    assert c.parent_ids == ["a", "b"]
    assert c.parent_id == "a"

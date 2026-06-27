"""ContextCommit multi-parent round-trip (task D)."""
from __future__ import annotations

from openprogram.context.commit.types import ContextCommit


def _make(**kw):
    base = dict(
        id="commit_abc",
        session_id="s1",
        called_by=None,
        created_at=1.0,
        head_node_id="n1",
        rules_version="v1",
        total_tokens=0,
    )
    base.update(kw)
    return ContextCommit(**base)


def test_single_parent_back_compat():
    c = _make(called_by="commit_p1")
    assert c.called_by == "commit_p1"
    assert c.commit_parents == ["commit_p1"]
    d = c.to_dict()
    assert d["called_by"] == "commit_p1"
    assert d["commit_parents"] == ["commit_p1"]


def test_multi_parent_merge():
    c = _make(commit_parents=["a", "b", "c"])
    assert c.commit_parents == ["a", "b", "c"]
    assert c.called_by == "a"
    d = c.to_dict()
    assert d["commit_parents"] == ["a", "b", "c"]
    assert d["called_by"] == "a"


def test_none_parent_first_commit():
    c = _make(called_by=None)
    assert c.commit_parents == []
    assert c.called_by is None


def test_legacy_payload_loads():
    from openprogram.context.commit.store import _payload_to_commit
    payload = {
        "id": "commit_old",
        "session_id": "s1",
        "called_by": "commit_prev",
        "created_at": 1.0,
        "head_node_id": "n1",
        "rules_version": "v1",
        "total_tokens": 0,
        "items": [],
    }
    c = _payload_to_commit(payload)
    assert c.called_by == "commit_prev"
    assert c.commit_parents == ["commit_prev"]


def test_new_payload_loads():
    from openprogram.context.commit.store import _payload_to_commit
    payload = {
        "id": "commit_new",
        "session_id": "s1",
        "called_by": "a",
        "commit_parents": ["a", "b"],
        "created_at": 1.0,
        "head_node_id": "n1",
        "rules_version": "v1",
        "total_tokens": 0,
        "items": [],
    }
    c = _payload_to_commit(payload)
    assert c.commit_parents == ["a", "b"]
    assert c.called_by == "a"

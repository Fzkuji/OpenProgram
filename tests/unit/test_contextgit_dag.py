"""Tests for ``openprogram.contextgit.dag``.

Pure-function tests — no DB, no server. These lock down the semantics
that retry / edit / checkout rely on.
"""
from __future__ import annotations

from openprogram.contextgit import (
    advance_head,
    children,
    head_or_tip,
    is_ancestor,
    linear_history,
    normalize_parent_pointers,
    sibling_index,
    siblings,
)


def _msg(id_: str, parent: str | None, *, ts: int = 0) -> dict:
    return {"id": id_, "parent_id": parent, "created_at": ts}


# ---- siblings / sibling_index -------------------------------------------

def test_siblings_includes_self_and_same_parent():
    msgs = [
        _msg("u1", None, ts=1),
        _msg("a1", "u1", ts=2),
        _msg("a2", "u1", ts=3),   # retry of assistant reply
        _msg("a3", "u1", ts=4),   # another retry
    ]
    sibs = siblings(msgs, "a2")
    assert [s["id"] for s in sibs] == ["a1", "a2", "a3"]


def test_siblings_sorted_by_created_at():
    msgs = [
        _msg("u1", None),
        _msg("a2", "u1", ts=20),
        _msg("a1", "u1", ts=10),
        _msg("a3", "u1", ts=30),
    ]
    assert [s["id"] for s in siblings(msgs, "a2")] == ["a1", "a2", "a3"]


def test_sibling_index_is_1_based():
    msgs = [
        _msg("u1", None),
        _msg("a1", "u1", ts=1),
        _msg("a2", "u1", ts=2),
        _msg("a3", "u1", ts=3),
    ]
    assert sibling_index(msgs, "a1") == (1, 3)
    assert sibling_index(msgs, "a2") == (2, 3)
    assert sibling_index(msgs, "a3") == (3, 3)


def test_sibling_index_unknown_message():
    assert sibling_index([_msg("u1", None)], "bogus") == (0, 0)


def test_root_messages_are_all_siblings():
    # Messages with parent_id = None share the "root" bucket.
    msgs = [_msg("u1", None, ts=1), _msg("u2", None, ts=2)]
    assert [s["id"] for s in siblings(msgs, "u1")] == ["u1", "u2"]


# ---- children ------------------------------------------------------------

def test_children_returns_all_children_ordered():
    msgs = [
        _msg("u1", None),
        _msg("a1", "u1", ts=1),
        _msg("a2", "u1", ts=2),
        _msg("unrelated", None),
    ]
    kids = children(msgs, "u1")
    assert [k["id"] for k in kids] == ["a1", "a2"]


# ---- linear_history ------------------------------------------------------

def test_linear_history_walks_parent_chain():
    msgs = [
        _msg("u1", None),
        _msg("a1", "u1"),
        _msg("u2", "a1"),
        _msg("a2", "u2"),
        # Sibling branch — should not appear when head is "a2":
        _msg("a1_retry", "u1"),
    ]
    hist = linear_history(msgs, "a2")
    assert [h["id"] for h in hist] == ["u1", "a1", "u2", "a2"]


def test_linear_history_follows_retry_branch():
    msgs = [
        _msg("u1", None),
        _msg("a1_old", "u1"),
        _msg("a1_new", "u1"),     # a retry — head now points here
        _msg("u2", "a1_new"),
        _msg("a2", "u2"),
    ]
    # Head on a2 → history goes through a1_new, not a1_old.
    assert [h["id"] for h in linear_history(msgs, "a2")] == \
        ["u1", "a1_new", "u2", "a2"]


def test_linear_history_unknown_head_is_empty():
    assert linear_history([_msg("u1", None)], "bogus") == []


def test_linear_history_survives_cycles():
    # Malformed data: u1 → u2 → u1. Should terminate, not loop.
    msgs = [{"id": "u1", "parent_id": "u2"}, {"id": "u2", "parent_id": "u1"}]
    hist = linear_history(msgs, "u1")
    # We don't guarantee the exact chain for malformed input, just
    # that it terminates. Length is bounded by node count.
    assert len(hist) <= 2


# ---- is_ancestor ---------------------------------------------------------

def test_is_ancestor_true_on_parent_chain():
    msgs = [_msg("u1", None), _msg("a1", "u1"), _msg("u2", "a1")]
    assert is_ancestor(msgs, "u1", "u2")
    assert is_ancestor(msgs, "a1", "u2")


def test_is_ancestor_false_on_sibling_branch():
    msgs = [
        _msg("u1", None),
        _msg("a1", "u1"),
        _msg("a1_retry", "u1"),  # a1 and a1_retry are siblings
    ]
    assert not is_ancestor(msgs, "a1", "a1_retry")
    assert not is_ancestor(msgs, "a1_retry", "a1")


def test_is_ancestor_self_true():
    msgs = [_msg("u1", None)]
    assert is_ancestor(msgs, "u1", "u1")


# ---- normalize_parent_pointers ------------------------------------------

def test_normalize_fills_in_missing_parent():
    # Legacy messages: no parent_id.
    msgs = [{"id": "u1"}, {"id": "a1"}, {"id": "u2"}]
    normalize_parent_pointers(msgs)
    assert msgs[0]["parent_id"] is None
    assert msgs[1]["parent_id"] == "u1"
    assert msgs[2]["parent_id"] == "a1"


def test_normalize_is_idempotent():
    msgs = [
        {"id": "u1", "parent_id": None},
        {"id": "a1", "parent_id": "u1"},
    ]
    before = [dict(m) for m in msgs]
    normalize_parent_pointers(msgs)
    assert msgs == before


def test_normalize_preserves_explicit_retry_links():
    # Simulated partial migration: a1 and a1_new share parent "u1".
    # normalize shouldn't overwrite them with the prev-in-list chain.
    msgs = [
        {"id": "u1", "parent_id": None},
        {"id": "a1", "parent_id": "u1"},
        {"id": "a1_new", "parent_id": "u1"},
    ]
    normalize_parent_pointers(msgs)
    assert msgs[2]["parent_id"] == "u1"  # NOT a1


# ---- head_or_tip --------------------------------------------------------

def test_head_or_tip_prefers_explicit_head():
    msgs = [_msg("u1", None), _msg("a1", "u1")]
    conv = {"head_id": "u1"}
    assert head_or_tip(conv, msgs) == "u1"


def test_head_or_tip_falls_back_to_last_message():
    msgs = [_msg("u1", None), _msg("a1", "u1")]
    assert head_or_tip({}, msgs) == "a1"


def test_head_or_tip_empty_conv_returns_none():
    assert head_or_tip({}, []) is None


# ---- advance_head -------------------------------------------------------

def test_advance_head_missing_parent_inherits_head():
    conv = {"head_id": "u1", "messages": [_msg("u1", None)]}
    advance_head(conv, {"id": "a1", "role": "assistant"})
    assert conv["messages"][-1]["parent_id"] == "u1"
    assert conv["head_id"] == "a1"


def test_advance_head_explicit_none_is_preserved():
    # Regression: retry of a root user message forks at parent_id=None.
    # advance_head must NOT rewrite that to the current HEAD — doing so
    # collapses the fork into a linear append and breaks the DAG.
    conv = {"head_id": "a1", "messages": [
        _msg("u1", None),
        _msg("a1", "u1"),
    ]}
    advance_head(conv, {"id": "u2", "role": "user", "parent_id": None})
    assert conv["messages"][-1]["parent_id"] is None
    assert conv["head_id"] == "u2"
    # u1 and u2 are now siblings at the root.
    assert [s["id"] for s in siblings(conv["messages"], "u2")] == ["u1", "u2"]


def test_advance_head_explicit_parent_is_preserved():
    conv = {"head_id": "a2", "messages": [
        _msg("u1", None), _msg("a1", "u1"), _msg("a2", "u1"),
    ]}
    advance_head(conv, {"id": "u2", "role": "user", "parent_id": "u1"})
    assert conv["messages"][-1]["parent_id"] == "u1"

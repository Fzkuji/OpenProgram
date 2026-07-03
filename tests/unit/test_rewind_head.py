"""Rewind must never leave head_id on a rewound (dead) node, and ROOT
must never be offered as a rewind target.

Bug: ``list_rewind_points`` returned ROOT (a synthetic user node with
empty output) as a selectable target. Rewinding to it marked ROOT
itself ``rewound=True`` while ``rewind_to``'s ``new_head`` loop found
no earlier seq, so the ``if new_head is not None`` guard skipped
``set_head`` — leaving head pointing at a rewound node. The next user
turn would then anchor its predecessor to that dead node.

Fixes: (1) ``list_rewind_points`` skips ``display=="root"``; (2)
``rewind_to`` sets head unconditionally so rewinding to the very start
yields head=None (empty session), never a rewound node.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from openprogram.store.session.session_store import SessionStore
import openprogram.store.session.session_store as ss_mod
from openprogram.context.nodes import Call, ROLE_USER, ROLE_LLM


@pytest.fixture
def store(monkeypatch):
    s = SessionStore(Path(tempfile.mkdtemp()) / "s")
    # rewind_to / list_rewind_points call default_store() internally.
    monkeypatch.setattr(ss_mod, "_default_store", s)
    return s


def _build_two_turns(store: SessionStore, sid: str) -> None:
    """ROOT → u1 → u1_reply → u2 → u2_reply, head at u2_reply."""
    git, idx = store._open(sid, create_if_missing=True)

    def add(nid, role, *, caller="", pred="", display=None, head=None):
        meta = {}
        if pred:
            meta["predecessor"] = pred
        if display:
            meta["display"] = display
        node = Call(id=nid, role=role, caller=caller, output=nid, metadata=meta)
        seq = idx.append(node, predecessor=pred, caller=caller or meta.get("caller") or "")
        git.write_history(seq, node.role, node.id, node.to_dict())
        idx.set_meta(updated_at=time.time())
        if head is not None:
            idx.set_head(head)

    add("ROOT", ROLE_USER, display="root")
    add("u1", ROLE_USER, caller="ROOT", head="u1")
    add("u1_reply", ROLE_LLM, pred="u1", head="u1_reply")
    add("u2", ROLE_USER, caller="ROOT", pred="u1_reply", head="u2")
    add("u2_reply", ROLE_LLM, pred="u2", head="u2_reply")
    store._persist_meta(git, idx)


def _head_is_rewound(store: SessionStore, sid: str) -> bool:
    _git, idx = store._open(sid)
    hid = idx.head_id
    if not hid:
        return False
    node = idx.nodes_by_id.get(hid)
    return bool((node.metadata or {}).get("rewound")) if node else False


def test_root_not_offered_as_rewind_point(store):
    from openprogram.agent._rewind import list_rewind_points
    _build_two_turns(store, "s1")
    ids = {p["msg_id"] for p in list_rewind_points("s1")}
    assert "ROOT" not in ids
    assert ids == {"u1", "u2"}


def test_rewind_to_second_turn_moves_head_before_it(store):
    from openprogram.agent._rewind import rewind_to
    _build_two_turns(store, "s1")
    rewind_to("s1", "u2")
    _git, idx = store._open("s1")
    assert idx.head_id == "u1_reply"
    assert not _head_is_rewound(store, "s1")


def test_rewind_to_first_turn_lands_on_root(store):
    from openprogram.agent._rewind import rewind_to
    _build_two_turns(store, "s1")
    rewind_to("s1", "u1")
    _git, idx = store._open("s1")
    # ROOT is the only earlier node — head lands there, and ROOT is not
    # rewound, so the next turn anchors cleanly.
    assert idx.head_id == "ROOT"
    assert not _head_is_rewound(store, "s1")


def test_rewind_to_root_leaves_no_stale_head(store):
    """Even if some caller targets ROOT directly (list_rewind_points no
    longer offers it), head must not stay on the rewound ROOT."""
    from openprogram.agent._rewind import rewind_to
    _build_two_turns(store, "s1")
    rewind_to("s1", "ROOT")
    # new_head found no earlier node → head becomes None (empty session),
    # NOT a stale pointer at the now-rewound ROOT.
    assert not _head_is_rewound(store, "s1")
    _git, idx = store._open("s1")
    assert idx.head_id is None

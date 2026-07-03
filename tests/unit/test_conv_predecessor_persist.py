"""Regression guard: every 2nd+ user turn must persist a conv
predecessor pointing at the previous turn's assistant reply.

The bug: webui user turns were written with ``caller=ROOT`` but no
``metadata.predecessor`` (the field was never populated on the write
path), so each continuation turn became a disconnected pseudo-root —
the DAG split into one tree per turn and the Branches panel showed a
fake branch per turn. get_branch(head) then stopped at the current
turn instead of walking back to ROOT, and active_branch_chain lost all
earlier history.

The fix resolves the predecessor from the AUTHORITATIVE store head
(the last-persisted leaf, i.e. the prior reply) at write time. These
tests reproduce the webui write shape — a user node with caller=ROOT
whose predecessor is taken from the store head — and pin that the DAG
chains and get_branch walks the full conversation.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from openprogram.store import SessionStore, GraphStoreShim
from openprogram.context.nodes import Call, ROLE_USER, ROLE_LLM


@pytest.fixture
def db(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions-git")


def _write_user_turn(db: SessionStore, sid: str, uid: str, text: str) -> None:
    """Mirror the fixed webui _append_msg user-node write: caller=ROOT,
    predecessor resolved from the authoritative store head."""
    shim = GraphStoreShim(db, sid)
    if not db.message_exists(sid, "ROOT"):
        shim.append(Call(id="ROOT", role=ROLE_USER, output="",
                         metadata={"display": "root"}))
    sess = db.get_session(sid) or {}
    pred = sess.get("head_id") or ""
    meta = {"source": "web"}
    if pred and pred != "ROOT":
        meta["predecessor"] = pred
    shim.append(Call(id=uid, role=ROLE_USER, output=text,
                     caller="ROOT", metadata=meta))
    db.set_head(sid, uid)


def _write_reply(db: SessionStore, sid: str, rid: str, uid: str) -> None:
    """Mirror persist_assistant_message: reply predecessor = user id."""
    shim = GraphStoreShim(db, sid)
    shim.append(Call(id=rid, role=ROLE_LLM, output="reply",
                     metadata={"source": "web", "predecessor": uid}))
    db.set_head(sid, rid)


def _pred_on_disk(db: SessionStore, sid: str, node_id: str) -> str:
    for m in db.get_messages(sid):
        if m.get("id") == node_id:
            return m.get("predecessor") or ""
    raise AssertionError(f"node {node_id} not found")


def test_first_turn_predecessor_empty(db):
    """Turn 1 hangs off ROOT via caller — no predecessor set."""
    db.create_session("s1", agent_id="a")
    _write_user_turn(db, "s1", "u1", "hi")
    assert _pred_on_disk(db, "s1", "u1") == ""


def test_second_turn_persists_predecessor(db):
    """The 2nd user turn must link back to the 1st turn's reply."""
    db.create_session("s1", agent_id="a")
    _write_user_turn(db, "s1", "u1", "hi")
    _write_reply(db, "s1", "u1_reply", "u1")
    _write_user_turn(db, "s1", "u2", "again")
    assert _pred_on_disk(db, "s1", "u2") == "u1_reply"


def test_get_branch_returns_full_chain(db):
    """With predecessors intact, get_branch(head) walks the whole
    conversation root→head, not just the last turn."""
    db.create_session("s1", agent_id="a")
    _write_user_turn(db, "s1", "u1", "hi")
    _write_reply(db, "s1", "u1_reply", "u1")
    _write_user_turn(db, "s1", "u2", "again")
    _write_reply(db, "s1", "u2_reply", "u2")
    chain = [m.get("id") for m in db.get_branch("s1", "u2_reply")]
    assert chain == ["u1", "u1_reply", "u2", "u2_reply"]


def test_third_turn_chains(db):
    """Three turns stay a single linear chain (no split)."""
    db.create_session("s1", agent_id="a")
    for i in (1, 2, 3):
        _write_user_turn(db, "s1", f"u{i}", f"msg{i}")
        _write_reply(db, "s1", f"u{i}_reply", f"u{i}")
    assert _pred_on_disk(db, "s1", "u2") == "u1_reply"
    assert _pred_on_disk(db, "s1", "u3") == "u2_reply"
    chain = [m.get("id") for m in db.get_branch("s1", "u3_reply")]
    assert chain == ["u1", "u1_reply", "u2", "u2_reply", "u3", "u3_reply"]

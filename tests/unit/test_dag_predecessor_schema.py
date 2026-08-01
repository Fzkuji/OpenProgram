"""session-dag-v2 Decision 1 — ``predecessor`` as a top-level schema
field, write-side invariant, and the ``spawn_branch`` store primitive.

Covers:
  * top-level field serialization round trip (Call / Graph);
  * old-format nodes (metadata.predecessor only) are NOT recognized —
    the field stays None (no compat: old data gets wiped, not migrated);
  * append of a ROOT-level conversational node without a predecessor
    raises ``PredecessorMissingError`` — except the session's first
    node and spawn branch roots;
  * ``spawn_branch`` creates a correct branch root and registers head;
  * ``get_branch`` walks edges only — a broken chain raises
    ``BrokenPredecessorChainError`` instead of being guessed at.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.context.nodes import Call, Graph, ROLE_USER, ROLE_LLM
from openprogram.store import SessionStore, GraphStoreShim
from openprogram.store.session.session_store import (
    BrokenPredecessorChainError,
    PredecessorMissingError,
)


@pytest.fixture
def db(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions-git")


def _seed_turn(db: SessionStore, sid: str) -> None:
    db.create_session(sid, "main", title="t")
    db.append_message(sid, {"id": "u1", "role": "user", "content": "hi",
                            "timestamp": 0, "predecessor": None})
    db.append_message(sid, {"id": "a1", "role": "assistant", "content": "ok",
                            "timestamp": 0, "predecessor": "u1"})


# --- schema field -------------------------------------------------


def test_call_predecessor_roundtrip():
    n = Call(id="n1", role=ROLE_USER, output="hi", predecessor="p0")
    d = n.to_dict()
    assert d["predecessor"] == "p0"
    assert "predecessor" not in (d["metadata"] or {})
    g = Graph.from_dict({"nodes": [d]})
    assert g["n1"].predecessor == "p0"


def test_old_metadata_only_predecessor_not_recognized():
    """Pre-v2 rows carried the edge in metadata only. No compat: the
    field stays None — old data is wiped, not migrated."""
    raw = Call(id="n1", role=ROLE_USER, output="hi",
               metadata={"predecessor": "p0"}).to_dict()
    raw["predecessor"] = None
    g = Graph.from_dict({"nodes": [raw]})
    assert g["n1"].predecessor is None


def test_store_roundtrip_persists_field(db):
    _seed_turn(db, "s1")
    db.invalidate_cache("s1")   # force rebuild from disk
    msgs = {m["id"]: m for m in db.get_messages("s1")}
    assert msgs["a1"]["predecessor"] == "u1"
    pair = db._open("s1")
    assert pair is not None
    _git, idx = pair
    assert idx.nodes_by_id["a1"].predecessor == "u1"
    assert "predecessor" not in (idx.nodes_by_id["a1"].metadata or {})


# --- write invariant ----------------------------------------------


def test_append_without_predecessor_rejected(db):
    _seed_turn(db, "s1")
    with pytest.raises(PredecessorMissingError) as ei:
        db.append_message("s1", {"id": "u2", "role": "user",
                                 "content": "orphan", "timestamp": 1,
                                 "predecessor": None})
    assert "s1" in str(ei.value) and "u2" in str(ei.value)


def test_shim_append_without_predecessor_rejected(db):
    _seed_turn(db, "s1")
    with pytest.raises(PredecessorMissingError):
        GraphStoreShim(db, "s1").append(
            Call(id="u2", role=ROLE_USER, output="orphan", caller="ROOT"))


def test_first_node_exempt(db):
    db.create_session("s1", "main", title="t")
    db.append_message("s1", {"id": "u1", "role": "user", "content": "hi",
                             "timestamp": 0, "predecessor": None})
    assert db.message_exists("s1", "u1")


def test_spawn_root_exempt_and_code_nodes_ignored(db):
    _seed_turn(db, "s1")
    # Spawn root via the primitive — legal despite predecessor=None.
    rid = db.spawn_branch("s1", "a1", source="agent_spawn", prompt="sub")
    assert db.message_exists("s1", rid)
    # Code nodes are not conversational — the invariant ignores them.
    GraphStoreShim(db, "s1").append(
        Call(id="c1", role="code", name="fn", input={}, output=None))


# --- spawn_branch primitive ----------------------------------------


def test_spawn_branch_shape(db):
    _seed_turn(db, "s1")
    rid = db.spawn_branch("s1", "a1", source="agent_spawn",
                          name="probe", node_id="sub_u", prompt="do it")
    assert rid == "sub_u"
    pair = db._open("s1")
    _git, idx = pair
    node = idx.nodes_by_id["sub_u"]
    assert node.predecessor is None
    assert node.caller == "a1"
    assert node.role == ROLE_USER
    assert node.output == "do it"
    assert node.metadata["source"] == "agent_spawn"
    assert node.metadata["spawn_branch_root"] is True
    # Head registered on the new branch root.
    assert (db.get_session("s1") or {}).get("head_id") == "sub_u"
    # Label registered.
    assert db.get_branch_meta("s1", "sub_u").get("name") == "probe"


def test_spawn_branch_isolated_in_get_branch(db):
    _seed_turn(db, "s1")
    rid = db.spawn_branch("s1", "a1", source="agent_spawn", prompt="sub")
    db.append_message("s1", {"id": "sub_a", "role": "assistant",
                             "content": "sub reply", "timestamp": 1,
                             "predecessor": rid})
    chain = [m["id"] for m in db.get_branch("s1", "sub_a")]
    assert chain == [rid, "sub_a"]   # stops at the spawn root


# --- get_branch: edges only ----------------------------------------


def test_get_branch_pure_edges(db):
    _seed_turn(db, "s1")
    db.append_message("s1", {"id": "u2", "role": "user", "content": "more",
                             "timestamp": 1, "predecessor": "a1"})
    chain = [m["id"] for m in db.get_branch("s1", "u2")]
    assert chain == ["u1", "a1", "u2"]


def test_get_branch_broken_chain_raises(db):
    _seed_turn(db, "s1")
    # Corrupt the index directly (the store's append path would have
    # rejected this node) — a mid-session conv node with no edge.
    pair = db._open("s1")
    _git, idx = pair
    idx.append(Call(id="ghost", role=ROLE_LLM, output="?"),
               predecessor=None, caller=None)
    with pytest.raises(BrokenPredecessorChainError) as ei:
        db.get_branch("s1", "ghost")
    assert "ghost" in str(ei.value)

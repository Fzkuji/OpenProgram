"""Deleting single DAG nodes: splice, ownership, undo."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from openprogram.state.store.session.nodes import Call, ROLE_CODE
from openprogram.state.store import SessionNodeWriter
from openprogram.state.store import SessionStore
from openprogram.state.store.session.session_store.node_deletion import NodeDeletionError


@pytest.fixture
def db(tmp_path: Path) -> SessionStore:
    store = SessionStore(tmp_path / "sessions")
    store.create_session("s1", agent_id="a")
    return store


def _msg(db, mid, role="user", parent=None):
    db.append_message("s1", {
        "id": mid, "role": role, "content": mid,
        "predecessor": parent, "timestamp": time.time(),
    })


def _chain(db):
    # u1 → a1 (calls c1) → u2 → a2 (calls c2) → u3 → a3
    _msg(db, "u1")
    _msg(db, "a1", "assistant", "u1")
    _msg(db, "u2", parent="a1")
    _msg(db, "a2", "assistant", "u2")
    _msg(db, "u3", parent="a2")
    _msg(db, "a3", "assistant", "u3")
    shim = SessionNodeWriter(db, "s1")
    for cid, owner in (("c1", "a1"), ("c2", "a2")):
        shim.append(Call(id=cid, role=ROLE_CODE, name="bash", caller=owner,
                         output="ok", metadata={"tool_call_id": cid}))
    db.set_head("s1", "a3")


def _ids(db):
    return [m["id"] for m in db.get_branch("s1")]


def test_deleting_a_mid_turn_splices_what_followed(db):
    _chain(db)
    journal = db.delete_nodes("s1", ["u2", "a2"])
    assert _ids(db) == ["u1", "a1", "u3", "a3"]
    # a2's tool call is its own work and goes with it; a1's stays.
    assert set(journal["deleted"]) == {"u2", "a2", "c2"}
    assert db.message_exists("s1", "c1") and not db.message_exists("s1", "c2")
    assert journal["reparented"] == [{"id": "u3", "old": "a2", "new": "a1"}]


def test_deleting_a_single_node_is_allowed(db):
    _chain(db)
    db.delete_nodes("s1", ["u2"])
    assert _ids(db) == ["u1", "a1", "a2", "u3", "a3"]
    db.delete_nodes("s1", ["c1"])
    assert not db.message_exists("s1", "c1") and db.message_exists("s1", "a1")


def test_deleting_head_moves_it_to_the_survivor_above(db):
    _chain(db)
    db.set_branch_name("s1", "a3", "mine")
    db.delete_nodes("s1", ["a3"])
    assert db.get_session("s1")["head_id"] == "u3"
    assert db.get_branch_meta("s1", "u3")["name"] == "mine"


def test_undo_puts_everything_back(db):
    _chain(db)
    db.set_branch_name("s1", "a3", "mine")
    before = _ids(db)
    journal = db.delete_nodes("s1", ["u2", "a2", "a3"])
    db.restore_deleted_nodes("s1", journal["op_id"])
    assert _ids(db) == before
    assert db.get_session("s1")["head_id"] == "a3"
    assert db.message_exists("s1", "c2")
    assert db.get_branch_meta("s1", "a3")["name"] == "mine"
    assert db.get_branch_meta("s1", "u3") == {}
    with pytest.raises(NodeDeletionError):
        db.restore_deleted_nodes("s1", journal["op_id"])


def test_trash_stays_outside_the_session_repo(db):
    _chain(db)
    journal = db.delete_nodes("s1", ["u2"])
    trash = db.root_path / ".deleted-nodes" / "s1" / journal["op_id"]
    assert (trash / "journal.json").is_file()
    assert [node["id"] for node in journal["nodes"]].count("u2") == 1


def test_structure_nodes_refuse(db):
    _chain(db)
    with pytest.raises(NodeDeletionError):
        db.delete_nodes("s1", ["ghost"])
    root = db.spawn_branch("s1", "c1", source="agent_spawn", prompt="go",
                           register_head=False)
    with pytest.raises(NodeDeletionError):
        db.delete_nodes("s1", [root])
    # Deleting the call that spawned the agent takes the agent branch too.
    _msg(db, "ag1", "assistant", root)
    journal = db.delete_nodes("s1", ["c1"])
    assert {"c1", root, "ag1"} <= set(journal["deleted"])

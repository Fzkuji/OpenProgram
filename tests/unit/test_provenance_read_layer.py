"""Provenance read-layer — the LLM-free seam memory maps from.

Pins the contract the Phase-2 memory extractor will build on: an
incremental node cursor over the session DAG, a node → Provenance
coordinate (bi-temporal, hashable), and accessors for the session /
project git commit history. See
docs/design/memory/entity-session-cache.md §5.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from openprogram.store import (
    Provenance,
    SessionStore,
    iter_nodes_since,
    node_provenance,
    project_commits,
    session_commits,
    session_project_id,
)


def _append(db, sess, mid, *, role="user", content="x", parent=None):
    db.append_message(sess, {
        "id": mid, "role": role, "content": content,
        "called_by": parent, "timestamp": time.time(),
    })


@pytest.fixture
def db(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions")


# incremental cursor


def test_iter_nodes_since_returns_all_by_default(db):
    _append(db, "s", "n0")
    _append(db, "s", "n1", parent="n0")
    _append(db, "s", "n2", parent="n1")
    nodes = iter_nodes_since(db, "s")
    assert [n.id for n in nodes] == ["n0", "n1", "n2"]


def test_iter_nodes_since_is_incremental(db):
    _append(db, "s", "n0")
    _append(db, "s", "n1", parent="n0")
    # Record the cursor after the first pass, append more, ask for the new.
    first = iter_nodes_since(db, "s")
    cursor = max(n.seq for n in first)
    _append(db, "s", "n2", parent="n1")
    _append(db, "s", "n3", parent="n2")
    fresh = iter_nodes_since(db, "s", after_seq=cursor)
    assert [n.id for n in fresh] == ["n2", "n3"]


def test_iter_nodes_since_unknown_session_is_empty(db):
    assert iter_nodes_since(db, "nope") == []


# node → provenance coordinate


def test_node_provenance_coordinates(db):
    _append(db, "s", "n0", content="alpha")
    db.update_session("s", project_id="proj_test")
    node = iter_nodes_since(db, "s")[0]

    prov = node_provenance(db, "s", node, ingestion_time=123.0)
    assert prov.session_id == "s"
    assert prov.node_ids == ("n0",)
    assert prov.project_id == "proj_test"
    assert prov.event_time == pytest.approx(node.created_at)
    assert prov.ingestion_time == 123.0
    assert prov.commit is None


def test_node_provenance_stamps_ingestion_time_by_default(db):
    _append(db, "s", "n0")
    node = iter_nodes_since(db, "s")[0]
    before = time.time()
    prov = node_provenance(db, "s", node)
    assert prov.ingestion_time >= before


def test_provenance_is_frozen_and_hashable(db):
    _append(db, "s", "n0")
    node = iter_nodes_since(db, "s")[0]
    prov = node_provenance(db, "s", node, ingestion_time=1.0)
    # frozen → immutable
    with pytest.raises(Exception):
        prov.session_id = "other"  # type: ignore[misc]
    # hashable → usable as a dedup key
    assert prov in {prov}


def test_session_project_id_empty_when_unset(db):
    _append(db, "s", "n0")
    assert session_project_id(db, "s") == ""


# commit history accessors


def test_session_commits_surfaces_turn_boundaries(db):
    _append(db, "s", "n0")
    db.commit_turn("s", "turn: hello")
    commits = session_commits(db, "s")
    # newest first: our turn commit, then the "session init" empty commit
    assert len(commits) >= 1
    assert commits[0].message == "turn: hello"
    # CommitInfo carries the time axis memory needs
    assert commits[0].sha and commits[0].timestamp >= 0


def test_session_commits_unknown_session_is_empty(db):
    assert session_commits(db, "nope") == []


def test_project_commits_unknown_project_is_empty():
    assert project_commits("proj_does_not_exist_xyz") == []

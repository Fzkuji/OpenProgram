"""SessionStore in-memory cache: bounded LRU eviction + lossless rebuild.

The ``SessionMemoryIndex`` cache is pure (rebuildable from git), so the
store size-caps it without losing data. These tests pin the two
properties that make the cap safe:

  * the cache never exceeds ``cache_cap`` (memory-leak backstop);
  * an evicted session rebuilds losslessly from disk on next access;
  * the most-recently-used session is never the one evicted (so an
    in-flight turn, which is always MRU, can't be dropped).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from openprogram.store import SessionStore


def _append(db, sess, mid, *, role="user", content="x", parent=None):
    # append_message goes through _open(create_if_missing=True) and writes
    # the history file directly — no project-store resolution, so the test
    # stays hermetic to tmp_path.
    db.append_message(sess, {
        "id": mid, "role": role, "content": content,
        "predecessor": parent, "timestamp": time.time(),
    })


@pytest.fixture
def db(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions", cache_cap=2)


def test_cache_never_exceeds_cap(db):
    for i in range(5):
        _append(db, f"s{i}", "n0", content=f"hello-{i}")
    assert len(db._sessions) <= 2


def test_lru_evicts_oldest_touched(db):
    _append(db, "a", "n0")
    _append(db, "b", "n0")
    # Touch a → a becomes MRU, b becomes LRU.
    db.get_messages("a")
    # Opening c (cap=2) must evict the LRU, which is b.
    _append(db, "c", "n0")
    assert "a" in db._sessions
    assert "c" in db._sessions
    assert "b" not in db._sessions


def test_evicted_session_rebuilds_losslessly(db):
    _append(db, "a", "n0", content="alpha")
    _append(db, "a", "n1", content="beta", parent="n0")
    # Persist the head pointer the way a real turn does (the dispatcher
    # calls update_session(head_id=...) / commit_turn at turn end, which
    # is what writes head_id into meta.json — append_message advances head
    # only in memory, deferring the meta write to the turn boundary).
    db.set_head("a", "n1")
    # Push a out of the cache (cap=2): opening b then c evicts a.
    _append(db, "b", "n0")
    _append(db, "c", "n0")
    assert "a" not in db._sessions  # evicted

    msgs = db.get_messages("a")  # forces a rebuild from git
    assert [m["id"] for m in msgs] == ["n0", "n1"]
    # The branch chain (head + parent edge) also survives the round-trip.
    chain = db.get_branch("a")
    assert [m["id"] for m in chain] == ["n0", "n1"]


def test_default_cap_is_generous(tmp_path: Path):
    store = SessionStore(tmp_path / "s")
    assert store._cache_cap >= 256


def test_env_overrides_cap(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_SESSION_CACHE_CAP", "7")
    store = SessionStore(tmp_path / "s")
    assert store._cache_cap == 7


@pytest.mark.parametrize(
    "session_id",
    ["", ".", "..", "../outside", "alias/../not-a-session"],
)
def test_read_boundary_rejects_non_session_paths(tmp_path: Path, session_id: str):
    root = tmp_path / "sessions"
    store = SessionStore(root)
    (root / "not-a-session").mkdir()
    (tmp_path / "outside").mkdir(exist_ok=True)

    assert store.get_session(session_id) is None
    assert store.get_branch(session_id) == []
    assert session_id not in store._sessions


def test_read_boundary_rejects_plain_directory_and_symlink_alias(tmp_path: Path):
    root = tmp_path / "sessions"
    store = SessionStore(root)
    (root / "not-a-session").mkdir()
    store.create_session("valid", "main")
    try:
        (root / "alias").symlink_to(root / "valid", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation is unavailable: {exc}")

    assert store.get_session("not-a-session") is None
    assert store.get_session("alias") is None
    assert "not-a-session" not in store._sessions
    assert "alias" not in store._sessions


def test_read_boundary_preserves_current_and_history_only_session(tmp_path: Path):
    root = tmp_path / "sessions"
    store = SessionStore(root)
    store.create_session("current", "main", title="Current")
    _append(store, "history-only", "n0", content="legacy")

    fresh = SessionStore(root)
    assert (fresh.get_session("current") or {})["title"] == "Current"
    assert [row["content"] for row in fresh.get_branch("history-only")] == [
        "legacy"
    ]

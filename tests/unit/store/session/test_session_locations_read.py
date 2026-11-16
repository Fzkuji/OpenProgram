"""The location index is parsed once per change of its file.

Every lookup of a conversation's folder reads the index; a list that
projects hundreds of runs used to read and resolve it a thousand times.
"""
from __future__ import annotations

import json

from openprogram.state.store.session.session_store import SessionStore
from openprogram.state.store.session.session_store import shared


def test_unchanged_index_is_not_read_again(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions")
    store._record_location("placed", tmp_path / "sessions" / "default" / "chats" / "placed")
    reads = []
    real_read = shared.read_text_with_retry
    monkeypatch.setattr(shared, "read_text_with_retry", lambda path: reads.append(path) or real_read(path))

    first = store._load_locations()
    second = store._load_locations()
    assert first == second and "placed" in first
    assert len(reads) <= 1
    # Callers get their own copy to change.
    second["other"] = "x"
    assert "other" not in store._load_locations()


def test_rewritten_index_is_read_again(tmp_path):
    reader = SessionStore(tmp_path / "sessions")
    writer = SessionStore(tmp_path / "sessions")
    assert "placed" not in reader._load_locations()
    writer._record_location("placed", tmp_path / "sessions" / "default" / "chats" / "placed")
    assert "placed" in reader._load_locations()
    # A rewrite of the same size and second is a new file all the same.
    path = reader._locations_path()
    data = json.loads(path.read_text())
    data["placed"] = data["placed"][:-1] + "X"
    shared.atomic_write_text(path, json.dumps(data, indent=2))
    assert reader._load_locations()["placed"].endswith("X")


def test_missing_index_reads_empty(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    assert store._load_locations() == {}

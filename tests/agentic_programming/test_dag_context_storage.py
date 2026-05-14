"""GraphStore: SQLite persistence for the flat-DAG context model.

Verifies that nodes / sessions roundtrip through the DB, FTS works, and
the cross-session helpers behave.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.context.nodes import (
    Graph,
    UserMessage,
    ModelCall,
    FunctionCall,
)
from openprogram.context.storage import (
    GraphStore,
    init_db,
    list_session_rows,
    read_session_row,
    delete_session,
    search_across_sessions,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "chat.sqlite"


def _make_minimal_graph() -> Graph:
    g = Graph()
    u = g.add_user_message("hello")
    m = g.add_model_call(
        model="claude-opus",
        reads=[u.id],
        output="hi back",
    )
    g.add_function_call(
        function_name="ping",
        arguments={"host": "127.0.0.1"},
        called_by=m.id,
        result="pong",
    )
    return g


# ── init_db / session row ─────────────────────────────────────────────


def test_init_db_is_idempotent(db_path):
    init_db(db_path)
    init_db(db_path)
    assert db_path.exists()


def test_create_session_row(db_path):
    init_db(db_path)
    store = GraphStore(db_path, "s1")
    assert not store.session_exists()
    store.create_session_row(title="hi", model="claude")
    assert store.session_exists()

    row = read_session_row(db_path, "s1")
    assert row["id"] == "s1"
    assert row["title"] == "hi"
    assert row["model"] == "claude"


def test_list_session_rows_sorted_by_updated_at(db_path):
    init_db(db_path)
    GraphStore(db_path, "a").create_session_row(title="A")
    GraphStore(db_path, "b").create_session_row(title="B")
    GraphStore(db_path, "c").create_session_row(title="C")
    GraphStore(db_path, "b").update_session_row(title="B updated")

    rows = list_session_rows(db_path)
    # B was touched last → first
    assert rows[0]["id"] == "b"


# ── append / load ─────────────────────────────────────────────────────


def test_append_and_load_roundtrip(db_path):
    init_db(db_path)
    store = GraphStore(db_path, "s1")
    store.create_session_row()
    g = _make_minimal_graph()
    for n in g:
        store.append(n)

    g2 = store.load()
    assert len(g2) == 3
    assert g2._last_id == g._last_id
    for nid in g.nodes:
        a = g.nodes[nid]
        b = g2.nodes[nid]
        assert type(a) is type(b)
        assert a.called_by == b.called_by
        if a.is_llm():
            assert a.reads == b.reads
            assert a.output == b.output
        if a.is_code():
            assert a.called_by == b.called_by
            assert a.arguments == b.arguments
            assert a.result == b.result


def test_save_batch(db_path):
    init_db(db_path)
    store = GraphStore(db_path, "s1")
    store.create_session_row()
    g = _make_minimal_graph()
    store.save(g)
    g2 = store.load()
    assert len(g2) == 3


def test_save_skips_already_persisted(db_path):
    init_db(db_path)
    store = GraphStore(db_path, "s1")
    store.create_session_row()
    g = _make_minimal_graph()
    store.save(g)
    # Save again — no new rows
    store.save(g)
    g2 = store.load()
    assert len(g2) == 3


def test_append_refuses_duplicate(db_path):
    init_db(db_path)
    store = GraphStore(db_path, "s1")
    store.create_session_row()
    g = Graph()
    n = g.add_user_message("once")
    store.append(n)
    with pytest.raises(ValueError, match="append-only"):
        store.append(n)


def test_last_node_id_updated_on_append(db_path):
    init_db(db_path)
    store = GraphStore(db_path, "s1")
    store.create_session_row()
    g = Graph()
    u = g.add_user_message("hi")
    store.append(u)
    row = read_session_row(db_path, "s1")
    assert row["last_node_id"] == u.id


# ── Sessions don't leak across each other ─────────────────────────────


def test_two_sessions_isolated(db_path):
    init_db(db_path)
    s1 = GraphStore(db_path, "alpha")
    s2 = GraphStore(db_path, "beta")
    s1.create_session_row()
    s2.create_session_row()

    g1 = Graph()
    u1 = g1.add_user_message("alpha message")
    s1.append(u1)

    g2 = Graph()
    u2 = g2.add_user_message("beta message")
    s2.append(u2)

    assert len(s1.load()) == 1
    assert len(s2.load()) == 1
    assert u1.id in s1.load().nodes
    assert u1.id not in s2.load().nodes


# ── Delete ────────────────────────────────────────────────────────────


def test_delete_removes_session_and_nodes(db_path):
    init_db(db_path)
    store = GraphStore(db_path, "to_delete")
    store.create_session_row()
    g = _make_minimal_graph()
    store.save(g)

    assert delete_session(db_path, "to_delete") is True
    assert not store.session_exists()
    # nodes table should have nothing for this session now
    g2 = store.load()
    assert len(g2) == 0


def test_delete_missing_returns_false(db_path):
    init_db(db_path)
    assert delete_session(db_path, "nope") is False


# ── FTS ───────────────────────────────────────────────────────────────


def test_search_within_session(db_path):
    init_db(db_path)
    store = GraphStore(db_path, "s1")
    store.create_session_row()
    g = Graph()
    u1 = g.add_user_message("the quick brown fox jumps over")
    u2 = g.add_user_message("hello world")
    store.append(u1)
    store.append(u2)

    hits = store.search("fox")
    assert u1.id in hits
    assert u2.id not in hits


def test_search_across_sessions(db_path):
    init_db(db_path)
    s1 = GraphStore(db_path, "alpha")
    s2 = GraphStore(db_path, "beta")
    s1.create_session_row()
    s2.create_session_row()
    g1 = Graph()
    n1 = g1.add_user_message("apples and oranges")
    s1.append(n1)
    g2 = Graph()
    n2 = g2.add_user_message("just oranges here")
    s2.append(n2)

    hits = search_across_sessions(db_path, "oranges")
    pairs = set(hits)
    assert ("alpha", n1.id) in pairs
    assert ("beta", n2.id) in pairs


def test_search_indexes_model_output(db_path):
    init_db(db_path)
    store = GraphStore(db_path, "s1")
    store.create_session_row()
    g = Graph()
    u = g.add_user_message("ask")
    m = g.add_model_call(
        model="claude-opus",
        reads=[u.id],
        output="the surprising answer is forty two",
    )
    store.append(u)
    store.append(m)
    hits = store.search("forty")
    assert m.id in hits


# ── update_session_row ────────────────────────────────────────────────


def test_update_session_row_changes_fields(db_path):
    init_db(db_path)
    store = GraphStore(db_path, "s1")
    store.create_session_row(title="old", model="claude")
    store.update_session_row(title="new", model="gpt-4")
    row = read_session_row(db_path, "s1")
    assert row["title"] == "new"
    assert row["model"] == "gpt-4"


def test_update_session_row_extra_json(db_path):
    init_db(db_path)
    import json
    store = GraphStore(db_path, "s1")
    store.create_session_row()
    store.update_session_row(extra={"workspace": "/tmp/x"})
    row = read_session_row(db_path, "s1")
    assert json.loads(row["extra_json"]) == {"workspace": "/tmp/x"}

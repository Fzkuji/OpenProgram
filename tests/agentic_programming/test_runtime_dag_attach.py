"""Runtime DAG-attach: store + head_id + append_node basics.

These verify only the new fields/methods, not exec()/render_context()
behavior — that's the next step.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.agentic_programming.runtime import Runtime
from openprogram.context.nodes import UserMessage, ModelCall, FunctionCall
from openprogram.context.storage import GraphStore, init_db


@pytest.fixture
def store(tmp_path: Path) -> GraphStore:
    db = tmp_path / "x.sqlite"
    init_db(db)
    s = GraphStore(db, "s1")
    s.create_session_row()
    return s


@pytest.fixture
def rt() -> Runtime:
    # No real provider — we never call exec() here.
    return Runtime(call=lambda *a, **kw: "", model="dummy")


# ── Defaults ─────────────────────────────────────────────────────


def test_runtime_starts_with_no_dag_target(rt):
    assert rt.store is None
    assert rt.head_id is None


def test_append_node_is_noop_when_no_store(rt):
    n = UserMessage(content="hello")
    rt.append_node(n)
    # No exception; head_id stays None.
    assert rt.head_id is None


# ── attach / detach ──────────────────────────────────────────────


def test_attach_sets_store_and_head(rt, store):
    rt.attach_store(store, head_id="seed-id")
    assert rt.store is store
    assert rt.head_id == "seed-id"


def test_detach_returns_final_head(rt, store):
    rt.attach_store(store, head_id=None)
    n = UserMessage(content="x")
    rt.append_node(n)
    final = rt.detach_store()
    assert final == n.id
    assert rt.store is None
    assert rt.head_id is None


# ── append_node behavior ─────────────────────────────────────────


def test_append_advances_head_id(rt, store):
    rt.attach_store(store)
    n1 = UserMessage(content="first")
    rt.append_node(n1)
    assert rt.head_id == n1.id
    n2 = ModelCall(model="x", output="reply")
    rt.append_node(n2)
    assert rt.head_id == n2.id


def test_append_assigns_seq_monotonically(rt, store):
    """append should set node.seq to a monotonically-increasing integer."""
    rt.attach_store(store)
    u = UserMessage(content="q")
    assert u.seq == -1   # unset before append
    rt.append_node(u)
    assert u.seq == 0
    m = ModelCall(model="x", output="a")
    rt.append_node(m)
    assert m.seq == 1
    assert m.seq > u.seq


def test_append_respects_explicit_called_by(rt, store):
    """called_by is a logical caller pointer, not derived from
    sequence — caller can set it explicitly."""
    rt.attach_store(store)
    u1 = UserMessage(content="q1")
    rt.append_node(u1)
    u2 = UserMessage(content="q2")
    rt.append_node(u2)
    # A code Call triggered by u1, not by the latest u2
    branch = FunctionCall(
        function_name="reroll", arguments={},
        result="alt", called_by=u1.id,
    )
    rt.append_node(branch)
    assert branch.called_by == u1.id


def test_append_persists_to_store(rt, store):
    rt.attach_store(store)
    u = UserMessage(content="persist me")
    rt.append_node(u)
    # Reload from disk and verify
    g = store.load()
    assert u.id in g.nodes
    assert g.nodes[u.id].content == "persist me"


def test_append_function_call_with_owning_metadata(rt, store):
    """FunctionCall and ModelCall append normally; metadata roundtrips."""
    rt.attach_store(store)
    fc = FunctionCall(
        function_name="my_fn",
        arguments={"a": 1},
        result={"ok": True},
        called_by="",
        metadata={"expose": "io"},
    )
    rt.append_node(fc)
    g = store.load()
    loaded = g.nodes[fc.id]
    assert loaded.is_code()
    assert loaded.metadata == {"expose": "io"}

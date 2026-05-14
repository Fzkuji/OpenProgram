"""Flat-DAG node model: Call / Graph / helpers.

Tests cover:
  - Unified Call node type (role / name / input / output / reads /
    called_by / seq / metadata)
  - Graph.add() assigns monotonic seq
  - Graph iteration is seq-ordered
  - Helpers (last_user_message, linear_back_to, branch_terminals,
    branch_internal, fold_history) work over the new model
  - Serialize-roundtrip drops legacy 'predecessor' fields cleanly
"""

from __future__ import annotations

import pytest

from openprogram.context.nodes import (
    Call,
    Graph,
    ROLE_USER,
    ROLE_LLM,
    ROLE_CODE,
    UserMessage,
    ModelCall,
    FunctionCall,
    last_user_message,
    linear_back_to,
    branch_terminals,
    branch_internal,
    fold_history,
)


# ── Call basics ─────────────────────────────────────────────────────


def test_call_has_role_and_seq_default_unset():
    c = Call(role=ROLE_USER, output="hi")
    assert c.role == ROLE_USER
    assert c.seq == -1                  # unset until added to a Graph
    assert c.id and len(c.id) >= 8

    assert c.is_user() and not c.is_llm() and not c.is_code()


def test_role_constants():
    assert ROLE_USER == "user"
    assert ROLE_LLM == "llm"
    assert ROLE_CODE == "code"


# ── Backward-compat factories ──────────────────────────────────────


def test_user_message_factory_returns_call():
    n = UserMessage(content="hello")
    assert isinstance(n, Call)
    assert n.role == ROLE_USER
    assert n.output == "hello"
    assert n.content == "hello"          # property accessor


def test_model_call_factory_returns_call():
    n = ModelCall(model="opus", reads=["x"], output="reply",
                  system_prompt="be terse")
    assert n.is_llm()
    assert n.name == "opus" and n.model == "opus"   # property
    assert n.reads == ["x"]
    assert n.output == "reply"
    assert n.system_prompt == "be terse"


def test_function_call_factory_returns_call():
    n = FunctionCall(function_name="search", arguments={"q": "x"},
                     result={"hits": 3}, called_by="parent_id")
    assert n.is_code()
    assert n.name == "search" and n.function_name == "search"
    assert n.arguments == {"q": "x"}
    assert n.result == {"hits": 3}
    assert n.called_by == "parent_id"


# ── Graph: append + seq ─────────────────────────────────────────────


@pytest.fixture
def graph() -> Graph:
    return Graph()


def test_graph_add_assigns_monotonic_seq(graph):
    a = graph.add(Call(role=ROLE_USER, output="a"))
    b = graph.add(Call(role=ROLE_LLM, output="b"))
    c = graph.add(Call(role=ROLE_CODE, name="f"))
    assert a.seq == 0
    assert b.seq == 1
    assert c.seq == 2


def test_graph_rejects_duplicate_id(graph):
    n = Call(id="dup", role=ROLE_USER)
    graph.add(n)
    with pytest.raises(ValueError):
        graph.add(Call(id="dup", role=ROLE_USER))


def test_graph_iter_is_seq_ordered(graph):
    """Even when nodes are inserted with out-of-order seq (e.g. loaded
    from DB), iteration goes by seq."""
    graph.nodes["a"] = Call(id="a", role=ROLE_USER, seq=2)
    graph.nodes["b"] = Call(id="b", role=ROLE_USER, seq=0)
    graph.nodes["c"] = Call(id="c", role=ROLE_USER, seq=1)
    assert [n.id for n in graph] == ["b", "c", "a"]


def test_graph_last_returns_max_seq(graph):
    a = graph.add(Call(role=ROLE_USER))
    b = graph.add(Call(role=ROLE_LLM))
    assert graph.last().id == b.id
    assert graph._last_id == b.id


def test_graph_convenience_builders(graph):
    u = graph.add_user_message("hi")
    m = graph.add_model_call(model="x", reads=[u.id], output="hello")
    c = graph.add_function_call(function_name="tool", arguments={"q": 1},
                                 called_by=m.id, result="ok")
    assert u.is_user() and u.output == "hi"
    assert m.is_llm() and m.reads == [u.id]
    assert c.is_code() and c.called_by == m.id
    assert c.result == "ok"


# ── Graph.update ───────────────────────────────────────────────────


def test_graph_update_existing_node(graph):
    """Used by the @agentic_function exit path: append on entry with
    output=None, update output on exit."""
    n = graph.add(Call(role=ROLE_CODE, name="fn", output=None))
    graph.update(n.id, output="final_result")
    assert graph[n.id].output == "final_result"


def test_graph_update_merges_metadata(graph):
    n = graph.add(Call(role=ROLE_CODE, metadata={"expose": "io"}))
    graph.update(n.id, metadata={"status": "success"})
    assert graph[n.id].metadata == {"expose": "io", "status": "success"}


def test_graph_update_unknown_raises(graph):
    with pytest.raises(KeyError):
        graph.update("nope", output="x")


# ── reads validation ───────────────────────────────────────────────


def test_add_model_call_rejects_unknown_reads(graph):
    with pytest.raises(ValueError, match="unknown ids"):
        graph.add_model_call(model="x", reads=["nope"], output="")


# ── Helpers ─────────────────────────────────────────────────────────


@pytest.fixture
def chat_graph() -> Graph:
    """A small chronological chat:
      n1 user → n2 llm → n3 user → n4 llm
    """
    g = Graph()
    n1 = g.add_user_message("q1")
    n2 = g.add_model_call(model="x", reads=[n1.id], output="a1")
    n3 = g.add_user_message("q2")
    n4 = g.add_model_call(model="x", reads=[n1.id, n2.id, n3.id],
                          output="a2")
    return g


def test_last_user_message_returns_latest_by_seq(chat_graph):
    n = last_user_message(chat_graph)
    assert n is not None
    assert n.output == "q2"


def test_linear_back_to_returns_chain_inclusive(chat_graph):
    nodes = list(chat_graph)
    target = nodes[1]  # second node (the first llm)
    ids = linear_back_to(chat_graph, target.id)
    # All nodes with seq >= target.seq, in seq order, target first.
    assert ids[0] == target.id
    assert ids[-1] == nodes[-1].id


def test_linear_back_to_target_not_found(chat_graph):
    with pytest.raises(ValueError):
        linear_back_to(chat_graph, "nonexistent")


# ── called_by lineage helpers ───────────────────────────────────


def test_branch_terminals_and_internal_via_called_by():
    """Build a fan-out scenario manually using called_by edges:

        spawn (n2)
         ├─ left_a → left_b
         └─ right_a → right_b
    """
    g = Graph()
    spawn = g.add_function_call(function_name="spawn", arguments={},
                                 called_by="")
    left_a = g.add(Call(role=ROLE_LLM, name="left",
                        output="L1", called_by=spawn.id))
    left_b = g.add(Call(role=ROLE_LLM, name="left",
                        output="L2", called_by=left_a.id))
    right_a = g.add(Call(role=ROLE_LLM, name="right",
                         output="R1", called_by=spawn.id))
    right_b = g.add(Call(role=ROLE_LLM, name="right",
                         output="R2", called_by=right_a.id))

    terminals = branch_terminals(spawn.id, g)
    assert set(terminals) == {left_b.id, right_b.id}

    left_chain = branch_internal(spawn.id, left_b.id, g)
    assert left_chain == [spawn.id, left_a.id, left_b.id]

    right_chain = branch_internal(spawn.id, right_b.id, g)
    assert right_chain == [spawn.id, right_a.id, right_b.id]


def test_branch_internal_rejects_outside_lineage(chat_graph):
    nodes = list(chat_graph)
    with pytest.raises(ValueError):
        branch_internal(nodes[0].id, nodes[-1].id, chat_graph)


# ── fold_history ─────────────────────────────────────────────────


def test_fold_history_includes_full_current_turn():
    """fold_history should include every node in the current turn
    plus collapsed (user, final-llm) pairs for prior turns."""
    g = Graph()
    n1 = g.add_user_message("q1")
    n2 = g.add_model_call(model="x", reads=[n1.id], output="a1-mid")
    n3 = g.add_model_call(model="x", reads=[n1.id, n2.id], output="a1-final")
    n4 = g.add_user_message("q2")
    n5 = g.add_model_call(model="x", reads=[n1.id, n3.id, n4.id],
                          output="a2-mid")
    n6 = g.add_model_call(model="x", reads=[n4.id, n5.id], output="a2-final")

    folded = fold_history(n6.id, g)
    # Prior turn (turn 1) collapses to (n1 user, n3 final-llm).
    # Current turn (turn 2) includes n4, n5, n6 verbatim.
    assert folded[0] == n1.id
    assert folded[1] == n3.id   # final llm of prior turn (not n2-mid)
    assert n2.id not in folded
    assert n4.id in folded
    assert n5.id in folded
    assert n6.id in folded


# ── Serialization roundtrip ─────────────────────────────────────────


def test_to_dict_and_from_dict_roundtrip(chat_graph):
    d = chat_graph.to_dict()
    g2 = Graph.from_dict(d)
    assert len(g2) == len(chat_graph)
    for n_orig in chat_graph:
        n_new = g2[n_orig.id]
        assert n_new.role == n_orig.role
        assert n_new.output == n_orig.output
        assert n_new.seq == n_orig.seq
        assert n_new.reads == n_orig.reads


def test_from_dict_silently_drops_legacy_predecessor():
    """Old DB dumps had a 'predecessor' field; from_dict must ignore it."""
    raw = {
        "nodes": [
            {"id": "n1", "seq": 0, "role": "user",
             "predecessor": None, "output": "hi"},
            {"id": "n2", "seq": 1, "role": "llm",
             "predecessor": "n1", "output": "yo"},
        ],
    }
    g = Graph.from_dict(raw)
    assert len(g) == 2
    assert g["n1"].output == "hi"
    assert g["n2"].output == "yo"

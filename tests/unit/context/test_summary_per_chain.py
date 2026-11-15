"""Each chain reads its own summary (context/compaction.html, "Which summary").

- Two branches compacted one after the other each keep their own summary;
  switching between them renders each with its own and writes nothing.
- A rolling summary absorbs the one before it on the same chain.
- A rewind into a summary's range falls back to an earlier summary that
  still lies on the chain, or to the raw turns.
- A summary before the fork point serves both branches.
- The graph builder folds each branch with the summary the render picks.
- Two branches that loaded different tools each carry their own.
"""
from __future__ import annotations

import pytest

from openprogram.runtime.context.nodes import active_summary_for_chain, render_context
from openprogram.state.store.session.nodes import Call, Graph


def _graph() -> Graph:
    """u0 a0 u1 a1 | fork after a1: A = u2 a2 u3 a3, B = v2 b2 v3 b3."""
    g = Graph()
    prev = None
    for i in range(2):
        g.add(Call(id=f"u{i}", role="user", output=f"q{i}", predecessor=prev))
        g.add(Call(id=f"a{i}", role="llm", output=f"r{i}", predecessor=f"u{i}"))
        prev = f"a{i}"
    for tag, (u, a) in (("A", ("u", "a")), ("B", ("v", "b"))):
        p = "a1"
        for i in (2, 3):
            g.add(Call(id=f"{u}{i}", role="user", output=f"{tag}q{i}", predecessor=p))
            g.add(Call(id=f"{a}{i}", role="llm", output=f"{tag}r{i}", predecessor=f"{u}{i}"))
            p = f"{a}{i}"
    return g


def _summary(g: Graph, sid: str, covers: list[str]) -> Call:
    return g.add(Call(id=sid, role="llm", name="context/summary", output=f"[{sid}]",
                      predecessor=None, metadata={"covers_ids": covers, "render_role": "user"}))


def _chain(g: Graph, tip: str) -> list[str]:
    from openprogram.runtime.context.nodes import render_spine
    return render_spine(g, tip)


def test_each_branch_reads_its_own_summary():
    g = _graph()
    s1 = _summary(g, "S1", ["u0", "a0", "u1", "a1", "u2", "a2"])
    s2 = _summary(g, "S2", ["u0", "a0", "u1", "a1", "v2", "b2"])
    assert active_summary_for_chain(g, _chain(g, "a3")) is s1
    assert active_summary_for_chain(g, _chain(g, "b3")) is s2
    assert render_context(g, head_id="a3", frame_entry_seq=-1) == ["S1", "u3", "a3"]
    assert render_context(g, head_id="b3", frame_entry_seq=-1) == ["S2", "v3", "b3"]


def test_a_summary_before_the_fork_serves_both_branches():
    g = _graph()
    shared = _summary(g, "S0", ["u0", "a0"])
    assert active_summary_for_chain(g, _chain(g, "a3")) is shared
    assert active_summary_for_chain(g, _chain(g, "b3")) is shared


def test_a_rolling_summary_absorbs_the_one_before_it():
    g = _graph()
    _summary(g, "S1", ["u0", "a0"])
    s2 = _summary(g, "S2", ["u0", "a0", "u1", "a1", "u2", "a2"])
    assert active_summary_for_chain(g, _chain(g, "a3")) is s2


def test_a_rewind_into_a_summary_falls_back_to_the_earlier_one():
    g = _graph()
    s1 = _summary(g, "S1", ["u0", "a0"])
    _summary(g, "S2", ["u0", "a0", "u1", "a1", "u2", "a2"])
    # The rewind retracts u2 a2 u3 a3 in place: S2 no longer lies on the chain.
    retracted = {"u2", "a2", "u3", "a3"}
    assert active_summary_for_chain(g, _chain(g, "a3"), retracted) is s1
    # Back into S1's range: nothing applies, the turns render raw.
    retracted |= {"u0", "a0"}
    assert active_summary_for_chain(g, _chain(g, "a3"), retracted) is None


def test_a_retracted_summary_does_not_exist():
    g = _graph()
    s1 = _summary(g, "S1", ["u0", "a0"])
    _summary(g, "S2", ["u0", "a0", "u1", "a1"])
    assert active_summary_for_chain(g, _chain(g, "a3"), {"S2"}) is s1


@pytest.fixture
def store(tmp_path, monkeypatch):
    from openprogram.state.store.session.session_store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    monkeypatch.setattr("openprogram.state.store.current.current_store", lambda: store)
    store.create_session("c", "main")
    prev = None
    for i in range(2):
        store.append_message("c", {"id": f"u{i}", "role": "user", "content": f"q{i}", "predecessor": prev})
        store.append_message("c", {"id": f"a{i}", "role": "assistant", "content": f"r{i}",
                                   "predecessor": f"u{i}", "status": "done"})
        prev = f"a{i}"
    for u, a in (("u", "a"), ("v", "b")):
        p = "a1"
        for i in (2, 3):
            store.append_message("c", {"id": f"{u}{i}", "role": "user", "content": f"{u}{i}", "predecessor": p})
            store.append_message("c", {"id": f"{a}{i}", "role": "assistant", "content": f"{a}{i}",
                                       "predecessor": f"{u}{i}", "status": "done"})
            p = f"{a}{i}"
    store.set_head("c", "a3")
    yield store
    store.close()


def test_switching_branches_writes_nothing_and_the_builder_agrees(store):
    from openprogram.runtime.context.persistence import Persister, rendered_history
    from openprogram.state.store.session.graph.builder import build_session_graph
    from openprogram.state.store.session.session_node_writer import SessionNodeWriter

    s1 = Persister().insert_summary_node("c", summary_text="A recap", cut_idx=4,
                                         history=rendered_history(store, "c", "a3"),
                                         facts={"render_role": "user"})
    s2 = Persister().insert_summary_node("c", summary_text="B recap", cut_idx=4,
                                         history=rendered_history(store, "c", "b3"),
                                         facts={"render_role": "user"})
    assert s1 and s2
    before = len(store.get_messages("c"))
    graph = SessionNodeWriter(store, "c").load()
    for tip, mine in (("a3", s1), ("b3", s2), ("a3", s1)):
        assert render_context(graph, head_id=tip, frame_entry_seq=-1)[0] == mine
        store.set_head("c", tip)
    assert len(store.get_messages("c")) == before, "a switch writes nothing"
    rows = {row["id"]: row for row in build_session_graph("c", include_layout=False)}
    assert rows[s1].get("covers_ids") and rows[s2].get("covers_ids")
    assert not rows[s1].get("superseded_summary") and not rows[s2].get("superseded_summary")


def test_two_branches_carry_the_tools_each_loaded():
    from openprogram.runtime.context.carried_tools import CARRIED_TOOL_NAMES

    g = _graph()
    a = _summary(g, "S1", ["u0", "a0", "u1", "a1", "u2", "a2"])
    b = _summary(g, "S2", ["u0", "a0", "u1", "a1", "v2", "b2"])
    a.metadata[CARRIED_TOOL_NAMES] = ["scheduler"]
    b.metadata[CARRIED_TOOL_NAMES] = ["browser"]
    on_a = active_summary_for_chain(g, _chain(g, "a3"))
    on_b = active_summary_for_chain(g, _chain(g, "b3"))
    assert on_a.metadata[CARRIED_TOOL_NAMES] == ["scheduler"]
    assert on_b.metadata[CARRIED_TOOL_NAMES] == ["browser"]

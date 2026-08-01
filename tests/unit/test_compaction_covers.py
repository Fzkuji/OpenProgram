"""Compaction summary nodes use ``covers``, not cloned tails.

The old scheme parented the summary at None (an orphan root) and
re-wrote the kept tail as ``k_``-prefixed clones hanging off it. That
duplicated the conversation in storage, broke node identity across a
compaction (a tool_call_id in the clone pointed at a node that was no
longer the one the model had called), and needed a filter at every
render/layout boundary to hide the second copy.

The replacement: the summary is an ordinary ``role=llm`` chain member
whose ``predecessor`` is the predecessor of the first node it replaces,
carrying ``metadata.covers = [first_seq, last_seq]``. Nothing is
cloned; the covered nodes stay exactly where they were and are simply
skipped when a render walks past the summary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.context.nodes import (
    Call,
    Graph,
    ROLE_USER,
    ROLE_LLM,
    ROLE_CODE,
    covers_range,
    render_context,
)
from openprogram.context.persistence import Persister, SUMMARY_NODE_NAME
from openprogram.store.session.session_store import SessionStore


# --- render_context honours covers ---------------------------------


def _chain(g: Graph, n: int) -> list[Call]:
    """n user/llm pairs, each llm following its user.

    ``predecessor`` is the top-level Call field (§3) — the spine walk
    reads only that, so the chain must be linked there.
    """
    out: list[Call] = []
    prev = None
    for i in range(n):
        u = g.add(Call(role=ROLE_USER, output=f"u{i}", predecessor=prev))
        a = g.add(Call(role=ROLE_LLM, output=f"a{i}", predecessor=u.id))
        out += [u, a]
        prev = a.id
    return out


def _summary(g: Graph, after: list[Call], covers, **meta) -> Call:
    """A summary node spliced onto the tip of ``after`` (§8: it is an
    ordinary chain member, so the rest of the branch hangs off it)."""
    return g.add(Call(
        role=ROLE_LLM, name=SUMMARY_NODE_NAME, output="[recap]",
        predecessor=after[-1].id if after else None,
        metadata={"covers": covers, **meta},
    ))


def test_covered_nodes_are_skipped_but_summary_is_kept():
    g = Graph()
    nodes = _chain(g, 4)
    covered = nodes[:4]          # first two turns
    kept = nodes[4:]
    summary = _summary(g, nodes, [covered[0].seq, covered[-1].seq])

    ids = render_context(g, head_id=summary.id, frame_entry_seq=-1)

    assert summary.id in ids
    for n in covered:
        assert n.id not in ids, f"{n.output} should be covered"
    for n in kept:
        assert n.id in ids, f"{n.output} should survive"


def test_summary_does_not_elide_itself():
    """A summary whose own seq falls inside its covers range (possible
    when it is written after the fact) must still render — otherwise the
    compaction silently drops all of the history AND the recap."""
    g = Graph()
    a = g.add(Call(role=ROLE_USER, output="a"))
    b = g.add(Call(role=ROLE_LLM, output="b", predecessor=a.id))
    summary = _summary(g, [a, b], [a.seq, 999])
    ids = render_context(g, head_id=summary.id, frame_entry_seq=-1)
    assert ids == [summary.id]


def test_covers_range_ignores_malformed_metadata():
    g = Graph()
    for bad in (None, "nope", [1], [1, 2, 3], ["a", "b"]):
        n = Call(role=ROLE_LLM, output="x", metadata={"covers": bad})
        assert covers_range(n) is None, bad
    good = Call(role=ROLE_LLM, output="x", metadata={"covers": [2, 5]})
    assert covers_range(good) == (2, 5)


def test_a_malformed_covers_elides_nothing():
    g = Graph()
    nodes = _chain(g, 2)
    summary = _summary(g, nodes, "garbage")
    ids = render_context(g, head_id=summary.id, frame_entry_seq=-1)
    for n in nodes:
        assert n.id in ids


# --- the persister writes a real chain member ----------------------


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionStore:
    st = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: st)
    return st


def _seed(store: SessionStore, sid: str, n: int) -> list[dict]:
    store.create_session(sid, "main", title="t")
    prev = None
    for i in range(n):
        uid, aid = f"u{i}", f"a{i}"
        store.append_message(sid, {"id": uid, "role": "user",
                                   "content": f"u{i}", "predecessor": prev})
        store.append_message(sid, {"id": aid, "role": "assistant",
                                   "content": f"a{i}", "predecessor": uid})
        prev = aid
    return store.get_messages(sid)


def test_persister_splices_summary_into_the_chain(store: SessionStore):
    msgs = _seed(store, "s1", 4)
    covered = msgs[:4]

    sid = Persister().insert_summary_node(
        "s1", summary_text="the recap", cut_idx=4, history=msgs,
    )
    assert sid

    from openprogram.store.session.graphstore_shim import GraphStoreShim
    graph = GraphStoreShim(store, "s1").load()
    seq_of = {n.id: n.seq for n in graph.nodes.values()}

    node = {m["id"]: m for m in store.get_messages("s1")}[sid]
    # It is a normal llm chain member, not an orphan root and not a system row.
    assert node["role"] == "assistant"
    assert node["predecessor"] == (covered[0].get("predecessor") or "")
    assert node["covers"] == [seq_of[covered[0]["id"]],
                              seq_of[covered[-1]["id"]]]
    assert "the recap" in node["content"]
    # And the range really does name the covered nodes, nobody else.
    lo, hi = node["covers"]
    inside = {n.id for n in graph.nodes.values()
              if lo <= n.seq <= hi and n.id != sid}
    assert inside == {m["id"] for m in covered}


def test_persister_clones_nothing(store: SessionStore):
    msgs = _seed(store, "s2", 4)
    before = {m["id"] for m in msgs}

    Persister().insert_summary_node(
        "s2", summary_text="recap", cut_idx=4, history=msgs,
    )

    after = store.get_messages("s2")
    added = {m["id"] for m in after} - before
    # Exactly one new node: the summary. No k_ tail clones.
    assert len(added) == 1
    assert not [m for m in after if m["id"].startswith("k_")]
    # And every original node is still present, unmodified in identity.
    assert before <= {m["id"] for m in after}


def test_original_tail_keeps_its_ids_and_predecessors(store: SessionStore):
    msgs = _seed(store, "s3", 4)
    tail_before = {m["id"]: m.get("predecessor") for m in msgs[4:]}

    Persister().insert_summary_node(
        "s3", summary_text="recap", cut_idx=4, history=msgs,
    )

    after = {m["id"]: m.get("predecessor") for m in store.get_messages("s3")}
    for nid, pred in tail_before.items():
        assert nid in after, "the kept tail must not be re-identified"
        assert after[nid] == pred, "the kept tail must not be re-parented"


def test_rollback_the_pre_compaction_view_is_still_reachable(store: SessionStore):
    """Ignoring covers reconstructs exactly the original conversation —
    that is what makes 'did the summary capture what I said' answerable."""
    msgs = _seed(store, "s4", 4)
    original = [m["id"] for m in msgs]

    Persister().insert_summary_node(
        "s4", summary_text="recap", cut_idx=4, history=msgs,
    )

    survivors = [m["id"] for m in store.get_messages("s4")
                 if not m["id"].startswith("summary_")]
    assert survivors == original


def test_persister_refuses_degenerate_cuts(store: SessionStore):
    msgs = _seed(store, "s5", 3)
    p = Persister()
    assert p.insert_summary_node("s5", summary_text="", cut_idx=2,
                                 history=msgs) is None
    assert p.insert_summary_node("s5", summary_text="x", cut_idx=0,
                                 history=msgs) is None
    assert p.insert_summary_node("s5", summary_text="x", cut_idx=len(msgs),
                                 history=msgs) is None

"""Compaction summary nodes: stand-in + segment substitution.

The summary is an ordinary ``role=llm`` node whose ``predecessor`` is
the predecessor of the first node it replaces, carrying
``metadata.covers_ids`` — the exact chain nodes it stands in for
(context/compaction.md §2). Nothing is cloned, no edges are rewritten,
and HEAD does not move (the append rule ignores mid-chain splices).

``render_context`` applies the §3 rule: if the active summary's covered
segment lies fully on the rendered chain, the segment (plus its caller
subtrees) is dropped and the summary is admitted at the segment's
position. A chain that does not contain the whole segment renders raw.
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
    active_summary,
    render_context,
    summary_covers_ids,
)
from openprogram.context.persistence import (
    Persister,
    SUMMARY_NODE_NAME,
    covered_chain_ids,
)
from openprogram.store.session.session_store import SessionStore


# --- render_context segment substitution ----------------------------


def _chain(g: Graph, n: int) -> list[Call]:
    """n user/llm pairs, each llm following its user, linked via the
    top-level ``predecessor`` field (§3)."""
    out: list[Call] = []
    prev = None
    for i in range(n):
        u = g.add(Call(role=ROLE_USER, output=f"u{i}", predecessor=prev))
        a = g.add(Call(role=ROLE_LLM, output=f"a{i}", predecessor=u.id))
        out += [u, a]
        prev = a.id
    return out


def _summary(g: Graph, covered: list[Call]) -> Call:
    """A summary standing in for ``covered`` — spliced at the covered
    range's own start (its predecessor), like the persister writes it."""
    return g.add(Call(
        role=ROLE_LLM, name=SUMMARY_NODE_NAME, output="[recap]",
        predecessor=covered[0].predecessor,
        metadata={"covers_ids": [n.id for n in covered]},
    ))


def test_segment_substitution_replaces_covered_with_summary():
    g = Graph()
    nodes = _chain(g, 4)
    covered, kept = nodes[:4], nodes[4:]
    summary = _summary(g, covered)

    ids = render_context(g, head_id=nodes[-1].id, frame_entry_seq=-1)

    for n in covered:
        assert n.id not in ids, f"{n.output} should be covered"
    for n in kept:
        assert n.id in ids, f"{n.output} should survive"
    # The summary sits where the segment began — before the kept tail.
    assert ids.index(summary.id) < ids.index(kept[0].id)


def test_a_chain_without_the_full_segment_renders_raw():
    """A fork from inside the covered range was never compacted — it
    must see its own raw history and no summary."""
    g = Graph()
    nodes = _chain(g, 3)
    summary = _summary(g, nodes[:4])
    # Fork off the first reply: a sibling of nodes[2].
    fu = g.add(Call(role=ROLE_USER, output="alt", predecessor=nodes[1].id))
    fa = g.add(Call(role=ROLE_LLM, output="alt-r", predecessor=fu.id))

    ids = render_context(g, head_id=fa.id, frame_entry_seq=-1)

    assert summary.id not in ids
    assert nodes[0].id in ids and nodes[1].id in ids
    assert fu.id in ids and fa.id in ids


def test_covered_turns_fold_with_their_caller_subtrees():
    g = Graph()
    u = g.add(Call(role=ROLE_USER, output="u0"))
    a = g.add(Call(role=ROLE_LLM, output="a0", predecessor=u.id))
    tool = g.add(Call(role=ROLE_CODE, name="f", output="ran", caller=a.id))
    u1 = g.add(Call(role=ROLE_USER, output="u1", predecessor=a.id))
    a1 = g.add(Call(role=ROLE_LLM, output="a1", predecessor=u1.id))
    summary = _summary(g, [u, a])

    ids = render_context(g, head_id=a1.id, frame_entry_seq=-1)

    assert tool.id not in ids, "a covered turn's calls fold with it"
    assert summary.id in ids
    assert u1.id in ids and a1.id in ids


def test_only_the_newest_summary_substitutes():
    """Rolling policy: older summaries are relics and elide nothing."""
    g = Graph()
    nodes = _chain(g, 4)
    old = _summary(g, nodes[:2])
    new = _summary(g, nodes[:4])

    assert active_summary(g).id == new.id
    ids = render_context(g, head_id=nodes[-1].id, frame_entry_seq=-1)

    assert new.id in ids
    assert old.id not in ids
    for n in nodes[:4]:
        assert n.id not in ids
    for n in nodes[4:]:
        assert n.id in ids


def test_summary_covers_ids_ignores_malformed_metadata():
    for bad in (None, "nope", [], 7):
        n = Call(role=ROLE_LLM, output="x", metadata={"covers_ids": bad})
        assert summary_covers_ids(n) is None, bad
    good = Call(role=ROLE_LLM, output="x", metadata={"covers_ids": ["a", "b"]})
    assert summary_covers_ids(good) == ["a", "b"]


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


def test_persister_splices_summary_at_the_segment_start(store: SessionStore):
    msgs = _seed(store, "s1", 4)
    covered = msgs[:4]

    sid = Persister().insert_summary_node(
        "s1", summary_text="the recap", cut_idx=4, history=msgs,
    )
    assert sid

    node = {m["id"]: m for m in store.get_messages("s1")}[sid]
    # It is a normal llm chain member, not an orphan root and not a system row.
    assert node["role"] == "assistant"
    assert node["predecessor"] == (covered[0].get("predecessor") or "")
    assert "the recap" in node["content"]

    from openprogram.store.session.session_node_writer import SessionNodeWriter
    graph = SessionNodeWriter(store, "s1").load()
    assert graph.nodes[sid].metadata.get("covers_ids") == \
        [m["id"] for m in covered]


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
    """Ignoring the summary reconstructs exactly the original
    conversation — what makes 'did the summary capture what I said'
    answerable."""
    msgs = _seed(store, "s4", 4)
    original = [m["id"] for m in msgs]

    Persister().insert_summary_node(
        "s4", summary_text="recap", cut_idx=4, history=msgs,
    )

    survivors = [m["id"] for m in store.get_messages("s4")
                 if not m["id"].startswith("summary_")]
    assert survivors == original


def test_compaction_never_moves_head(store: SessionStore):
    """The summary is a mid-chain splice; the append rule only advances
    head on chain extension, so head stays wherever it was — on the
    branch tip, or on a covered node the user checked out."""
    msgs = _seed(store, "s6", 4)
    tip = msgs[-1]["id"]
    assert store.get_session("s6")["head_id"] == tip

    Persister().insert_summary_node(
        "s6", summary_text="recap", cut_idx=4, history=msgs,
    )
    assert store.get_session("s6")["head_id"] == tip
    assert [m["id"] for m in store.get_branch("s6")] == \
        [m["id"] for m in msgs]

    covered_tip = msgs[3]["id"]
    store.set_head("s6", covered_tip)
    Persister().insert_summary_node(
        "s6", summary_text="recap 2", cut_idx=2, history=msgs,
    )
    assert store.get_session("s6")["head_id"] == covered_tip


def test_recompaction_extends_the_covered_segment(store: SessionStore):
    """§4: when the covered slice starts with the previous summary, the
    new covers_ids is the old segment extended by the newly eaten turns
    — coverage always names real turns, never another summary."""
    msgs = _seed(store, "s8", 4)
    first = Persister().insert_summary_node(
        "s8", summary_text="recap 1", cut_idx=4, history=msgs,
    )
    assert first
    # The rendered view after compaction #1: [summary, kept tail].
    by_id = {m["id"]: m for m in store.get_messages("s8")}
    rendered = [by_id[first]] + msgs[4:]

    second = Persister().insert_summary_node(
        "s8", summary_text="recap 2", cut_idx=3, history=rendered,
    )
    assert second

    from openprogram.store.session.session_node_writer import SessionNodeWriter
    graph = SessionNodeWriter(store, "s8").load()
    covers = graph.nodes[second].metadata.get("covers_ids")
    assert covers == [m["id"] for m in msgs[:6]]
    assert first not in covers


def test_covered_chain_ids_expands_summaries():
    covered = [
        {"id": "sum_old", "extra": {"covers_ids": ["u0", "a0"]}},
        {"id": "u1"},
        {"id": "a1"},
    ]
    assert covered_chain_ids(covered) == ["u0", "a0", "u1", "a1"]


def test_persister_refuses_degenerate_cuts(store: SessionStore):
    msgs = _seed(store, "s5", 3)
    p = Persister()
    assert p.insert_summary_node("s5", summary_text="", cut_idx=2,
                                 history=msgs) is None
    assert p.insert_summary_node("s5", summary_text="x", cut_idx=0,
                                 history=msgs) is None
    assert p.insert_summary_node("s5", summary_text="x", cut_idx=len(msgs),
                                 history=msgs) is None

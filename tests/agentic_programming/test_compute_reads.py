"""context.nodes.compute_reads — pure-function helper that picks the
ids that should go into the next LLM call's ``reads``.

Tests build small graphs manually and assert the algorithm's behavior
under various frame / expose / render_range configurations.
"""

from __future__ import annotations

import pytest

from openprogram.context.nodes import (
    Call,
    Graph,
    ROLE_USER,
    ROLE_LLM,
    ROLE_CODE,
    compute_reads,
)


def _user(g: Graph, content: str) -> Call:
    return g.add(Call(role=ROLE_USER, output=content))


def _llm(g: Graph, output: str, *, called_by: str = "") -> Call:
    return g.add(Call(role=ROLE_LLM, output=output, called_by=called_by))


def _code(g: Graph, name: str, *, expose: str = "io",
          called_by: str = "") -> Call:
    return g.add(Call(role=ROLE_CODE, name=name, called_by=called_by,
                       metadata={"expose": expose}))


# ── No frame: top-level chat returns the linear chain ──────────────


def test_top_level_returns_full_chain_in_seq_order():
    g = Graph()
    u = _user(g, "q1")
    m = _llm(g, "a1")
    u2 = _user(g, "q2")
    assert compute_reads(g) == [u.id, m.id, u2.id]


def test_head_seq_caps_the_chain():
    g = Graph()
    u = _user(g, "q1")
    m = _llm(g, "a1")
    u2 = _user(g, "q2")
    # Slice at m: u2 excluded.
    assert compute_reads(g, head_seq=m.seq) == [u.id, m.id]


def test_empty_graph_returns_empty_list():
    g = Graph()
    assert compute_reads(g) == []


# ── Inside a frame: pre + in_frame both visible by default ─────────


def test_in_frame_includes_pre_and_in_frame():
    g = Graph()
    u = _user(g, "q")
    m = _llm(g, "a")
    # imagine a function entered here: entry_seq is the seq of `m`
    entry = m.seq
    in1 = _llm(g, "step1")
    in2 = _llm(g, "step2")
    reads = compute_reads(g, frame_entry_seq=entry)
    assert reads == [u.id, m.id, in1.id, in2.id]


def test_depth_zero_isolates_in_frame():
    g = Graph()
    _user(g, "q")
    _llm(g, "a")
    entry = g.last().seq
    s1 = _llm(g, "s1")
    reads = compute_reads(g, frame_entry_seq=entry,
                          render_range={"depth": 0})
    assert reads == [s1.id]


def test_depth_keeps_recent_pre_frame_only():
    g = Graph()
    a = _user(g, "1")
    b = _llm(g, "2")
    c = _user(g, "3")
    d = _llm(g, "4")
    entry = d.seq
    s = _llm(g, "step")
    # depth=2 → keep most recent 2 pre-frame (c, d) + all in-frame (s)
    reads = compute_reads(g, frame_entry_seq=entry,
                          render_range={"depth": 2})
    assert reads == [c.id, d.id, s.id]


def test_siblings_cap_keeps_recent_in_frame_nodes_only():
    g = Graph()
    u = _user(g, "q")
    entry = u.seq
    s1 = _llm(g, "1")
    s2 = _llm(g, "2")
    s3 = _llm(g, "3")
    reads = compute_reads(g, frame_entry_seq=entry,
                          render_range={"siblings": 2})
    assert u.id in reads
    assert s3.id in reads
    assert s2.id in reads
    assert s1.id not in reads


# ── Expose filtering on code Calls ─────────────────────────────────


def test_io_function_hides_internal_llm():
    """code Call with expose='io' suppresses llm Calls that point at
    it via ``called_by``."""
    g = Graph()
    u = _user(g, "q")
    fn = _code(g, "agent", expose="io")
    internal = _llm(g, "internal", called_by=fn.id)
    final = _llm(g, "after")
    reads = compute_reads(g)
    assert u.id in reads
    assert fn.id in reads             # the summary visible
    assert internal.id not in reads   # internal hidden
    assert final.id in reads


def test_full_function_keeps_internal_llm():
    g = Graph()
    u = _user(g, "q")
    fn = _code(g, "agent", expose="full")
    internal = _llm(g, "internal", called_by=fn.id)
    reads = compute_reads(g)
    assert u.id in reads
    assert fn.id in reads
    assert internal.id in reads       # transparent


def test_io_only_suppresses_its_own_internals():
    """Internal llm calls of one function don't get suppressed by a
    sibling function's expose=io."""
    g = Graph()
    a = _code(g, "a", expose="io")
    a_llm = _llm(g, "a-internal", called_by=a.id)
    b = _code(g, "b", expose="full")
    b_llm = _llm(g, "b-internal", called_by=b.id)
    reads = compute_reads(g)
    assert a.id in reads
    assert a_llm.id not in reads       # a is io → hide a's internals
    assert b.id in reads
    assert b_llm.id in reads           # b is full → keep b's internals


# ── head_seq + frame combo ──────────────────────────────────────────


def test_head_seq_limits_chain_inside_frame_too():
    g = Graph()
    u = _user(g, "q")
    entry = u.seq
    s1 = _llm(g, "1")
    s2 = _llm(g, "2")
    s3 = _llm(g, "3")
    reads = compute_reads(g, head_seq=s2.seq, frame_entry_seq=entry)
    assert reads == [u.id, s1.id, s2.id]
    assert s3.id not in reads

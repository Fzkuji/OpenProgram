"""§6 path-native membership — dag/overview.md.

    A node is rendered iff its nearest ROOT-level ancestor (walking
    ``caller`` upward) lies on ``head_id``'s predecessor chain, and the
    frame/expose rules admit it.

These tests pin the *membership* half of that rule: which nodes the walk
admits, and which it must never reach. The frame/expose half is pinned by
tests/unit/programs/test_render_context.py.
"""

from __future__ import annotations

from openprogram.context.nodes import (
    Call,
    Graph,
    ROLE_USER,
    ROLE_LLM,
    ROLE_CODE,
    render_context,
    render_path,
    render_spine,
)


def _add(g: Graph, role: str, *, pred=None, caller: str = "",
         output: str = "", seq: int = -1, **meta) -> Call:
    return g.add(Call(role=role, output=output, predecessor=pred,
                      caller=caller, seq=seq, metadata=meta))


# (a) Two branches: the head's branch renders, the sibling never does


def test_sibling_branch_is_unreachable():
    """user1 → llm1 forks into two user turns. Rendering from the A tip
    must not admit a single B node — including B's llm sub-calls, which
    hang off B by ``caller`` and were previously re-admitted by the
    engine's caller-based patch."""
    g = Graph()
    u1 = _add(g, ROLE_USER, pred="ROOT", output="q1")
    l1 = _add(g, ROLE_LLM, pred=u1.id, output="a1")

    # Branch A
    ua = _add(g, ROLE_USER, pred=l1.id, output="qA")
    la = _add(g, ROLE_LLM, pred=ua.id, output="aA")
    la_tool = _add(g, ROLE_CODE, caller=la.id, output="toolA")

    # Branch B — same predecessor as A, so it is a fork of the same slot
    ub = _add(g, ROLE_USER, pred=l1.id, output="qB")
    lb = _add(g, ROLE_LLM, pred=ub.id, output="aB")
    lb_tool = _add(g, ROLE_CODE, caller=lb.id, output="toolB")
    lb_inner = _add(g, ROLE_LLM, caller=lb_tool.id, output="innerB")

    reads = render_context(g, head_id=la.id)

    assert reads == [u1.id, l1.id, ua.id, la.id, la_tool.id]
    for nid in (ub.id, lb.id, lb_tool.id, lb_inner.id):
        assert nid not in reads

    # ...and symmetrically from B's tip.
    reads_b = render_context(g, head_id=lb.id)
    assert ua.id not in reads_b and la.id not in reads_b


# (b) A placeholder sibling is off the chain, so it is simply not reached


def test_placeholder_sibling_not_rendered():
    """A retry leaves the abandoned assistant placeholder as a fork
    sibling. It used to be excluded by walking back from the head; now
    it is never on the spine to begin with."""
    g = Graph()
    u1 = _add(g, ROLE_USER, pred="ROOT", output="q")
    placeholder = _add(g, ROLE_LLM, pred=u1.id, output="", status="running")
    real = _add(g, ROLE_LLM, pred=u1.id, output="the real answer")

    reads = render_context(g, head_id=real.id)
    assert reads == [u1.id, real.id]
    assert placeholder.id not in reads


# (c) A spawn branch stops at its root


def test_spawn_branch_stops_at_spawn_root():
    """A spawn root has ``predecessor=None`` and a ``caller`` pointing at
    the spawning node. The spine must stop there and not follow
    ``caller`` into the parent branch (§4: spawn branches have clean
    context)."""
    g = Graph()
    u1 = _add(g, ROLE_USER, pred="ROOT", output="parent q")
    l1 = _add(g, ROLE_LLM, pred=u1.id, output="parent a")
    spawner = _add(g, ROLE_CODE, caller=l1.id, output="spawn_job")

    root = _add(g, ROLE_USER, pred=None, caller=spawner.id,
                output="spawned prompt", spawn_branch_root=True)
    sl = _add(g, ROLE_LLM, pred=root.id, output="spawned reply")

    assert render_spine(g, sl.id) == [root.id, sl.id]

    reads = render_context(g, head_id=sl.id)
    assert reads == [root.id, sl.id]
    for nid in (u1.id, l1.id, spawner.id):
        assert nid not in reads


# (c2) The parent branch never renders a spawn branch's internals


def test_spawn_branch_hidden_from_parent_context():
    """The reverse direction of (c): rendering from the PARENT's head
    must not descend into a spawn branch via the caller edge. A Goal
    working agent must not read the judge's spawned instructions and
    verdict from its own DAG history."""
    g = Graph()
    u1 = _add(g, ROLE_USER, pred="ROOT", output="parent q")
    l1 = _add(g, ROLE_LLM, pred=u1.id, output="parent a")
    spawner = _add(g, ROLE_CODE, caller=l1.id, output="goal")

    root = _add(g, ROLE_USER, pred=None, caller=spawner.id,
                output="judge instructions", spawn_branch_root=True)
    sl = _add(g, ROLE_LLM, pred=root.id, output="judge verdict")
    stool = _add(g, ROLE_CODE, caller=sl.id, output="judge tool")

    reads = render_context(g, head_id=l1.id)
    assert spawner.id in reads
    for nid in (root.id, sl.id, stool.id):
        assert nid not in reads


# (d) Purity — the read path touches nothing


def test_render_is_pure():
    g = Graph()
    u1 = _add(g, ROLE_USER, pred="ROOT", output="q")
    l1 = _add(g, ROLE_LLM, pred=u1.id, output="a")
    _add(g, ROLE_CODE, caller=l1.id, output="tool")
    _add(g, ROLE_USER, pred=l1.id, output="fork")

    before = g.to_json()
    render_context(g, head_id=l1.id)
    render_path(g, l1.id)
    render_spine(g, l1.id)
    assert g.to_json() == before


# (e) seq orders the output regardless of insertion order


def test_output_is_seq_ordered_not_insertion_ordered():
    """Nodes loaded from storage can arrive out of order. Membership is
    a walk; ordering is seq, and only seq."""
    g = Graph()
    u1 = _add(g, ROLE_USER, pred="ROOT", output="q", seq=10)
    l1 = _add(g, ROLE_LLM, pred=u1.id, output="a", seq=30)
    # inserted last, but belongs in the middle by seq
    tool = _add(g, ROLE_CODE, caller=l1.id, output="tool", seq=20)

    assert render_context(g, head_id=l1.id) == [u1.id, tool.id, l1.id]


def test_root_subtree_is_not_expanded():
    """Every top-level node carries ``caller="ROOT"`` (§3), so expanding
    ROOT's caller-subtree would re-admit the entire session and dissolve
    branch isolation. A ROOT-level node is its own nearest ROOT-level
    ancestor; ROOT is never one."""
    g = Graph()
    root = _add(g, ROLE_USER, output="", display="root")
    u1 = _add(g, ROLE_USER, pred=root.id, caller=root.id, output="hello")
    l1 = _add(g, ROLE_LLM, pred=u1.id, output="reply 1")
    u2 = _add(g, ROLE_USER, pred=l1.id, caller=root.id, output="later turn")

    reads = render_context(g, head_id=l1.id)
    assert u2.id not in reads
    assert u1.id in reads and l1.id in reads


def test_unknown_head_renders_nothing():
    g = Graph()
    _add(g, ROLE_USER, pred="ROOT", output="q")
    assert render_context(g, head_id="nope") == []

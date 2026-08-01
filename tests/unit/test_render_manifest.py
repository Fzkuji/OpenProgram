"""Render manifests + the ratcheted aging boundary.

Two invariants, both about the prompt being stable rather than merely
correct-on-average:

**Ratchet.** The aging boundary moves only when a turn commits. Two
renders inside one turn must produce identical bytes — otherwise the
second LLM call of a turn sees a rewritten middle of its own history,
which destroys the KV cache prefix and makes the model's view of the
conversation shift under it mid-reasoning.

**Replay.** Every call records what the policy did to build its prompt.
Re-rendering with that manifest reproduces the same bytes even after
the global policy constants change, so an old turn can be audited or
re-run against the context it actually had.
"""

from __future__ import annotations

import pytest

from openprogram.context import aging
from openprogram.context.nodes import (
    Call,
    Graph,
    ROLE_USER,
    ROLE_LLM,
    ROLE_CODE,
    render_context,
)
from openprogram.context.render import render_dag_messages
from openprogram.context.tool_aging import policy


@pytest.fixture(autouse=True)
def _clean_boundary_cache():
    aging.reset_boundary_cache()
    yield
    aging.reset_boundary_cache()


def _session(n_turns: int) -> Graph:
    """n turns of user → llm → tool, long enough to push past the tail.

    ``predecessor`` is the top-level Call field (§3): the spine walk
    reads only that, so the conversation must be chained through it.
    """
    g = Graph()
    prev = None
    for i in range(n_turns):
        u = g.add(Call(role=ROLE_USER, output=f"question {i}",
                       predecessor=prev))
        llm = g.add(Call(role=ROLE_LLM, output=f"answer {i}",
                         predecessor=u.id))
        g.add(Call(role=ROLE_CODE, name="grep", caller=llm.id,
                   input={"pattern": f"p{i}"},
                   output=f"a long tool result for turn {i}\n" * 5,
                   metadata={"expose": "full",
                             "tool_call_id": f"tc{i}"}))
        prev = llm.id
    return g


def _tip(g: Graph) -> str:
    """The branch tip: newest ROOT-level conversational node."""
    return max((n for n in g if not n.caller), key=lambda n: n.seq).id


def _render(g: Graph, manifest=None) -> str:
    ids = render_context(g, head_id=_tip(g), frame_entry_seq=-1)
    msgs = render_dag_messages(g, ids, None, manifest)
    return "\n".join(repr(m) for m in msgs)


# --- ratchet -------------------------------------------------------


def test_two_renders_in_one_turn_are_byte_identical(monkeypatch):
    """The whole point of the ratchet: nothing shifts mid-turn."""
    from openprogram.store import _current_turn_id

    g = _session(6)
    token = _current_turn_id.set("turn-A")
    try:
        first = _render(g)
        # A tool call lands mid-turn — this is exactly the event that
        # used to slide the rolling window forward. It extends the SAME
        # branch, so it is chained onto the current tip.
        llm = g.add(Call(role=ROLE_LLM, output="thinking more",
                         predecessor=_tip(g)))
        g.add(Call(role=ROLE_CODE, name="grep", caller=llm.id,
                   input={"pattern": "new"}, output="fresh result",
                   metadata={"expose": "full", "tool_call_id": "tcN"}))
        second_prefix = _render(g)[:len(first)]
    finally:
        _current_turn_id.reset(token)

    assert second_prefix == first, (
        "the already-rendered prefix must not be rewritten mid-turn"
    )


def test_boundary_is_memoised_per_turn(monkeypatch):
    from openprogram.store import _current_turn_id

    g = _session(6)
    nodes = list(g)
    token = _current_turn_id.set("turn-B")
    try:
        first = aging.aged_before_seq(nodes)
        # More llm nodes arrive; a rolling window would advance here.
        g.add(Call(role=ROLE_LLM, output="another"))
        g.add(Call(role=ROLE_LLM, output="and another"))
        second = aging.aged_before_seq(list(g))
    finally:
        _current_turn_id.reset(token)
    assert first == second


def test_boundary_advances_on_the_next_turn():
    from openprogram.store import _current_turn_id

    g = _session(6)
    t1 = _current_turn_id.set("turn-C1")
    try:
        first = aging.aged_before_seq(list(g))
    finally:
        _current_turn_id.reset(t1)

    g.add(Call(role=ROLE_LLM, output="next turn reply"))
    g.add(Call(role=ROLE_LLM, output="and more"))

    t2 = _current_turn_id.set("turn-C2")
    try:
        second = aging.aged_before_seq(list(g))
    finally:
        _current_turn_id.reset(t2)

    assert second > first, "a committed turn must move the boundary"


def test_short_conversation_ages_nothing():
    g = _session(2)   # fewer llm nodes than TAIL_TURNS
    assert aging.aged_before_seq(list(g)) == -1


# --- manifest replay -----------------------------------------------


def test_manifest_replays_byte_identically_after_policy_change(monkeypatch):
    """Record a render, move the global policy, replay with the manifest."""
    g = _session(8)

    original = _render(g)
    manifest = aging.last_manifest()
    assert manifest is not None
    assert manifest["policy_version"] == aging.POLICY_VERSION
    recorded_boundary = manifest["aged_before_seq"]
    assert recorded_boundary >= 0, "this fixture must actually age something"

    # Now the operator retunes aging globally.
    monkeypatch.setattr(policy, "TAIL_TURNS", 7)
    aging.reset_boundary_cache()

    drifted = _render(g)
    assert drifted != original, (
        "sanity: the policy change must actually change the live render, "
        "otherwise this test proves nothing"
    )

    replayed = _render(g, manifest=manifest)
    assert replayed == original


def test_manifest_records_the_boundary_actually_used():
    g = _session(8)
    _render(g)
    mf = aging.last_manifest()
    assert mf["aged_before_seq"] == aging.aged_before_seq(list(g))


def test_replay_does_not_overwrite_the_stored_manifest():
    """Replaying an old turn must not clobber the current turn's record."""
    g = _session(8)
    _render(g)
    live = aging.last_manifest()

    stale = dict(live, aged_before_seq=0)
    _render(g, manifest=stale)

    assert aging.last_manifest() == live


def test_manifest_with_a_junk_boundary_falls_back_to_live_policy():
    g = _session(8)
    live = _render(g)
    for junk in ({}, {"aged_before_seq": None}, {"aged_before_seq": "x"},
                 {"aged_before_seq": True}):
        assert _render(g, manifest=junk) == live


# --- ablation switches ---------------------------------------------


def test_aging_can_be_switched_off(monkeypatch):
    g = _session(8)
    aged = _render(g)
    monkeypatch.setattr(policy, "AGING_ENABLED", False)
    aging.reset_boundary_cache()
    assert _render(g) != aged, "OPENPROGRAM_TOOL_AGING=off must stop aging"


def test_tail_turns_override_changes_the_boundary(monkeypatch):
    g = _session(8)
    nodes = list(g)
    monkeypatch.setattr(policy, "TAIL_TURNS", 2)
    aging.reset_boundary_cache()
    tight = aging.aged_before_seq(nodes)
    monkeypatch.setattr(policy, "TAIL_TURNS", 6)
    aging.reset_boundary_cache()
    loose = aging.aged_before_seq(nodes)
    assert tight > loose, "a bigger tail window must age less"

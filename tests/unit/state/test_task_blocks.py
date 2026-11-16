"""Which task and step each block of a reply belongs to (state/task_blocks.py)."""
from __future__ import annotations

from openprogram.state import task_blocks
from openprogram.state.task_blocks import effects_of, owner_after, stamp


def _tool(name, call_id, **more):
    return {"type": "tool", "tool": name, "tool_call_id": call_id, **more}


def _text(text="t"):
    return {"type": "text", "text": text}


def _reply():
    """The stored example of the contract, before any stamp."""
    return [
        _text("I'll look first, then add the fold."),
        _tool("task_start", "c1"),
        _tool("grep", "c2"),
        _text("The list is in agent-list.tsx."),
        _tool("task_step", "c3"),
        _tool("edit", "c4"),
        _tool("task_step", "c9"),
        _text("Finished agents now fold into one row."),
    ]


EFFECTS = {"c1": {"task": "T3", "step": "14"}, "c3": {"task": "T3", "step": "15"}, "c9": {"task": None}}


def _owners(blocks):
    return [(b.get("task"), b.get("step")) for b in blocks]


def test_a_task_call_is_the_boundary_and_carries_the_new_state():
    blocks = _reply()
    assert stamp(blocks, EFFECTS) == 1
    assert _owners(blocks) == [
        (None, None), ("T3", "14"), ("T3", "14"), ("T3", "14"),
        ("T3", "15"), ("T3", "15"), (None, None), (None, None),
    ]
    assert blocks[1]["task_effect"] == {"task": "T3", "step": "14"}
    assert blocks[6]["task_effect"] == {"task": None}
    # Unowned blocks carry neither key.
    assert "task" not in blocks[0] and "step" not in blocks[7] and "task_effect" not in blocks[2]
    # A second pass changes nothing, and the blocks alone give the effects back.
    assert stamp(blocks, effects_of(blocks)) is None
    assert effects_of(blocks) == EFFECTS


def test_a_task_without_steps_stamps_the_task_alone():
    blocks = [_tool("task_start", "c1"), _tool("bash", "c2"), {"type": "thinking", "text": "hm"}]
    stamp(blocks, {"c1": {"task": "T1"}})
    assert blocks[1]["task"] == "T1" and "step" not in blocks[1]
    assert blocks[2]["task"] == "T1"


def test_a_call_without_an_effect_leaves_ownership_as_it_was():
    blocks = [_tool("task_start", "c1"), _tool("task_step", "c2"), _tool("edit", "c3")]
    stamp(blocks, {"c1": {"task": "T1", "step": "4"}})
    assert _owners(blocks) == [("T1", "4")] * 3
    assert "task_effect" not in blocks[1]


def test_ownership_follows_position_not_the_order_results_arrived():
    # Two calls of one response: the edit sits after the step call, so it is step 2's.
    blocks = [_tool("task_start", "c1"), _tool("task_step", "c2"), _tool("edit", "c3")]
    stamp(blocks, {"c1": {"task": "T1", "step": "1"}})
    assert _owners(blocks)[2] == ("T1", "1")
    assert stamp(blocks, {"c1": {"task": "T1", "step": "1"}, "c2": {"task": "T1", "step": "2"}}) == 1
    assert _owners(blocks) == [("T1", "1"), ("T1", "2"), ("T1", "2")]
    # A call placed before the step call stays with the earlier step.
    late = [_tool("task_start", "c1"), _tool("edit", "c3"), _tool("task_step", "c2")]
    stamp(late, {"c1": {"task": "T1", "step": "1"}, "c2": {"task": "T1", "step": "2"}})
    assert _owners(late) == [("T1", "1"), ("T1", "1"), ("T1", "2")]


def test_a_goal_turn_starts_inside_the_open_task():
    blocks = [_text(), _tool("read", "c1"), _tool("task_step", "c2"), _tool("edit", "c3")]
    initial = {"task": "T2", "step": "20"}
    stamp(blocks, {"c2": {"task": "T2", "step": "21"}}, initial)
    assert _owners(blocks) == [("T2", "20"), ("T2", "20"), ("T2", "21"), ("T2", "21")]
    assert owner_after(blocks, initial) == {"task": "T2", "step": "21"}
    assert owner_after([], initial) == initial
    assert owner_after([], None) == {}


def test_a_steering_message_is_never_stamped_and_does_not_end_the_task():
    blocks = [_tool("task_start", "c1"), {"type": "steering", "message_id": "m9", "text": "also x"},
              _tool("edit", "c2")]
    stamp(blocks, {"c1": {"task": "T1", "step": "1"}})
    assert "task" not in blocks[1] and blocks[2]["task"] == "T1"


def test_a_resumed_reply_replays_its_stored_blocks():
    blocks = _reply()
    stamp(blocks, EFFECTS)
    stored = [dict(b) for b in blocks[:6]]
    # The next block of the resumed reply belongs to step 15.
    assert owner_after(stored) == {"task": "T3", "step": "15"}
    assert owner_after(blocks) == {}


def test_a_restarted_response_takes_its_effect_with_it():
    blocks = _reply()
    stamp(blocks, EFFECTS)
    del blocks[4:]  # the response that advanced the task is sent again
    assert owner_after(blocks) == {"task": "T3", "step": "14"}
    blocks.append(_tool("edit", "c5", task="T3", step="15"))
    assert stamp(blocks, effects_of(blocks)) == 4
    assert _owners(blocks)[4] == ("T3", "14")


def test_stale_stamps_and_effects_are_removed():
    blocks = [_tool("task_step", "c1", task="T1", step="1", task_effect={"task": "T1", "step": "1"}),
              {"type": "text", "text": "x", "task": "T1", "step": "1"}]
    assert stamp(blocks, {}) == 0
    assert blocks == [_tool("task_step", "c1"), _text("x")]


def test_effects_are_cleaned():
    assert task_blocks.clean_effect({"task": "T1", "step": 14}) == {"task": "T1", "step": "14"}
    assert task_blocks.clean_effect({"task": ""}) == {"task": None}
    assert task_blocks.clean_effect({"step": "1"}) is None
    assert task_blocks.clean_effect("T1") is None
    # Any call can carry one: a real call's task_progress moves the reply too.
    assert effects_of([_tool("edit", "c1", task_effect={"task": "T1"})]) == {"c1": {"task": "T1"}}
    assert effects_of([_tool("edit", "c1"), _text()]) == {}


def test_a_real_call_with_progress_is_the_boundary_and_its_own_step():
    """A call whose task_progress opened the task or moved it on belongs to
    the step it began, and so does everything after it."""
    blocks = [_text("I'll start."), _tool("bash", "c1"), _tool("read", "c2"),
              _tool("edit", "c3"), _text("Done.")]
    effects = {"c1": {"task": "T1", "step": "1"}, "c3": {"task": "T1", "step": "2"}}
    assert stamp(blocks, effects) == 1
    assert _owners(blocks) == [(None, None), ("T1", "1"), ("T1", "1"), ("T1", "2"), ("T1", "2")]
    assert effects_of(blocks) == effects
    assert owner_after(blocks) == {"task": "T1", "step": "2"}

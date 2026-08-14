"""Tests for context.snip — oldest-turn removal."""

from openprogram.context.snip import snip, _split_turns, SNIP_NOTE


def _counter(messages):
    """Dummy token counter: 100 tokens per message."""
    return len(messages) * 100


def test_no_snip_when_under_threshold():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    result, removed = snip(msgs, _counter, context_window=10000)
    assert removed == 0
    assert result == msgs


def test_snip_removes_oldest_turn():
    msgs = [
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "reply 1"},
        {"role": "user", "content": "turn 2"},
        {"role": "assistant", "content": "reply 2"},
        {"role": "user", "content": "turn 3"},
        {"role": "assistant", "content": "reply 3"},
    ]
    # 6 messages * 100 = 600 tokens. threshold = 500 - 13 = 487
    result, removed = snip(msgs, _counter, context_window=500, reserve=13)
    assert removed >= 1
    # Should not contain turn 1
    contents = [m["content"] for m in result if m["role"] != "user" or "Note:" not in m.get("content", "")]
    assert "turn 1" not in contents
    assert "reply 1" not in contents
    # Should contain the snip note
    assert any(SNIP_NOTE.format(n=removed) in m.get("content", "") for m in result)


def test_snip_preserves_last_turn():
    msgs = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old reply"},
        {"role": "user", "content": "latest"},
        {"role": "assistant", "content": "latest reply"},
    ]
    # Force heavy snipping
    result, removed = snip(msgs, _counter, context_window=300, reserve=0)
    # Must keep at least the last turn
    assert any(m.get("content") == "latest" for m in result)
    assert any(m.get("content") == "latest reply" for m in result)


def test_snip_handles_tool_use_in_turn():
    msgs = [
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "let me check"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1"}]},
        {"role": "tool", "content": "result", "tool_call_id": "t1"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "turn 2"},
        {"role": "assistant", "content": "reply 2"},
    ]
    # All 7 messages in first turn + 2 in second = 7+2=9 total
    # 900 tokens > threshold 500-13=487, need to remove first turn
    result, removed = snip(msgs, _counter, context_window=500, reserve=13)
    assert removed >= 1
    # Tool messages from turn 1 should be gone
    assert not any(m.get("tool_call_id") == "t1" for m in result)


def test_split_turns():
    msgs = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "d"},
    ]
    turns = _split_turns(msgs)
    assert len(turns) == 2
    assert turns[0] == [msgs[0], msgs[1]]
    assert turns[1] == [msgs[2], msgs[3]]


def test_single_turn_not_removed():
    msgs = [
        {"role": "user", "content": "only"},
        {"role": "assistant", "content": "one"},
    ]
    result, removed = snip(msgs, _counter, context_window=100, reserve=0)
    assert removed == 0
    assert result == msgs


def test_snip_note_format():
    msgs = [
        {"role": "user", "content": "t1"},
        {"role": "assistant", "content": "r1"},
        {"role": "user", "content": "t2"},
        {"role": "assistant", "content": "r2"},
        {"role": "user", "content": "t3"},
        {"role": "assistant", "content": "r3"},
    ]
    result, removed = snip(msgs, _counter, context_window=400, reserve=13)
    assert removed > 0
    note_msgs = [m for m in result if "Note:" in m.get("content", "")]
    assert len(note_msgs) == 1
    assert f"{removed} earlier conversation turn" in note_msgs[0]["content"]

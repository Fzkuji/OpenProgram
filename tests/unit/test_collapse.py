"""Tests for Tier 3 Context Collapse — segmented LLM summary."""

from openprogram.context.collapse import collapse, _split_turns, _make_segments


def _make_turn(i: int, tokens: int = 100) -> list[dict]:
    return [
        {"role": "user", "content": f"user message {i}" + " x" * tokens},
        {"role": "assistant", "content": f"assistant reply {i}" + " y" * tokens},
    ]


def _flat_history(n_turns: int, tokens_per_turn: int = 100) -> list[dict]:
    msgs = []
    for i in range(n_turns):
        msgs.extend(_make_turn(i, tokens_per_turn))
    return msgs


def _counter(msgs):
    return sum(len(str(m.get("content", ""))) for m in msgs)


def _dummy_llm(prompt: str) -> str:
    return "Summary of the segment."


def test_no_collapse_when_under_threshold():
    msgs = _flat_history(5)
    result, originals, n = collapse(
        msgs, _dummy_llm, _counter, context_window=999999
    )
    assert n == 0
    assert result == msgs
    assert originals == []


def test_collapse_reduces_messages():
    msgs = _flat_history(20, tokens_per_turn=200)
    result, originals, n = collapse(
        msgs, _dummy_llm, _counter, context_window=2000, reserve=100
    )
    assert n > 0
    assert len(result) < len(msgs)
    assert len(originals) == len(msgs)


def test_recent_messages_preserved():
    msgs = _flat_history(20, tokens_per_turn=200)
    result, _, n = collapse(
        msgs, _dummy_llm, _counter, context_window=2000, reserve=100,
        keep_recent=6,
    )
    if n > 0:
        recent_texts = [m["content"] for m in result[-6:]]
        original_recent = [m["content"] for m in msgs[-6:]]
        assert recent_texts == original_recent


def test_collapsed_messages_contain_summary():
    msgs = _flat_history(20, tokens_per_turn=200)
    result, _, n = collapse(
        msgs, _dummy_llm, _counter, context_window=2000, reserve=100
    )
    if n > 0:
        collapsed_msgs = [m for m in result if "[Collapsed" in m.get("content", "")]
        assert len(collapsed_msgs) == n
        for m in collapsed_msgs:
            assert "Summary of the segment" in m["content"]


def test_split_turns():
    msgs = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "d"},
    ]
    turns = _split_turns(msgs)
    assert len(turns) == 2
    assert turns[0][0]["content"] == "a"
    assert turns[1][0]["content"] == "c"


def test_make_segments():
    turns = [[{"role": "user"}] for _ in range(12)]
    segs = _make_segments(turns, segment_size=5)
    assert len(segs) == 3
    assert len(segs[0]) == 5
    assert len(segs[1]) == 5
    assert len(segs[2]) == 2


def test_llm_failure_fallback():
    def _bad_llm(prompt):
        raise RuntimeError("LLM down")

    msgs = _flat_history(20, tokens_per_turn=200)
    result, _, n = collapse(
        msgs, _bad_llm, _counter, context_window=2000, reserve=100
    )
    if n > 0:
        collapsed_msgs = [m for m in result if "[Collapsed" in m.get("content", "")]
        for m in collapsed_msgs:
            assert "messages summarized" in m["content"]

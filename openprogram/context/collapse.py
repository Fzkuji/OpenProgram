"""Tier 3 Context Collapse — segmented LLM summary.

Divides old conversation turns into segments (default 5 turns each),
summarizes each segment with an LLM call, and replaces the original
messages with the summaries.  Original messages are returned separately
so the caller can store them for potential rollback.

Runs after Snip (Tier 2) and before Auto-Compact (Tier 4).
"""

from __future__ import annotations

from typing import Any, Callable

COLLAPSE_SEGMENT_SIZE = 5
COLLAPSE_KEEP_RECENT = 10

COLLAPSE_PROMPT = (
    "Summarize the following conversation segment in 2-3 sentences. "
    "Focus on: what was discussed, key decisions made, and any "
    "important outcomes. Be concise.\n\n{segment_text}"
)


def collapse(
    messages: list[dict[str, Any]],
    llm_call: Callable[[str], str],
    token_counter: Callable[[list[dict[str, Any]]], int],
    context_window: int,
    reserve: int = 13_000,
    segment_size: int = COLLAPSE_SEGMENT_SIZE,
    keep_recent: int = COLLAPSE_KEEP_RECENT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Segment-summarize old messages using LLM.

    Parameters
    ----------
    messages:
        The conversation history (list of role-keyed dicts).
    llm_call:
        ``fn(prompt) -> summary_text`` — synchronous LLM call.
    token_counter:
        ``fn(messages) -> int`` that counts prompt tokens.
    context_window:
        The model's full context window size in tokens.
    reserve:
        Headroom to keep free (default 13 000).
    segment_size:
        Number of turns per segment (default 5).
    keep_recent:
        Number of recent messages to keep intact (default 10).

    Returns
    -------
    (collapsed_messages, original_messages, n_segments_collapsed)
        collapsed_messages: messages with old segments replaced by summaries
        original_messages: the original messages before collapse (for rollback)
        n_segments_collapsed: how many segments were summarized
    """
    threshold = context_window - reserve
    current = token_counter(messages)
    if current <= threshold:
        return messages, [], 0

    original = list(messages)

    if len(messages) <= keep_recent:
        return messages, [], 0

    old_part = messages[:-keep_recent]
    recent_part = messages[-keep_recent:]

    turns = _split_turns(old_part)
    if len(turns) <= 1:
        return messages, [], 0

    segments = _make_segments(turns, segment_size)

    collapsed: list[dict[str, Any]] = []
    n_collapsed = 0

    for segment_turns in segments:
        flat_segment = _flatten(segment_turns)
        segment_text = _render_segment(flat_segment)

        try:
            prompt = COLLAPSE_PROMPT.format(segment_text=segment_text)
            summary = llm_call(prompt)
        except Exception:
            summary = f"[{len(flat_segment)} messages summarized]"

        collapsed.append({
            "role": "user",
            "content": f"[Collapsed {len(segment_turns)} turns]: {summary}",
        })
        n_collapsed += 1

    result = collapsed + recent_part

    new_tokens = token_counter(result)
    if new_tokens > threshold:
        pass

    return result, original, n_collapsed


def _split_turns(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    turns: list[list[dict[str, Any]]] = []
    current_turn: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "user" and current_turn:
            turns.append(current_turn)
            current_turn = []
        current_turn.append(msg)
    if current_turn:
        turns.append(current_turn)
    return turns


def _make_segments(
    turns: list[list[dict[str, Any]]], segment_size: int
) -> list[list[list[dict[str, Any]]]]:
    segments = []
    for i in range(0, len(turns), segment_size):
        segments.append(turns[i : i + segment_size])
    return segments


def _flatten(turns: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for turn in turns:
        result.extend(turn)
    return result


def _render_segment(messages: list[dict[str, Any]]) -> str:
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = [
                p.get("text", "") for p in content if isinstance(p, dict)
            ]
            content = "\n".join(text_parts)
        if len(content) > 500:
            content = content[:500] + "..."
        lines.append(f"{role}: {content}")
    return "\n".join(lines)

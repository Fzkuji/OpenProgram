"""Snip — remove oldest conversation turns when context is full.

Follows Claude Code's snipCompactIfNeeded: delete complete turns
(user + assistant + tool calls) from the oldest end. Free operation
(no LLM call), runs before the more expensive compact().

A "turn" is one user message + the assistant reply that follows it +
any tool-use / tool-result pairs within that assistant reply.  We
never split a turn — removing half would leave dangling tool_use
blocks without their results, which breaks the message contract.
"""

from __future__ import annotations

from typing import Any, Callable

SNIP_NOTE = "[Note: {n} earlier conversation turn(s) removed from context]"


def snip(
    messages: list[dict[str, Any]],
    token_counter: Callable[[list[dict[str, Any]]], int],
    context_window: int,
    reserve: int = 13_000,
) -> tuple[list[dict[str, Any]], int]:
    """Remove the oldest complete turns until tokens fit.

    Parameters
    ----------
    messages:
        The conversation history (list of role-keyed dicts).
    token_counter:
        ``fn(messages) -> int`` that counts prompt tokens.
    context_window:
        The model's full context window size in tokens.
    reserve:
        Headroom to keep free (default 13 000, matching Claude Code).

    Returns
    -------
    (trimmed_messages, n_turns_removed)
    """
    threshold = context_window - reserve
    current = token_counter(messages)
    if current <= threshold:
        return messages, 0

    turns = _split_turns(messages)
    if len(turns) <= 1:
        return messages, 0

    removed = 0
    while current > threshold and len(turns) > 1:
        turns.pop(0)
        removed += 1
        flat = _flatten(turns)
        current = token_counter(flat)

    result = _flatten(turns)

    if removed > 0:
        note = SNIP_NOTE.format(n=removed)
        result.insert(0, {"role": "user", "content": note})

    return result, removed


def _split_turns(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split a flat message list into turns.

    A turn starts at each ``role=user`` message and includes
    everything until (but not including) the next ``role=user``.
    """
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


def _flatten(turns: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Flatten a list of turns back into a flat message list."""
    result: list[dict[str, Any]] = []
    for turn in turns:
        result.extend(turn)
    return result

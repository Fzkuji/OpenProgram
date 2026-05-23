"""Head + tail middle-truncation for over-long strings.

A single tool result of 50MB blows the LLM context even within one
turn, so this runs on the tail window too — not just on aged turns.
The truncation preserves the start (where the model usually puts
the most informative summary line) and the end (where errors and
exit codes land), dropping the middle.
"""
from __future__ import annotations


def middle_truncate(text: str, cap: int) -> str:
    """Truncate ``text`` to roughly ``cap`` chars, keeping head + tail.

    Returns the original ``text`` if it's already short enough.
    Truncation marker reports how many chars were dropped so the
    model can ask for the full output via another tool if needed.
    """
    if not isinstance(text, str):
        text = str(text)
    n = len(text)
    if n <= cap:
        return text
    # Reserve ~30 chars for the marker; split remaining 60/40 head/tail
    # — head tends to carry the diagnostic line / signature / first
    # rows; tail tends to carry the conclusion / exit code.
    body_budget = max(cap - 60, 200)
    head_len = (body_budget * 3) // 5
    tail_len = body_budget - head_len
    dropped = n - head_len - tail_len
    return (
        text[:head_len]
        + f"\n... [truncated {dropped} chars] ...\n"
        + text[-tail_len:]
    )

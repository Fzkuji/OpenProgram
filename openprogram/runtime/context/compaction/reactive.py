"""Recognising a context overflow the provider reported.

Recovery itself is the turn's: the agent loop hands its messages to the
turn's ``compact_turn`` (``conversation/turn_runtime/turn_compaction.py``),
which compacts the branch for good and rebuilds the request, and the loop
sends the request that overflowed once more. Only that request is sent
again, never the whole turn (context/compaction.html, "Reactive").
"""
from __future__ import annotations


def is_overflow_error(exc: Exception) -> bool:
    """Whether an exception says the request did not fit the context window."""
    msg_text = str(exc)
    from openprogram.models.providers.utils.overflow import OVERFLOW_PATTERNS, _STATUS_CODE_RE
    for p in OVERFLOW_PATTERNS:
        if p.search(msg_text):
            return True
    if _STATUS_CODE_RE.match(msg_text):
        return True
    return False


__all__ = ["is_overflow_error"]

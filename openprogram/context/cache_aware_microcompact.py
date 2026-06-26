"""Cache-aware Microcompact — Anthropic Context Editing API integration.

Uses the Anthropic ``cache_edits`` API parameter to clear old tool_result
blocks server-side without breaking prompt cache prefix. This is the
cache-aware path of Microcompact (the time-based path lives in
``microcompact.py``).

Trigger: after 50 tool calls, then every 25 calls thereafter.
Only works with Anthropic API (other providers fall back to time-based).

Reference: https://platform.claude.com/docs/en/build-with-claude/context-editing
Beta header: context-management-2025-06-27
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

INITIAL_TRIGGER = 50
SUBSEQUENT_INTERVAL = 25
KEEP_RECENT = 5

_tool_call_count: ContextVar[int] = ContextVar("_tool_call_count", default=0)
_last_trigger_at: ContextVar[int] = ContextVar("_last_trigger_at", default=0)


def increment_tool_calls(n: int = 1) -> int:
    """Bump the tool call counter and return the new count."""
    current = _tool_call_count.get(0)
    new = current + n
    _tool_call_count.set(new)
    return new


def should_trigger() -> bool:
    """Check whether cache-aware microcompact should fire this turn."""
    count = _tool_call_count.get(0)
    last = _last_trigger_at.get(0)
    if count < INITIAL_TRIGGER:
        return False
    if last == 0:
        return True
    return (count - last) >= SUBSEQUENT_INTERVAL


def build_cache_edits() -> dict[str, Any] | None:
    """Build the ``cache_edits`` parameter for the Anthropic API.

    Returns None if cache-aware microcompact should not fire this turn.
    When it returns a dict, the caller should merge it into the API
    request params.
    """
    if not should_trigger():
        return None
    _last_trigger_at.set(_tool_call_count.get(0))
    return {
        "cache_edits": {
            "strategy": "clear_tool_uses",
            "keep_recent": KEEP_RECENT,
        },
    }


def reset():
    """Reset counters (for testing)."""
    _tool_call_count.set(0)
    _last_trigger_at.set(0)

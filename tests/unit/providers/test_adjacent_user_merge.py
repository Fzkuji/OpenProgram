"""Two user messages with no reply between become one where roles must alternate.

A reply stopped before it said anything is not sent, so the next message
follows the one it answered (Codex keeps such input on the user side; it
puts nothing in the assistant's place).
"""
from __future__ import annotations

import time

from openprogram.models.providers.types import (
    AssistantMessage, Context, TextContent, ToolCall, ToolResultMessage, UserMessage,
)


def _user(text: str) -> UserMessage:
    return UserMessage(role="user", content=[TextContent(type="text", text=text)], timestamp=0)


def _messages():
    now = int(time.time() * 1000)
    call = AssistantMessage(content=[ToolCall(id="t1", name="read", arguments={})], api="anthropic-messages",
                            provider="anthropic", model="m", stop_reason="toolUse", timestamp=now)
    result = ToolResultMessage(role="toolResult", tool_call_id="t1", tool_name="read",
                               content=[TextContent(type="text", text="ok")], is_error=False, timestamp=now)
    return [_user("write a poem"), _user("continue"), call, result, _user("thanks")]


def test_anthropic_joins_adjacent_user_messages_and_leaves_tool_results_alone() -> None:
    from openprogram.models.providers.adapters.anthropic.anthropic import _build_messages

    built = _build_messages(Context(messages=_messages()))

    assert [m["role"] for m in built] == ["user", "assistant", "user", "user"]
    assert [b["text"] for b in built[0]["content"]] == ["write a poem", "continue"]
    assert built[2]["content"][0]["type"] == "tool_result"


def test_gemini_joins_adjacent_user_turns() -> None:
    from openprogram.models.providers.shared.google import _merge_adjacent_user_turns

    merged = _merge_adjacent_user_turns([
        {"role": "user", "parts": [{"text": "write a poem"}]},
        {"role": "user", "parts": [{"text": "continue"}]},
        {"role": "model", "parts": [{"text": "ok"}]},
        {"role": "user", "parts": [{"functionResponse": {"name": "x"}}]},
        {"role": "user", "parts": [{"text": "after"}]},
    ])

    assert [t["role"] for t in merged] == ["user", "model", "user", "user"]
    assert merged[0]["parts"] == [{"text": "write a poem"}, {"text": "continue"}]


def test_openai_requests_are_unchanged() -> None:
    from openprogram.models.providers.adapters.anthropic import anthropic
    from openprogram.models.providers.shared import google
    import openprogram.models.providers.adapters.openai_completions as completions

    # Only the two adapters that need alternation merge.
    assert hasattr(anthropic, "_merge_adjacent_user_messages")
    assert hasattr(google, "_merge_adjacent_user_turns")
    assert not any("merge_adjacent_user" in name for name in dir(completions))

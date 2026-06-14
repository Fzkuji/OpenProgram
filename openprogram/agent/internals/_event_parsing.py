"""Agent-event → chat envelope translation + usage extraction.

Pure helpers lifted out of ``dispatcher.py``. No state, no DB — just
shape transforms over what the agent loop emits.

  * ``agent_event_to_envelope`` — AgentEvent → chat_response dict the
    UI consumes. Live streaming, tool start/end.
  * ``aiter_event_stream`` — yield-each generator wrapper around the
    EventStream so tests can monkey-patch with a static list.
  * ``extract_text`` — pull plain text out of an AssistantMessage's
    content blocks.
  * ``extract_usage`` — normalize the dozen variations of usage
    dict/object providers return into a single 4-key shape.
  * ``shorten`` / ``stringify_tool_result`` — render tool output
    payloads for the chat bubble.
"""
from __future__ import annotations

import json
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.agent.dispatcher import TurnRequest


def agent_event_to_envelope(ev, req: "TurnRequest") -> Optional[dict]:
    """Convert an AgentEvent → chat_response envelope (the same shape
    the legacy webui chat path emitted), so TUI/web handlers work
    unchanged."""
    t = getattr(ev, "type", None)
    cid = req.session_id

    if t == "message_update":
        ame = getattr(ev, "assistant_message_event", None)
        if ame is None:
            return None
        ame_type = getattr(ame, "type", None)
        if ame_type == "text_delta":
            return {
                "type": "chat_response",
                "data": {"type": "stream_event",
                         "session_id": cid,
                         "event": {"type": "text",
                                   "text": getattr(ame, "delta", "")}},
            }
        if ame_type == "thinking_delta":
            return {
                "type": "chat_response",
                "data": {"type": "stream_event",
                         "session_id": cid,
                         "event": {"type": "thinking",
                                   "text": getattr(ame, "delta", "")}},
            }
        return None

    if t == "tool_execution_start":
        args = getattr(ev, "args", None)
        return {
            "type": "chat_response",
            "data": {"type": "stream_event",
                     "session_id": cid,
                     "event": {"type": "tool_use",
                               "tool": getattr(ev, "tool_name", "?"),
                               "input": json.dumps(args, default=str)
                                        if args is not None else None,
                               "tool_call_id": getattr(ev, "tool_call_id", None)}},
        }

    if t == "tool_execution_end":
        return {
            "type": "chat_response",
            "data": {"type": "stream_event",
                     "session_id": cid,
                     "event": {"type": "tool_result",
                               "tool": getattr(ev, "tool_name", "?"),
                               "result": shorten(getattr(ev, "result", "")),
                               "is_error": bool(getattr(ev, "is_error", False)),
                               "tool_call_id": getattr(ev, "tool_call_id", None)}},
        }

    return None


async def aiter_event_stream(ev_stream):
    """Iterate an EventStream as an async generator.

    EventStream from agent_loop has __aiter__ already; this wrapper
    is a seam tests can monkey-patch with a list of events.
    """
    async for ev in ev_stream:
        yield ev


def extract_text(msg) -> str:
    """Pull plain text out of an AssistantMessage's content list."""
    if msg is None:
        return ""
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    if not content:
        return ""
    parts: list[str] = []
    for c in content:
        ctype = getattr(c, "type", None)
        if ctype == "text":
            parts.append(getattr(c, "text", "") or "")
    return "".join(parts)


def extract_usage(msg) -> dict:
    """Pull a usage dict from a final assistant message.

    Handles three shapes providers emit: pydantic Usage, plain dict,
    AssistantMessage.usage. Normalises field aliases to Anthropic shape
    (input excludes cached_tokens) so cache_hit_rate computes the same
    for OpenAI / Anthropic without per-provider branching downstream.
    """
    if msg is None:
        return {}
    usage = None
    if isinstance(msg, dict):
        usage = msg.get("usage")
    else:
        usage = getattr(msg, "usage", None)
        if usage is not None and hasattr(usage, "model_dump"):
            usage = usage.model_dump()
    if usage is None:
        return {}

    def _g(*names):
        for n in names:
            if isinstance(usage, dict):
                v = usage.get(n)
            else:
                v = getattr(usage, n, None)
            if v:
                return int(v)
        return 0
    input_tokens = _g("input_tokens", "input", "prompt_tokens")
    output_tokens = _g("output_tokens", "output", "completion_tokens")
    cache_read = _g("cache_read_tokens", "cache_read", "cached_tokens")
    cache_write = _g("cache_write_tokens", "cache_write", "cache_creation_input_tokens")

    def _has(*names):
        for n in names:
            if isinstance(usage, dict):
                if usage.get(n):
                    return True
            elif getattr(usage, n, None):
                return True
        return False
    if _has("prompt_tokens") and cache_read and input_tokens >= cache_read:
        input_tokens -= cache_read
    return {
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens":  cache_read,
        "cache_write_tokens": cache_write,
    }


def shorten(value, limit: int = 4000) -> str:
    s = stringify_tool_result(value)
    if len(s) <= limit:
        return s
    return s[:limit] + f"... (+{len(s) - limit} more)"


def stringify_tool_result(value) -> str:
    """Turn whatever AgentEventToolEnd.result carries into a string
    the UI can render directly.

    AgentToolResult.content is a list of TextContent / ImageContent
    blocks. Naïvely str()-ing produces a pydantic repr — pull the
    actual text out of the blocks instead.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    content = getattr(value, "content", None)
    if content is not None:
        parts: list[str] = []
        try:
            for block in content:
                txt = getattr(block, "text", None)
                if txt:
                    parts.append(txt)
        except TypeError:
            parts = []
        if parts:
            return "\n".join(parts)
    try:
        return json.dumps(value, default=str)
    except Exception:
        return str(value)

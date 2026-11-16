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
    payloads for the chat bubble; ``stored_tool_result`` is the whole
    text a tool node keeps.
"""
from __future__ import annotations

import json
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.runtime.agent.turn_request import TurnRequest


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
            output_attempt = getattr(ame, "output_attempt", None)
            event = {"type": "text", "text": getattr(ame, "delta", "")}
            if output_attempt is not None:
                event["output_attempt"] = output_attempt
            return {
                "type": "chat_response",
                "data": {"type": "stream_event",
                         "session_id": cid,
                         "msg_id": req.user_msg_id,
                         "event": event},
            }
        if ame_type == "thinking_delta":
            return {
                "type": "chat_response",
                "data": {"type": "stream_event",
                         "session_id": cid,
                         "msg_id": req.user_msg_id,
                         "event": {"type": "thinking",
                                   "text": getattr(ame, "delta", "")}},
            }
        if ame_type in {"servertool_start", "servertool_end", "text_end"}:
            event = provider_search_event(ame)
            if event is None:
                return None
            return {
                "type": "chat_response",
                "data": {"type": "stream_event",
                         "session_id": cid,
                         "msg_id": req.user_msg_id,
                         "event": event},
            }
        if ame_type in {"structured_output_retry", "structured_output_end", "retry"}:
            event = (
                ame.model_dump(mode="json", exclude_none=True)
                if hasattr(ame, "model_dump")
                else dict(vars(ame))
            )
            return {
                "type": "chat_response",
                "data": {
                    "type": "stream_event",
                    "session_id": cid,
                    "msg_id": req.user_msg_id,
                    "event": event,
                },
            }
        return None

    if t == "tool_execution_start":
        args = getattr(ev, "args", None)
        event = {"type": "tool_use",
                 "tool": getattr(ev, "tool_name", "?"),
                 "input": json.dumps(args, default=str)
                          if args is not None else None,
                 "tool_call_id": getattr(ev, "tool_call_id", None)}
        if getattr(ev, "task_effect", None) is not None:
            # The call's ``task_progress`` moved the reply (``state.task_blocks``).
            event["task_effect"] = ev.task_effect
        return {
            "type": "chat_response",
            "data": {"type": "stream_event",
                     "session_id": cid,
                     "msg_id": req.user_msg_id,
                     "event": event},
        }

    if t == "tool_execution_end":
        stored = stored_tool_result(getattr(ev, "result", ""))
        raw_details = getattr(getattr(ev, "result", None), "details", None)
        shown, truncated = card_text(stored, raw_details)
        event = {"type": "tool_result",
                 "tool": getattr(ev, "tool_name", "?"),
                 "result": shown,
                 "is_error": bool(getattr(ev, "is_error", False)),
                 "outcome": getattr(ev, "outcome", None),
                 "tool_call_id": getattr(ev, "tool_call_id", None)}
        if truncated:
            event["full_output"] = truncated
        if event["result"] != stored:
            event[STORED_RESULT_KEY] = stored
        media = _stored_media(req, getattr(getattr(ev, "result", None), "content", None))
        if media:
            event[STORED_MEDIA_KEY] = media
        from openprogram.models.providers.shared.tool_loads import durable_details
        details = durable_details(raw_details)
        if details:
            event[STORED_DETAILS_KEY] = details
        return {
            "type": "chat_response",
            "data": {"type": "stream_event",
                     "session_id": cid,
                     "msg_id": req.user_msg_id,
                     "event": event},
        }

    return None


def provider_search_event(ame) -> Optional[dict]:
    """Client event for a provider-executed search or a cited text block.

    ``servertool_start`` / ``servertool_end`` become the ``tool_use`` /
    ``tool_result`` events function tools use, marked
    ``provider_executed``; ``text_end`` is forwarded only when its block
    carries citations. Anything else (or a malformed event) is ``None``.
    """
    from openprogram.runtime.agent import provider_search as ps

    ame_type = getattr(ame, "type", None)
    if ame_type == "text_end":
        citations = ps.text_end_citations(ame)
        if not citations:
            return None
        return {"type": "text_end",
                "content_index": getattr(ame, "content_index", None),
                "citations": citations}
    block = ps.server_tool_block_at(ame)
    if block is None:
        return None
    call_id = str(ps._get(block, "id") or "")
    if not call_id:
        return None
    if ame_type == "servertool_start":
        return {"type": "tool_use",
                "tool": ps.server_tool_name(block),
                "input": ps.server_tool_input(block),
                "tool_call_id": call_id,
                "provider_executed": True}
    if ame_type == "servertool_end":
        failed = ps.server_tool_failed(block)
        return {"type": "tool_result",
                "tool": ps.server_tool_name(block),
                "input": ps.server_tool_input(block),
                "result": ps.server_tool_result(block),
                "is_error": failed,
                "outcome": "failed" if failed else "completed",
                "tool_call_id": call_id,
                "provider_executed": True}
    return None


async def aiter_event_stream(ev_stream):
    """Iterate an EventStream as an async generator.

    EventStream from agent_loop has __aiter__ already; this wrapper
    is a seam tests can monkey-patch with a list of events.
    """
    async for ev in ev_stream:
        yield ev


def extract_text(msg) -> str:
    """One AssistantMessage's flat text (``reply_text``: passages a tool
    call or reasoning came between are a blank line apart)."""
    if msg is None:
        return ""
    from openprogram.state.reply_text import reply_text
    return reply_text(getattr(msg, "content", None))


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
    result = {
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens":  cache_read,
        "cache_write_tokens": cache_write,
    }
    request_input_estimate = _g("request_input_estimate")
    if request_input_estimate:
        result["request_input_estimate"] = request_input_estimate
    requested = usage.get("requested_service_tier") if isinstance(usage, dict) else getattr(usage, "requested_service_tier", None)
    actual = usage.get("service_tier") if isinstance(usage, dict) else getattr(usage, "service_tier", None)
    if requested in ("priority", "fast") or actual in ("priority", "fast"):
        result["service_tiers"] = [actual if actual in ("priority", "fast", "default") else "unreported"]
    return result



def card_text(stored: str, details) -> tuple[str, dict | None]:
    """What a tool card shows for a result, and its truncation line.

    A result the agent loop bounded (``tool_dispatch._as_stored``) shows
    the head of the tool's own output, not the head, tail and note written
    for the model; the second value then says how long the whole output is,
    and "view full output" reads the saved file (the card's
    ``full_output``). Any other result shows its stored text, cut to a
    preview."""
    output = details.get("output") if isinstance(details, dict) else None
    preview = details.get("preview") if isinstance(details, dict) else None
    if isinstance(output, dict) and isinstance(preview, str):
        return preview, {"total_lines": output.get("total_lines"),
                         "total_chars": output.get("total_chars"),
                         "shown_chars": len(preview)}
    return shorten(stored), None


def shorten(value, limit: int = 4000) -> str:
    s = stringify_tool_result(value)
    if len(s) <= limit:
        return s
    return s[:limit] + f"... (+{len(s) - limit} more)"


# The frame key carrying a tool result's whole text when the frame's
# ``result`` is a preview. The stream tap stores it on the tool node and
# removes it before the frame goes on to any client.
STORED_RESULT_KEY = "stored_result"

# The frame key carrying a tool result's durable details (its load record,
# ``shared.tool_loads``). The stream tap stores them in the tool node's
# metadata and removes them before the frame goes on to any client.
STORED_DETAILS_KEY = "stored_details"


# The frame key carrying where a tool result's images (and other media) are
# kept (``store.session.media``). The stream tap stores it in the tool
# node's metadata and removes it before the frame goes on to any client.
STORED_MEDIA_KEY = "stored_media"


def _stored_media(req, content) -> list:
    """Keep a tool result's media in its conversation's folder and return
    their records, so later requests send them where the turn did."""
    if not content or not any(getattr(block, "data", None) for block in content):
        return []
    try:
        from openprogram.runtime.context.session_db import default_db
        from openprogram.state.store.session.files.media import records_for_blocks

        session_dir = default_db()._session_dir(req.session_id)  # noqa: SLF001
        return records_for_blocks(session_dir, content)
    except Exception:  # noqa: BLE001 - the turn goes on without the copy
        import logging
        logging.getLogger(__name__).warning(
            "tool result media not kept for session %s", getattr(req, "session_id", None), exc_info=True)
        return []


def stored_tool_result(value) -> str:
    """The text a tool node keeps: the result exactly as the model read it.

    Every later request renders history from this text, so it is never a
    preview: a cut result would hide the rest from the model and change the
    request bytes after the turn that ran the tool, which the provider then
    reads again uncached. Size is bounded once, where the result is made:
    the agent loop's bound to ``spill.result_cap()``
    (``tool_dispatch._as_stored``), the model's ``tool_output_weight`` or
    less (a smaller cap the tool declared, a small window). The bound also
    makes the turn send this same single text. An old result is never
    shortened; only compaction replaces it.
    """
    return stringify_tool_result(value)


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

"""Message helpers for a model request: the latest user text, memory prefetch injection and durable message dicts."""
from __future__ import annotations

import copy
import json
from typing import Any


def _latest_user_text(messages: list) -> str:
    """Walk back from the end and return the last user-role text.

    Memory prefetch uses this as the recall query for the upcoming
    turn. Empty string if no user message is present (e.g. on the
    first model warmup call).
    """
    for msg in reversed(messages):
        role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role != "user":
            continue
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for c in content:
                if isinstance(c, str):
                    parts.append(c)
                elif isinstance(c, dict):
                    if c.get("type") == "text" or "text" in c:
                        parts.append(str(c.get("text", "")))
                else:
                    text = getattr(c, "text", None)
                    if text:
                        parts.append(str(text))
            joined = " ".join(p for p in parts if p)
            if joined.strip():
                return joined.strip()
        return ""
    return ""



def _inject_memory_prefetch(llm_messages: list, block: str, anchor: str | None = None) -> bool:
    """Prefix ``block`` to the turn's user message in this request.

    The turn's message is the last user message, or, once the loop has added
    user-role notes of its own after it (a progress note, a "continue"
    instruction, something the user said mid-turn), the last one whose text is
    ``anchor``: the block stays where the first request of the turn put it, so
    the prefix the provider cached still matches.

    dag/overview.md §7 — prefetched memory (and a run note) belongs to the
    turn that recalled it, not to the session-constant system prompt. Tool
    results carry role ``toolResult``, so the last ``role == "user"`` message
    is always the conversational turn.

    ``llm_messages`` is the request's own list, but its messages may be the
    stored context's objects (the default converter only filters them). The
    message is therefore replaced in the list by a copy carrying the block,
    and never edited: editing it would store the block in the conversation,
    so every later round would add it again and the provider's cached prefix
    would stop matching. Returns True when a message was replaced.

    Later turns render the block from the message's node
    (``context.render.with_turn_block``), with the same bytes; a message that
    already starts with it (a resumed turn rendered from its node) is left
    as it is.
    """
    from openprogram.runtime.context.render import with_turn_block

    prefix = with_turn_block("", block)
    if anchor and not any(_field(msg, "role") == "user" and _latest_user_text([msg]) == anchor
                          for msg in llm_messages or []):
        anchor = None
    for index in range(len(llm_messages or []) - 1, -1, -1):
        msg = llm_messages[index]
        if _field(msg, "role") != "user":
            continue
        if anchor and _latest_user_text([msg]) != anchor:
            continue
        content = _field(msg, "content")
        if isinstance(content, str):
            # A message rendered from history already carries its block.
            if not content.startswith(prefix):
                llm_messages[index] = _with(msg, "content", prefix + content)
            return True
        if isinstance(content, list):
            for at, part in enumerate(content):
                if _field(part, "type") != "text":
                    continue
                if str(_field(part, "text") or "").startswith(prefix):
                    return True
                text = prefix + str(_field(part, "text") or "")
                parts = [*content[:at], _with(part, "text", text), *content[at + 1:]]
                llm_messages[index] = _with(msg, "content", parts)
                return True
            # Image/file-only turn: no text part to prefix — leave it alone
            # rather than inventing a block ordering the provider may reject.
        return False
    return False


def _field(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _with(item: Any, name: str, value: Any) -> Any:
    """A shallow copy of ``item`` with one field replaced."""
    if isinstance(item, dict):
        return {**item, name: value}
    if hasattr(item, "model_copy"):
        return item.model_copy(update={name: value})
    clone = copy.copy(item)
    setattr(clone, name, value)
    return clone


def _durable_message(message: Any) -> dict[str, Any]:
    """Return JSON data only; checkpoints never retain provider objects."""
    if hasattr(message, "model_dump"):
        try:
            value = message.model_dump(mode="json")
        except Exception:
            # AgentTool contains its executable callback, which is not a
            # durable value.  A checkpoint needs the resolved public schema,
            # not a repr of that callback or a process-local object address.
            if all(hasattr(message, field) for field in ("name", "description", "parameters")):
                value = {
                    "name": message.name,
                    "description": message.description,
                    "parameters": message.parameters,
                }
            else:
                value = {"repr": str(message)}
    elif isinstance(message, dict):
        value = dict(message)
    else:
        value = {"repr": str(message)}
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))

"""
Shared utilities for OpenAI Responses API providers.

Handles message/tool conversion to Responses API format and streaming
event processing.

Mirrors openai-responses-shared.ts
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from openprogram.models.providers.utils.streams.json_parse import parse_streaming_json
from openprogram.models.providers.utils.sanitize_unicode import sanitize_surrogates

if TYPE_CHECKING:
    from openprogram.models.providers.types import (
        AssistantMessage,
        Context,
        Model,
        StopReason,
        Tool,
    )
    from openprogram.models.providers.utils.streams.event_stream import EventStream


# ---------------------------------------------------------------------------
# Hash utility
# ---------------------------------------------------------------------------

def _responses_item_dict(item: Any) -> dict[str, Any]:
    if item is None:
        return {}
    if isinstance(item, dict):
        return item
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        try:
            payload = dump(mode="json")
        except TypeError:
            payload = dump()
        if isinstance(payload, dict):
            return payload
    raw = getattr(item, "__dict__", None)
    return dict(raw) if isinstance(raw, dict) else {}


def _drop_none(value: Any) -> Any:
    """Recursively drop null fields (SDK dumps include absent optionals)."""
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_none(v) for v in value]
    return value


def _web_search_input(action: Any) -> dict[str, Any]:
    """``{"query": ...}`` from a ``web_search_call`` item's ``action``."""
    action = _responses_item_dict(action)
    query = action.get("query")
    if not query:
        queries = action.get("queries") or []
        query = queries[0] if queries and isinstance(queries[0], str) else None
    if query:
        return {"query": query}
    if action.get("url"):
        # open_page / find actions carry the page instead of a query.
        return {"url": action["url"]}
    return {}


def _web_search_sources(action: Any) -> list[dict[str, Any]]:
    """``[{"url", "title"}]`` from ``action.sources`` (empty when absent)."""
    results: list[dict[str, Any]] = []
    for src in _responses_item_dict(action).get("sources") or []:
        src = _responses_item_dict(src)
        url = src.get("url")
        if url:
            results.append({"url": url, "title": src.get("title") or url})
    return results


def _url_citations(content: list[Any]) -> list[dict[str, Any]]:
    """``url_citation`` annotations of a message item's content parts."""
    citations: list[dict[str, Any]] = []
    for part in content:
        for ann in _responses_item_dict(part).get("annotations") or []:
            ann = _responses_item_dict(ann)
            if ann.get("type") != "url_citation" or not ann.get("url"):
                continue
            citations.append({
                "url": ann["url"],
                "title": ann.get("title"),
                "start": ann.get("start_index"),
                "end": ann.get("end_index"),
            })
    return citations


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value)
    except TypeError:
        return json.dumps(value, default=str)


# ---------------------------------------------------------------------------
# Client-executed tool search
# ---------------------------------------------------------------------------
#
# A deferred tool loaded mid-conversation must not change bytes already sent.
# The tools array is rendered before the conversation, so a tool added to it
# (even at its end) makes the provider re-read everything after it. The
# Responses API's client-executed ``tool_search`` puts a loaded tool where it
# was loaded instead: the model's search is a ``tool_search_call`` item, and
# the loaded definitions travel in the ``tool_search_output`` item that
# answers it, at the end of the input. The tools array then stays as it was.

#: The registered loader tool that maps onto the Responses ``tool_search`` tool.
TOOL_SEARCH_NAME = "tool_search"

#: The tag around a search's note sent after its ``tool_search_output``.
SEARCH_NOTE_TAG = "tool-search-result"


def _tool_search_tool(tool: "Tool", api: str | None, model_id: str | None) -> dict[str, Any]:
    from openprogram.models.providers.schema import normalize_for

    return {
        "type": "tool_search",
        "execution": "client",
        "description": tool.description,
        "parameters": normalize_for(api, tool.parameters, model_id),
    }


def carried_tool_names(input_items: list[dict[str, Any]]) -> set[str]:
    """Names of the tools a ``tool_search_output`` in ``input_items`` loads."""
    names: set[str] = set()
    for item in input_items:
        if isinstance(item, dict) and item.get("type") == "tool_search_output":
            for spec in item.get("tools") or ():
                if isinstance(spec, dict) and spec.get("name"):
                    names.add(spec["name"])
    return names


def _carried_search(message: Any, model: "Model") -> list[dict[str, Any]]:
    """The one search pair that loads what a compaction summary carries.

    A summary takes the place of the searches that loaded its carried tools
    (``shared.tool_loads.carried_records``); one ``tool_search_call`` /
    ``tool_search_output`` pair in front of it loads them again, each with
    the definition the conversation first sent, so the tools array stays as
    it was. Its call id comes from the names, so every request after the
    compaction sends the same bytes. A carried name with no stored
    definition is left to the tools array.
    """
    from openprogram.models.providers.shared.tool_loads import carried_records
    from openprogram.models.providers.types import Tool as _Tool

    records = [entry for entry in carried_records(message)
               if isinstance(entry.get("definition"), dict)
               and entry["definition"].get("name") == entry["name"]
               and isinstance(entry["definition"].get("parameters"), dict)]
    if not records:
        return []
    names = [entry["name"] for entry in records]
    call_id = f"carried_{_short_hash(','.join(names))}"
    return [
        {
            "type": "tool_search_call",
            "execution": "client",
            "call_id": call_id,
            "status": "completed",
            "arguments": {"select": "select:" + ",".join(names)},
        },
        {
            "type": "tool_search_output",
            "execution": "client",
            "call_id": call_id,
            "status": "completed",
            "tools": [_function_tool(_Tool(**entry["definition"]), model.api, model.id)
                      for entry in records],
        },
    ]


def loads_searched_tools_from_input(model: Any) -> bool:
    """Whether a request to ``model`` keeps a searched tool loaded only while
    the search that loaded it is in the input (``carried_tool_names``): its
    provider declares the Responses form for its API
    (``settings.tool_search_spec``)."""
    from openprogram.models.providers.settings.tool_search_spec import model_tool_search_form

    return model_tool_search_form(model) == "responses"


def native_tool_search(model: Any, context: "Context") -> bool:
    """Whether this request sends its searches natively: the model takes the
    Responses form and ``context`` offers the loader."""
    return loads_searched_tools_from_input(model) and any(
        getattr(t, "name", None) == TOOL_SEARCH_NAME for t in (context.tools or ()))


def _short_hash(s: str) -> str:
    """Fast deterministic hash to shorten long strings (mirrors shortHash in TS)."""
    h1 = 0xDEADBEEF
    h2 = 0x41C6CE57
    for ch in s:
        c = ord(ch)
        h1 = ((h1 ^ c) * 2654435761) & 0xFFFFFFFF
        h2 = ((h2 ^ c) * 1597334677) & 0xFFFFFFFF
    h1 = (((h1 ^ (h1 >> 16)) * 2246822507) & 0xFFFFFFFF) ^ (((h2 ^ (h2 >> 13)) * 3266489909) & 0xFFFFFFFF)
    h2 = (((h2 ^ (h2 >> 16)) * 2246822507) & 0xFFFFFFFF) ^ (((h1 ^ (h1 >> 13)) * 3266489909) & 0xFFFFFFFF)
    return format(h2 & 0xFFFFFFFF, "x") + format(h1 & 0xFFFFFFFF, "x")


# ---------------------------------------------------------------------------
# Message conversion
# ---------------------------------------------------------------------------

# Providers whose tool call IDs use the "callId|itemId" format
def convert_responses_messages(
    model: "Model",
    context: "Context",
    include_system_prompt: bool = True,
    native_tool_search: bool = False,
) -> list[dict[str, Any]]:
    """Convert internal messages to OpenAI Responses API input format.

    With ``native_tool_search`` a ``tool_search`` call and its result become
    the client-executed ``tool_search_call`` / ``tool_search_output`` pair,
    the output carrying the loaded tools' definitions.
    """
    from openprogram.models.providers.shared.transform_messages import transform_messages

    # transform_messages calls this with 3 args (id, model, msg) per its
    # NormalizeToolCallIdFn signature, and ONLY for cross-model replay —
    # same-model turns keep their native ids untouched. Every Responses
    # endpoint (official or custom relay) caps call_id at 64 chars, so
    # normalize unconditionally: a foreign id is meaningless to the
    # target upstream anyway.
    def normalize_tool_call_id(id_: str, _model=None, _msg=None) -> str:
        import re
        if "|" not in id_:
            # Single foreign id (Anthropic toolu_..., synthetic, ...):
            # pass through when it fits, otherwise hash it
            # deterministically so the call/result pair stays matched.
            if len(id_) <= 64 and re.match(r"^[a-zA-Z0-9_-]+$", id_):
                return id_
            import hashlib
            return "tc_" + hashlib.sha256(id_.encode()).hexdigest()[:60]
        call_id, item_id_raw = id_.split("|", 1)
        sanitized_call = re.sub(r"[^a-zA-Z0-9_-]", "_", call_id)
        sanitized_item = re.sub(r"[^a-zA-Z0-9_-]", "_", item_id_raw)
        if not sanitized_item.startswith("fc"):
            sanitized_item = f"fc_{sanitized_item}"
        sanitized_call = sanitized_call[:64].rstrip("_")
        sanitized_item = sanitized_item[:64].rstrip("_")
        return f"{sanitized_call}|{sanitized_item}"

    messages: list[dict[str, Any]] = []
    if include_system_prompt and context.system_prompt:
        role = "developer" if getattr(model, "reasoning", None) else "system"
        messages.append({"role": role, "content": sanitize_surrogates(context.system_prompt)})

    transformed = transform_messages(context.messages, model, normalize_tool_call_id)
    # call_ids of the tool_search calls sent as tool_search_call items; their
    # results go as tool_search_output, never as function_call_output.
    native_search_calls: set[str] = set()

    for msg_index, msg in enumerate(transformed):
        role = getattr(msg, "role", None)

        if role == "user":
            if native_tool_search:
                # A summary rendered as user input carries tools too.
                messages.extend(_carried_search(msg, model))
            content_val = msg.content
            if isinstance(content_val, str):
                messages.append({
                    "role": "user",
                    "content": [{"type": "input_text", "text": sanitize_surrogates(content_val)}],
                })
            else:
                content_items: list[dict[str, Any]] = []
                for item in content_val:
                    item_type = getattr(item, "type", None)
                    if item_type == "text":
                        content_items.append({"type": "input_text", "text": sanitize_surrogates(item.text)})
                    elif item_type == "image":
                        content_items.append({
                            "type": "input_image",
                            "detail": "auto",
                            "image_url": f"data:{item.mime_type};base64,{item.data}",
                        })
                if "image" not in (model.input or []):
                    content_items = [c for c in content_items if c["type"] != "input_image"]
                if not content_items:
                    continue
                messages.append({"role": "user", "content": content_items})

        elif role == "assistant":
            if native_tool_search:
                messages.extend(_carried_search(msg, model))
            output: list[dict[str, Any]] = []
            # A turn from any OTHER model (different id, provider or api)
            # carries fc_ item ids the target upstream never issued —
            # replaying them risks "Item not found". Only a same-model
            # turn keeps its item id.
            is_foreign_turn = not (
                msg.model == model.id
                and msg.provider == model.provider
                and msg.api == model.api
            )

            for block in msg.content:
                block_type = getattr(block, "type", None)
                if block_type == "thinking":
                    sig = getattr(block, "thinking_signature", None)
                    if sig:
                        try:
                            reasoning_item = json.loads(sig)
                            # store=false responses don't persist items, so
                            # their server-assigned `id` won't resolve on the
                            # next call ("Item not found"). Strip it — the
                            # encrypted_content payload is self-contained.
                            if isinstance(reasoning_item, dict):
                                reasoning_item.pop("id", None)
                                # SDK output includes absent optional values as
                                # null; compatible input schemas may reject them.
                                for key in ("content", "encrypted_content", "status"):
                                    if reasoning_item.get(key) is None:
                                        reasoning_item.pop(key, None)
                            output.append(reasoning_item)
                        except (json.JSONDecodeError, TypeError):
                            pass
                elif block_type == "text":
                    msg_id = getattr(block, "text_signature", None)
                    if not msg_id:
                        msg_id = f"msg_{msg_index}"
                    elif len(msg_id) > 64:
                        msg_id = f"msg_{_short_hash(msg_id)}"
                    # NOTE: no "status" field here. `status` is an OUTPUT-only
                    # field; some Responses endpoints (notably openai-codex's
                    # /responses) reject it on an INPUT item with
                    # "Unknown parameter: input[N].status". An input
                    # message item never needs it.
                    output.append({
                        "type": "message",
                        "role": "assistant",
                        "content": [{
                            "type": "output_text",
                            "text": sanitize_surrogates(getattr(block, "text", "")),
                            "annotations": [],
                        }],
                        "id": msg_id,
                    })
                elif block_type == "toolCall":
                    call_parts = block.id.split("|", 1)
                    call_id = call_parts[0]
                    item_id: str | None = call_parts[1] if len(call_parts) > 1 else None

                    if is_foreign_turn and item_id and item_id.startswith("fc_"):
                        item_id = None

                    if native_tool_search and block.name == TOOL_SEARCH_NAME:
                        # No item id: the backend matches the output by
                        # call_id, and an id from another turn may not resolve.
                        output.append({
                            "type": "tool_search_call",
                            "execution": "client",
                            "call_id": call_id,
                            "status": "completed",
                            "arguments": dict(getattr(block, "arguments", {}) or {}),
                        })
                        native_search_calls.add(call_id)
                        continue

                    fc: dict[str, Any] = {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": block.name,
                        "arguments": json.dumps(getattr(block, "arguments", {}) or {}),
                    }
                    if item_id is not None:
                        fc["id"] = item_id
                    output.append(fc)
                elif block_type == "serverToolUse":
                    # transform_messages already dropped blocks from another
                    # API. Resend the provider's own item; it is complete in
                    # itself and never pairs with a function_call_output.
                    raw = getattr(block, "raw", None)
                    if isinstance(raw, dict) and raw.get("type") == "web_search_call":
                        output.append(_drop_none(dict(raw)))
                        continue
                    query = (getattr(block, "input", None) or {}).get("query")
                    if not getattr(block, "id", None) or not query:
                        continue
                    output.append({
                        "type": "web_search_call",
                        "id": block.id,
                        "status": "completed",
                        "action": {"type": "search", "query": query},
                    })

            if not output:
                continue
            messages.extend(output)

        elif role == "toolResult":
            text_result = "\n".join(
                c.text for c in msg.content if getattr(c, "type", None) == "text"
            )
            has_images = any(getattr(c, "type", None) == "image" for c in msg.content)
            has_text = bool(text_result)

            call_id = msg.tool_call_id.split("|")[0]
            if call_id in native_search_calls:
                # The output carries the definitions the result's load record
                # stored when the tools were loaded (``shared.tool_loads``),
                # never the result text: the item is the same bytes on every
                # request however the text ages and whatever the tools'
                # schemas become. A tool the turn no longer offers is refused
                # when called, like any unknown tool.
                from openprogram.models.providers.shared.tool_loads import load_records, search_note
                from openprogram.models.providers.types import Tool as _Tool

                tools = [] if msg.is_error else [
                    _function_tool(_Tool(**entry["definition"]), model.api, model.id)
                    for entry in load_records(getattr(msg, "details", None))
                ]
                messages.append({
                    "type": "tool_search_output",
                    "execution": "client",
                    "call_id": call_id,
                    "status": "completed",
                    "tools": tools,
                })
                # The output has no place for words, and an empty list reads
                # as "no such tool". What the search has to say beyond its
                # definitions (the tool is already callable, the name is
                # unknown, nothing matched) follows as its own item, from
                # the note the result stored, so the bytes never change.
                note = search_note(getattr(msg, "details", None))
                if note:
                    messages.append({"role": "user", "content": [{
                        "type": "input_text",
                        "text": sanitize_surrogates(f"<{SEARCH_NOTE_TAG}>\n{note}\n</{SEARCH_NOTE_TAG}>"),
                    }]})
                continue
            messages.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": sanitize_surrogates(text_result if has_text else "(see attached image)"),
            })

            if has_images and "image" in (model.input or []):
                content_parts: list[dict[str, Any]] = [
                    {"type": "input_text", "text": "Attached image(s) from tool result:"}
                ]
                for block in msg.content:
                    if getattr(block, "type", None) == "image":
                        content_parts.append({
                            "type": "input_image",
                            "detail": "auto",
                            "image_url": f"data:{block.mime_type};base64,{block.data}",
                        })
                messages.append({"role": "user", "content": content_parts})

    return messages


# ---------------------------------------------------------------------------
# Tool conversion
# ---------------------------------------------------------------------------

def convert_responses_tools(
    tools: "list[Tool]",
    api: str | None = "openai-responses",
    model_id: str | None = None,
    *,
    native_tool_search: bool = False,
    carried: "set[str] | frozenset[str]" = frozenset(),
) -> list[dict[str, Any]]:
    """Convert tools to OpenAI Responses API function format.

    With ``native_tool_search`` the ``tool_search`` loader becomes the
    client-executed Responses ``tool_search`` tool, and a tool named in
    ``carried`` (one a ``tool_search_output`` in the input already loads)
    is left out, so loading it does not change the array.

    Schema dialect + the ``"strict"`` flag are both decided by the
    unified ``providers.schema`` layer keyed on ``(api, model_id)`` —
    the SAME path the Chat Completions builder uses, so the two no
    longer drift (previously this defaulted strict off ``strict_tools_enabled``
    while completions gated on ``api_wants_strict``). Callers thread
    their ``model.api`` / ``model.id``; the api default keeps old
    call-by-position working.
    """
    converted: list[dict[str, Any]] = []
    for t in tools:
        if native_tool_search:
            if t.name == TOOL_SEARCH_NAME:
                converted.append(_tool_search_tool(t, api, model_id))
                continue
            if t.name in carried:
                continue
        converted.append(_function_tool(t, api, model_id))
    return converted


def _function_tool(t: "Tool", api: str | None, model_id: str | None) -> dict[str, Any]:
    """One tool as a Responses function definition, in the tools array or
    in a ``tool_search_output`` alike."""
    from openprogram.models.providers.schema import normalize_for, wants_strict_flag

    return {
        "type": "function",
        "name": t.name,
        "description": t.description,
        "parameters": normalize_for(api, t.parameters, model_id),
        "strict": wants_strict_flag(api, model_id, t.parameters),
    }


# ---------------------------------------------------------------------------
# Stream processing
# ---------------------------------------------------------------------------

async def process_responses_stream(
    openai_stream: Any,
    output: "AssistantMessage",
    stream: "EventStream",
    model: "Model",
    service_tier: str | None = None,
    apply_service_tier_pricing: Any | None = None,
    signal: Any | None = None,
) -> None:
    """Process an OpenAI Responses API stream into our event stream format.

    Blocks are located by the event's ``output_index`` (stored as
    ``"index"`` on each block, popped when the item completes — the
    amazon_bedrock pattern), NOT by a single "current block" cursor.
    With parallel tool calls the API interleaves ``output_item.added``
    idx0/idx1 with their ``function_call_arguments.delta`` events; a
    cursor routed idx0's deltas onto idx1's block and both tool calls
    finalized with empty arguments.

    ``signal`` is the caller's cancel event (``SimpleStreamOptions.signal``);
    when it is set mid-stream we raise :class:`StreamAborted` so the
    provider can finalize the turn as ``stop_reason="aborted"``.
    """
    from openprogram.models.providers.catalog.models import calculate_cost
    from openprogram.models.providers.utils.failures.errors import StreamAborted

    blocks = output.content
    refusal_seen = False

    def _get(ev: Any, name: str, default: Any = None) -> Any:
        return ev.get(name, default) if isinstance(ev, dict) else getattr(ev, name, default)

    def _find(idx: Any, want_type: str) -> int:
        """Index into ``blocks`` of the open block for output item ``idx``.

        Falls back to the most recent still-open block of ``want_type``
        when the event carries no ``output_index`` (older event shapes).
        A block is "open" while its ``"index"`` key is present.
        """
        if idx is not None:
            i = next(
                (i for i, b in enumerate(blocks)
                 if isinstance(b, dict) and b.get("index") == idx),
                -1,
            )
            if i >= 0:
                return i
        for i in range(len(blocks) - 1, -1, -1):
            b = blocks[i]
            if isinstance(b, dict) and b.get("type") == want_type and "index" in b:
                return i
        return -1

    def _apply_response_terminal(response: Any) -> None:
        resp_dict = response if isinstance(response, dict) else response.__dict__
        usage_raw = resp_dict.get("usage")
        if usage_raw:
            usage_dict = usage_raw if isinstance(usage_raw, dict) else usage_raw.__dict__
            input_tokens = usage_dict.get("input_tokens", 0) or 0
            output_tokens = usage_dict.get("output_tokens", 0) or 0
            total_tokens = usage_dict.get("total_tokens", 0) or 0
            details = usage_dict.get("input_tokens_details") or {}
            details_dict = details if isinstance(details, dict) else details.__dict__
            cached = details_dict.get("cached_tokens", 0) or 0

            ticks = usage_dict.get("cost_in_usd_ticks")
            if isinstance(ticks, (int, float)) and ticks >= 0:
                output.usage.provider_cost_usd = ticks / 10_000_000_000
            output.usage.input = input_tokens - cached
            output.usage.output = output_tokens
            output.usage.cache_read = cached
            output.usage.cache_write = 0
            output.usage.total_tokens = total_tokens
            output.usage.tokens_reported = all(usage_dict.get(key) is not None for key in ("input_tokens", "output_tokens"))

        output.usage.requested_service_tier = service_tier
        output.usage.service_tier = resp_dict.get("service_tier")
        calculate_cost(model, output.usage)

        if apply_service_tier_pricing:
            tier = resp_dict.get("service_tier") or service_tier
            apply_service_tier_pricing(output.usage, tier)

        details = resp_dict.get("incomplete_details")
        details_dict = details if isinstance(details, dict) else getattr(details, "__dict__", {})
        incomplete_reason = details_dict.get("reason")
        if refusal_seen or incomplete_reason == "content_filter":
            output.stop_reason = "error"
        elif incomplete_reason == "max_output_tokens":
            output.stop_reason = "length"
        else:
            output.stop_reason = _map_stop_reason(resp_dict.get("status"))
        if any(
            getattr(block, "type", None) == "toolCall"
            or (isinstance(block, dict) and block.get("type") == "toolCall")
            for block in output.content
        ) and output.stop_reason == "stop":
            output.stop_reason = "toolUse"

        output.content = _finalize_content_blocks(output.content)

    async for event in openai_stream:
        if signal is not None and callable(getattr(signal, "is_set", None)) and signal.is_set():
            raise StreamAborted("stream cancelled by caller signal")

        event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", None)

        if event_type == "response.output_item.added":
            item = _get(event, "item", {})
            item_dict = item if isinstance(item, dict) else (item.__dict__ if item else {})
            item_type = item_dict.get("type")
            out_idx = _get(event, "output_index")
            if out_idx is None:
                out_idx = len(blocks)

            if item_type == "reasoning":
                blocks.append({"type": "thinking", "thinking": "", "index": out_idx})
                stream.push({"type": "thinking_start", "content_index": len(blocks) - 1, "partial": output})

            elif item_type == "message":
                blocks.append({"type": "text", "text": "", "index": out_idx})
                stream.push({"type": "text_start", "content_index": len(blocks) - 1, "partial": output})

            elif item_type == "function_call":
                call_id = item_dict.get("call_id", "")
                item_id = item_dict.get("id", "")
                blocks.append({
                    "type": "toolCall",
                    "id": f"{call_id}|{item_id}",
                    "name": item_dict.get("name", ""),
                    "arguments": {},
                    "partial_json": item_dict.get("arguments", ""),
                    "index": out_idx,
                })
                stream.push({"type": "toolcall_start", "content_index": len(blocks) - 1, "partial": output})

            elif item_type == "tool_search_call":
                # A client-executed search is the loader tool's call: it runs
                # like any tool and is sent back as a tool_search_call.
                blocks.append({
                    "type": "toolCall",
                    "id": f"{item_dict.get('call_id', '')}|{item_dict.get('id', '')}",
                    "name": TOOL_SEARCH_NAME,
                    "arguments": {},
                    "index": out_idx,
                })
                stream.push({"type": "toolcall_start", "content_index": len(blocks) - 1, "partial": output})

            elif item_type == "web_search_call":
                blocks.append({
                    "type": "serverToolUse",
                    "id": item_dict.get("id", "") or "",
                    "name": "web_search",
                    "input": _web_search_input(item_dict.get("action")),
                    "status": "searching",
                    "results": [],
                    "index": out_idx,
                })
                stream.push({"type": "servertool_start", "content_index": len(blocks) - 1, "partial": output})

        elif event_type in (
            "response.web_search_call.in_progress",
            "response.web_search_call.searching",
            "response.web_search_call.completed",
        ):
            i = _find(_get(event, "output_index"), "serverToolUse")
            if i >= 0 and blocks[i].get("type") == "serverToolUse":
                blocks[i]["status"] = (
                    "completed" if event_type.endswith(".completed") else "searching"
                )

        elif event_type == "response.reasoning_summary_text.delta":
            i = _find(_get(event, "output_index"), "thinking")
            if i >= 0 and blocks[i].get("type") == "thinking":
                delta = _get(event, "delta", "")
                blocks[i]["thinking"] = blocks[i].get("thinking", "") + delta
                stream.push({"type": "thinking_delta", "content_index": i, "delta": delta, "partial": output})

        elif event_type == "response.reasoning_summary_part.done":
            i = _find(_get(event, "output_index"), "thinking")
            if i >= 0 and blocks[i].get("type") == "thinking":
                blocks[i]["thinking"] = blocks[i].get("thinking", "") + "\n\n"
                stream.push({"type": "thinking_delta", "content_index": i, "delta": "\n\n", "partial": output})

        elif event_type in ("response.output_text.delta", "response.refusal.delta"):
            i = _find(_get(event, "output_index"), "text")
            if i >= 0 and blocks[i].get("type") == "text":
                delta = _get(event, "delta", "")
                blocks[i]["text"] = blocks[i].get("text", "") + delta
                stream.push({"type": "text_delta", "content_index": i, "delta": delta, "partial": output})

        elif event_type == "response.function_call_arguments.delta":
            i = _find(_get(event, "output_index"), "toolCall")
            if i >= 0 and blocks[i].get("type") == "toolCall":
                delta = _get(event, "delta", "")
                blocks[i]["partial_json"] = blocks[i].get("partial_json", "") + delta
                blocks[i]["arguments"] = parse_streaming_json(blocks[i]["partial_json"])
                stream.push({"type": "toolcall_delta", "content_index": i, "delta": delta, "partial": output})

        elif event_type == "response.function_call_arguments.done":
            i = _find(_get(event, "output_index"), "toolCall")
            if i >= 0 and blocks[i].get("type") == "toolCall":
                args_str = _get(event, "arguments", "")
                blocks[i]["partial_json"] = args_str or ""
                blocks[i]["arguments"] = parse_streaming_json(blocks[i]["partial_json"])

        elif event_type == "response.output_item.done":
            item = _get(event, "item", {})
            item_dict = _responses_item_dict(item)
            item_type = item_dict.get("type")
            out_idx = _get(event, "output_index")

            if item_type == "reasoning":
                i = _find(out_idx, "thinking")
                if i >= 0 and blocks[i].get("type") == "thinking":
                    block = blocks[i]
                    summary = item_dict.get("summary") or []
                    thinking_text = "\n\n".join(s.get("text", "") if isinstance(s, dict) else getattr(s, "text", "") for s in summary).strip()
                    # Codex returns the DONE reasoning item with encrypted_content
                    # and an EMPTY summary array — the readable text only arrived
                    # via reasoning_summary_text.delta. Overwriting unconditionally
                    # clobbers that accumulated text to "", the thinking block then
                    # fails the `if _t:` guard at persist time, and neither the
                    # live blocks nor the DAG node ever carry the thinking — the
                    # UI can't collapse it into the "Thinking ×1" strip. Keep the
                    # deltas when the summary is empty.
                    if thinking_text:
                        block["thinking"] = thinking_text
                    block["thinking_signature"] = _json_dumps(item_dict)
                    block.pop("index", None)
                    stream.push({"type": "thinking_end", "content_index": i, "content": block.get("thinking", ""), "partial": output})

            elif item_type == "message":
                refusal_seen = any(
                    (
                        c.get("type") == "refusal" or bool(c.get("refusal"))
                        if isinstance(c, dict)
                        else getattr(c, "type", None) == "refusal"
                        or bool(getattr(c, "refusal", None))
                    )
                    for c in (item_dict.get("content") or [])
                ) or refusal_seen
                i = _find(out_idx, "text")
                if i >= 0 and blocks[i].get("type") == "text":
                    block = blocks[i]
                    item_content = item_dict.get("content") or []
                    text = "".join(
                        (c.get("text", "") if isinstance(c, dict) else getattr(c, "text", ""))
                        + (c.get("refusal", "") if isinstance(c, dict) else getattr(c, "refusal", ""))
                        for c in item_content
                    )
                    block["text"] = text
                    block["text_signature"] = item_dict.get("id", "")
                    citations = _url_citations(item_content)
                    if citations:
                        block["citations"] = citations
                    block.pop("index", None)
                    stream.push({"type": "text_end", "content_index": i, "content": text, "partial": output})

            elif item_type == "function_call":
                i = _find(out_idx, "toolCall")
                if i >= 0 and blocks[i].get("type") == "toolCall":
                    # Mutate the block in output.content so the finalized
                    # message carries the parsed args, not the empty stub
                    # from response.output_item.added.
                    block = blocks[i]
                    args_raw = block.get("partial_json", "") or item_dict.get("arguments", "{}")
                    args = parse_streaming_json(args_raw)
                    block["arguments"] = args
                    block.pop("partial_json", None)
                    block.pop("index", None)
                    tool_call = {
                        "type": "toolCall",
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "arguments": args,
                    }
                    stream.push({"type": "toolcall_end", "content_index": i, "tool_call": tool_call, "partial": output})
                else:
                    args = parse_streaming_json(item_dict.get("arguments", "{}"))
                    tool_call = {
                        "type": "toolCall",
                        "id": f"{item_dict.get('call_id', '')}|{item_dict.get('id', '')}",
                        "name": item_dict.get("name", ""),
                        "arguments": args,
                    }
                    stream.push({"type": "toolcall_end", "content_index": len(blocks) - 1, "tool_call": tool_call, "partial": output})

            elif item_type == "tool_search_call":
                raw_args = item_dict.get("arguments")
                args = (raw_args if isinstance(raw_args, dict)
                        else parse_streaming_json(raw_args or "{}"))
                if not isinstance(args, dict):
                    args = {}
                i = _find(out_idx, "toolCall")
                if i < 0 or blocks[i].get("type") != "toolCall":
                    blocks.append({"type": "toolCall", "name": TOOL_SEARCH_NAME, "arguments": {}})
                    i = len(blocks) - 1
                    stream.push({"type": "toolcall_start", "content_index": i, "partial": output})
                block = blocks[i]
                block["id"] = f"{item_dict.get('call_id', '')}|{item_dict.get('id', '')}"
                block["arguments"] = args
                block.pop("index", None)
                tool_call = {"type": "toolCall", "id": block["id"],
                             "name": TOOL_SEARCH_NAME, "arguments": args}
                stream.push({"type": "toolcall_end", "content_index": i, "tool_call": tool_call, "partial": output})

            elif item_type == "web_search_call":
                i = _find(out_idx, "serverToolUse")
                if i < 0 or blocks[i].get("type") != "serverToolUse":
                    blocks.append({
                        "type": "serverToolUse",
                        "id": item_dict.get("id", "") or "",
                        "name": "web_search",
                        "input": {},
                        "status": "searching",
                        "results": [],
                    })
                    i = len(blocks) - 1
                    stream.push({"type": "servertool_start", "content_index": i, "partial": output})
                block = blocks[i]
                action = item_dict.get("action")
                status = item_dict.get("status")
                block["status"] = "failed" if status in ("failed", "incomplete") else "completed"
                block["input"] = _web_search_input(action) or block.get("input") or {}
                block["results"] = _web_search_sources(action)
                if item_dict.get("id"):
                    block["id"] = item_dict["id"]
                block["raw"] = item_dict
                block.pop("index", None)
                server_tool = {k: v for k, v in block.items() if k != "index"}
                stream.push({"type": "servertool_end", "content_index": i, "server_tool": server_tool, "partial": output})

        elif event_type in ("response.completed", "response.incomplete"):
            # Items that never saw output_item.done keep no bookkeeping key.
            for b in blocks:
                if isinstance(b, dict):
                    b.pop("index", None)
            response = event.get("response") if isinstance(event, dict) else getattr(event, "response", None)
            if response:
                _apply_response_terminal(response)

        elif event_type == "error":
            code = event.get("code") if isinstance(event, dict) else getattr(event, "code", "")
            msg_text = event.get("message") if isinstance(event, dict) else getattr(event, "message", "Unknown error")
            # Empty error event (both code and message null) is a transient
            # backend hiccup — codex intermittently emits these mid-stream on
            # large / tool-bearing requests while small requests succeed.
            # Surface it as a RETRYABLE stream error so retry_stream backs off
            # and tries again (which usually succeeds), instead of either a
            # bare RuntimeError (treated non-retryable -> killed the run) or a
            # doomed no-backoff storm. A real error (with a code/message) stays
            # a hard failure.
            if not code and not msg_text:
                from openprogram.models.providers.utils.streams.stream_retry import ProviderStreamError
                raise ProviderStreamError(
                    "empty error event (transient backend hiccup)",
                    retryable=not bool(getattr(output, "content", None)),
                    # This failure is time-windowed — give the backend a
                    # longer floor before retrying (retry_after_s is honored
                    # as a lower bound by stream_backoff_seconds) so we probe
                    # a recovered window instead of hammering the bad one.
                    retry_after_s=3.0,
                )
            raise RuntimeError(f"Error Code {code}: {msg_text}")

        elif event_type == "response.failed":
            raise RuntimeError("Unknown error")


def _finalize_content_blocks(blocks: list) -> list:
    """Coerce streaming-stage dict blocks into the Pydantic content variants."""
    from openprogram.models.providers.types import (
        ServerToolUse,
        TextContent,
        ThinkingContent,
        ToolCall,
    )

    finalized: list = []
    for b in blocks:
        if not isinstance(b, dict):
            finalized.append(b)
            continue
        btype = b.get("type")
        try:
            if btype == "text":
                finalized.append(TextContent.model_validate(b))
            elif btype == "thinking":
                finalized.append(ThinkingContent.model_validate(b))
            elif btype == "toolCall":
                args = b.get("arguments")
                if not isinstance(args, dict):
                    # parse_streaming_json can yield non-dict for malformed input;
                    # ToolCall schema requires a dict, so fall back to empty.
                    b = {**b, "arguments": {}}
                b.pop("partial_json", None)
                finalized.append(ToolCall.model_validate(b))
            elif btype == "serverToolUse":
                finalized.append(ServerToolUse.model_validate(
                    {k: v for k, v in b.items() if k != "index"}
                ))
            else:
                finalized.append(b)
        except Exception:
            finalized.append(b)
    return finalized


def _map_stop_reason(status: str | None) -> "StopReason":
    if not status:
        return "stop"
    mapping: dict[str, "StopReason"] = {
        "completed": "stop",
        "incomplete": "length",
        "failed": "error",
        "cancelled": "error",
        "in_progress": "stop",
        "queued": "stop",
    }
    return mapping.get(status, "stop")

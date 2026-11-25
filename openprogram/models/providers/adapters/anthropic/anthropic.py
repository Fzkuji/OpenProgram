"""
Anthropic Messages API provider — mirrors packages/ai/src/providers/anthropic.ts

Full parity including:
- API-key authentication
- Cache control retention (ephemeral / 1h)
- Beta headers: fine-grained-tool-streaming + interleaved-thinking
- sanitize_surrogates on all text content
- Empty content block filtering
- Usage capture from message_start event (not just end)
- All stop reasons: pause_turn, sensitive, refusal
"""
from __future__ import annotations

import re
import time
from typing import Any, AsyncGenerator, Optional


from ...types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    Citation,
    EventDone,
    EventError,
    EventServerToolEnd,
    EventServerToolStart,
    EventStart,
    EventTextDelta,
    EventTextEnd,
    EventTextStart,
    EventThinkingDelta,
    EventThinkingEnd,
    EventThinkingStart,
    EventToolCallDelta,
    EventToolCallEnd,
    EventToolCallStart,
    ImageContent,
    Model,
    ServerToolUse,
    SimpleStreamOptions,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from ...utils.streams.cancelable_stream import iter_until_cancelled
from ...utils.streams.json_parse import parse_streaming_json
from ...utils.sanitize_unicode import sanitize_surrogates
from ...shared.transform_messages import transform_messages as _transform_messages
from ...shared.tool_attachments import AttachmentMessage
from ...shared.validate_modalities import validate_input_modalities


def _sdk():
    """The ``anthropic`` SDK, imported when a client is first built.

    The provider catalog imports this module in every process to register
    the provider; the SDK takes 0.4 s to import and is used only by a
    request to this provider, so a command or a turn on another provider
    does not pay for it.
    """
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - base dependency, normally present
        raise ImportError(
            "The 'anthropic' Python SDK is required to use the Anthropic "
            "provider. Reinstall the complete OpenProgram release."
        ) from exc
    return anthropic

# Anthropic beta features
_BETA_FINE_GRAINED = "fine-grained-tool-streaming-2025-05-14"
_BETA_INTERLEAVED = "interleaved-thinking-2025-05-14"
# 1M context window — opt-in via the model id's ``[1m]`` suffix.
#
# CRITICAL: use ``context-management-2025-06-27``, NOT the older
# ``context-1m-2025-08-07``. They route the SAME 1M window through
# DIFFERENT billing paths:
#   * context-1m-2025-08-07  → the API pay-as-you-go path. On a Claude
#     SUBSCRIPTION this bills against usage credits (real money) and 429s
#     "Usage credits are required for long context requests" once the
#     credit limit is hit.
#   * context-management-2025-06-27 → the path the official Claude Code
#     CLI sends (verified by capturing its requests). On a subscription
#     the 1M window is included — it runs even with usage credits
#     exhausted, no per-token surcharge.
# Sending the wrong one is what billed $1.98 for a single 1M probe.
_BETA_CONTEXT_1M = "context-management-2025-06-27"
# Claude 的高速档（Opus 4.6+）：请求体 speed:"fast" + 本 beta 头。订阅
# 账户没充 usage credits 时 Anthropic 会 429（"Usage credits are
# required for fast mode"）——如实透传给界面，不代表模型不支持。
_BETA_FAST = "fast-mode-2026-02-01"
# Loading a searched tool by ``tool_reference`` (the "anthropic-reference"
# form, settings/tool_search_spec.py). Claude Code sends the first on the
# first-party API and the second on Vertex and Bedrock.
_BETA_TOOL_SEARCH = "advanced-tool-use-2025-11-20"
_BETA_TOOL_SEARCH_3P = "tool-search-tool-2025-10-19"
#: The text beside a result made only of tool_reference blocks: a user
#: message may not end in one (Claude Code ``messages.ts``). Sent on every
#: request, so the bytes never change once sent.
TOOL_LOADED_TEXT = "Tool loaded."
_TOOL_SEARCH = "tool_search"


def _wants_1m(model_id: str) -> bool:
    """True when the model id carries the ``[1m]`` opt-in suffix."""
    return "[1m]" in (model_id or "")


def _strip_1m(model_id: str) -> str:
    """The bare model id the Anthropic API expects — without ``[1m]``."""
    return (model_id or "").replace("[1m]", "")
# Strict tool use (grammar-constrained tool inputs). Requires this beta
# header + a recent model (Sonnet 4.5+ / Opus 4.1+ / Haiku 4.5+);
# enables the SAME schema subset as OpenAI strict mode.
_BETA_STRUCTURED_OUTPUTS = "structured-outputs-2025-11-13"


def _anthropic_strict_on(model: "Model", is_oauth: bool) -> bool:
    """Whether to use Anthropic strict tool use for this request.

    Gated three ways: the global ``OPENPROGRAM_STRICT_TOOLS`` toggle +
    model version (Sonnet 4.5+/Opus 4.1+/Haiku 4.5+) — both via the
    unified ``schema.wants_strict_flag`` — and API-key authentication.
    """
    if is_oauth:
        return False
    from openprogram.models.providers.schema import wants_strict_flag
    return wants_strict_flag(getattr(model, "api", None), getattr(model, "id", None))


def _observe_api_key_limits(model: Any, ant_stream: Any, *, is_oauth: bool,
                            base_url: Optional[str], account: Optional[str], api_key: str) -> None:
    """Keep the ``anthropic-ratelimit-*`` headroom of an official API-key reply.

    Only the official API with an API key: a sign-in token's limits and an
    Anthropic-compatible host's headers are not read.
    """
    if is_oauth or model.provider != "anthropic":
        return
    host = (base_url or "https://api.anthropic.com").rstrip("/")
    if host not in ("https://api.anthropic.com", "https://api.anthropic.com/v1"):
        return
    response = getattr(ant_stream, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return
    from openprogram.models.usage.quota import observe_headers
    observe_headers(model.provider, api_key, headers, account=account)


def _is_oauth_token(api_key: str) -> bool:
    """Check if the API key is an OAuth token (sk-ant-oat prefix)."""
    return "sk-ant-oat" in api_key


def _sanitize_surrogates(text: str) -> str:
    """
    Remove lone surrogate characters that would cause JSON encoding failures.
    Mirrors sanitizeSurrogates() in TypeScript.
    """
    # Replace lone surrogates (U+D800–U+DFFF) with U+FFFD
    return re.sub(r"[\ud800-\udfff]", "\ufffd", text)



# Stop reason mapping from Anthropic to pi_ai (matches TS exactly)
_STOP_REASON_MAP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "tool_use": "toolUse",
    "pause_turn": "stop",
    "sensitive": "error",
    "refusal": "error",
    "stop_sequence": "stop",
}


def _normalize_tool_call_id(id_: str, model: Model, source: AssistantMessage) -> str:
    """Normalize tool call IDs for Anthropic (max 64 chars, alphanum + _ -)."""
    import re as _re
    if len(id_) <= 64 and _re.match(r"^[a-zA-Z0-9_-]+$", id_):
        return id_
    import hashlib
    return "tc_" + hashlib.sha256(id_.encode()).hexdigest()[:60]


def _supports_adaptive_thinking(model_id: str) -> bool:
    """Check if model supports adaptive thinking (Opus 4.6+ or Sonnet 4.6+)."""
    return (
        "opus-4-6" in model_id or "opus-4.6" in model_id
        or "sonnet-4-6" in model_id or "sonnet-4.6" in model_id
    )



def _get_cache_control(base_url: str | None, cache_retention: str | None = None) -> dict | None:
    """
    Build cache_control dict for Anthropic API.
    Uses ephemeral; 1h TTL only for an endpoint in cache.json's
    ``long_ttl_endpoints`` (api.anthropic.com) and long retention.
    TTL value + eligible endpoints come from the provider's cache.json spec
    (see providers/settings/cache_spec.py) rather than being hardcoded here.
    """
    from openprogram.models.providers.settings.cache_spec import get_cache_spec, ttl_for_retention

    retention = cache_retention or "short"
    if retention == "none":
        return None
    spec = get_cache_spec("anthropic")
    ttl = ttl_for_retention("anthropic", retention)
    # Long TTL only applies on endpoints the spec marks eligible.
    if ttl is not None:
        endpoints = spec.get("long_ttl_endpoints") or []
        if not (base_url and any(e in base_url for e in endpoints)):
            ttl = None
    result: dict[str, Any] = {"type": "ephemeral"}
    if ttl:
        result["ttl"] = ttl
    return result


def _build_client(
    model: Model,
    api_key: str,
    interleaved_thinking: bool = True,
    options_headers: dict[str, str] | None = None,
    is_oauth_override: bool | None = None,
    base_url_override: str | None = None,
    fast: bool = False,
    tool_search: bool = False,
) -> tuple["anthropic.AsyncAnthropic", bool]:
    """
    Build the Anthropic async client with appropriate headers.
    Mirrors createClient() in TypeScript.

    ``is_oauth_override`` / ``base_url_override``: when the caller already
    resolved a :class:`~openprogram.auth.resolver.ResolvedConnection`, it
    knows definitively whether this is oauth/device_code/import and
    what base_url the credential carries — pass those through instead of
    re-deriving them here (sniffing the ``sk-ant-oat`` prefix is a fallback
    for callers that haven't gone through the resolver).

    Returns (client, is_oauth_token).
    """
    _anthropic = _sdk()
    from openprogram.models.auth.provider_policy import require_api_key
    require_api_key(api_key, is_subscription=bool(is_oauth_override))
    is_oauth = False
    base_url = base_url_override \
        or getattr(model, "base_url", None) or getattr(model, "baseUrl", None)
    model_headers = model.headers or {}

    # Adaptive thinking models don't use the interleaved-thinking beta (it's deprecated for them)
    needs_interleaved_beta = interleaved_thinking and not _supports_adaptive_thinking(model.id)

    beta_features = [_BETA_FINE_GRAINED]
    if needs_interleaved_beta:
        beta_features.append(_BETA_INTERLEAVED)
    # Strict tool use needs its own beta header. Only the regular
    # API-key path and recent models qualify.
    if _anthropic_strict_on(model, is_oauth):
        beta_features.append(_BETA_STRUCTURED_OUTPUTS)
    # 1M context — opt-in via the model id's [1m] suffix. The bare id is
    # sent in the request body (see _build_params); here we just flip the
    # beta header on.
    if _wants_1m(model.id):
        beta_features.append(_BETA_CONTEXT_1M)
    if fast:
        beta_features.append(_BETA_FAST)
    if tool_search:
        beta_features.append(_tool_search_beta(base_url))

    # The SDK's own retries are off: the credential layer
    # (``models/auth/selection.py``) and the stream's re-send handle every
    # failure once, so a 429 is not retried on the same key behind their back.
    sdk_max_retries = 0

    # Regular API key auth
    default_headers = {
        "accept": "application/json",
        "anthropic-beta": ",".join(beta_features),
        **model_headers,
        **(options_headers or {}),
    }
    from openprogram.models.auth.provider_policy import require_api_headers
    require_api_headers(default_headers)
    client = _anthropic.AsyncAnthropic(
        api_key=api_key,
        base_url=base_url,
        default_headers=default_headers,
        max_retries=sdk_max_retries,
        http_client=_shared_http_client(base_url),
    )

    return client, is_oauth


def _tool_search_beta(base_url: str | None) -> str:
    """The tool-search beta for this endpoint: Vertex takes the third-party
    name, the first-party API the other (``CC/utils/betas.ts``)."""
    host = (base_url or "").lower()
    if "aiplatform.googleapis.com" in host or "vertex" in host:
        return _BETA_TOOL_SEARCH_3P
    return _BETA_TOOL_SEARCH


def _uses_references(model: Model, context: Context) -> bool:
    """Whether this request loads searched tools by ``tool_reference``: the
    model takes the "anthropic-reference" form and the request offers the
    loader."""
    from openprogram.models.providers.settings.tool_search_spec import model_tool_search_form

    return model_tool_search_form(model) == "anthropic-reference" and any(
        getattr(t, "name", None) == _TOOL_SEARCH for t in (context.tools or ()))


def _reference_records(msg: Any) -> list[dict[str, Any]]:
    """The load records a tool result sends as ``tool_reference`` blocks: a
    ``tool_search`` result that loaded something. Any other result (no
    match, an error, a stopped call, a guide's old record) is sent as text."""
    if not isinstance(msg, ToolResultMessage) or msg.tool_name != _TOOL_SEARCH or msg.is_error:
        return []
    from openprogram.models.providers.shared.tool_loads import load_records

    return load_records(msg.details)


def _deferred_tools(context: Context) -> Context:
    """``context`` with the tools its searches loaded sent as deferred items.

    Each tool a ``tool_search`` result references is appended after the
    others with ``defer_loading``, in the order the history first loaded
    it, with the definition its load record kept: the array only grows at
    its end, and an entry never changes once sent. A tool a compaction
    summary carries stays an ordinary item (the first request after a
    summary is where the array may take a new shape).
    """
    from openprogram.models.providers.shared.tool_loads import carried_records
    from openprogram.models.providers.types import Tool as _Tool

    referenced: dict[str, dict[str, Any]] = {}
    carried: set[str] = set()
    for msg in context.messages:
        if getattr(msg, "role", None) in ("user", "assistant"):
            carried.update(entry["name"] for entry in carried_records(msg))
        for entry in _reference_records(msg):
            referenced.setdefault(entry["name"], entry["definition"])
    deferred = {name: definition for name, definition in referenced.items() if name not in carried}
    if not deferred:
        return context
    tools = [t for t in (context.tools or []) if t.name not in deferred]
    tools += [_Tool(name=name, description=definition.get("description") or "",
                    parameters=dict(definition.get("parameters") or {}), defer_loading=True)
              for name, definition in deferred.items()]
    return context.model_copy(update={"tools": tools})


def _shared_http_client(base_url: str | None = None):
    """A per-event-loop shared httpx client for the Anthropic SDK.

    Without this, ``AsyncAnthropic`` builds its OWN httpx client bound to
    whatever loop is current. The worker runs each turn in a SHORT-LIVED
    event loop (dispatcher creates one, runs the turn, then ``loop.close()``);
    a freshly-built SDK client's connection gets torn down when that loop
    closes — mid-stream — surfacing as "Task was destroyed but it is
    pending" + RuntimeError('Event loop is closed'), a half-sent request,
    and Anthropic 400ing it. ``get_shared_async_client`` caches per
    (name, loop), so the client's lifecycle matches the turn's loop exactly
    — the same fix that keeps openai-codex working under the worker.
    Policy and construction failures propagate so the SDK cannot fall back to
    its unmanaged default transport.
    """
    from openprogram.models.providers.utils.http_transport.http_client import get_shared_async_client
    from openprogram.host.security.url_policy import OwnerURLException, normalize_origin

    configured_origin = normalize_origin(base_url or "https://api.anthropic.com")
    return get_shared_async_client(
        "anthropic",
        consumer="provider.anthropic.sdk",
        configured_origin=configured_origin,
        owner_exception=OwnerURLException(
            consumer="provider.anthropic.sdk", origin=configured_origin
        ),
    )


def _convert_tool_result_block(tr_msg: ToolResultMessage, is_oauth: bool = False,
                               references: bool = False) -> dict[str, Any]:
    """Convert a single ToolResultMessage to an Anthropic tool_result block.

    With ``references`` a search that loaded tools is sent as
    ``tool_reference`` blocks only, never with text in the same result; its
    text stays on the node for the page and for a model without the form.
    """
    records = _reference_records(tr_msg) if references else []
    if records:
        return {
            "type": "tool_result",
            "tool_use_id": tr_msg.tool_call_id,
            "content": [{"type": "tool_reference", "tool_name": entry["name"]} for entry in records],
            "is_error": False,
        }
    cblocks: list[dict[str, Any]] = []
    for block in tr_msg.content:
        if isinstance(block, TextContent):
            text = sanitize_surrogates(block.text)
            cblocks.append({"type": "text", "text": text})
        elif isinstance(block, ImageContent):
            cblocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": block.mime_type,
                    "data": block.data,
                },
            })
    return {
        "type": "tool_result",
        "tool_use_id": tr_msg.tool_call_id,
        "content": cblocks,
        "is_error": tr_msg.is_error,
    }


# Provider-side web search (docs/reference/design/providers/requests/web-search.html).
_WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}


def _block_dict(value: Any) -> dict[str, Any]:
    """An SDK content block (or dict) as a plain dict, nulls dropped."""
    if isinstance(value, dict):
        data = value
    else:
        dump = getattr(value, "model_dump", None)
        data = dump(mode="json") if callable(dump) else dict(getattr(value, "__dict__", {}))
    return {k: v for k, v in data.items() if v is not None}


def _web_search_results(content: Any) -> tuple[list[dict[str, Any]], str]:
    """``([{"url", "title"}], status)`` from a web_search_tool_result's content."""
    if isinstance(content, list):
        results = []
        for item in content:
            item = _block_dict(item)
            if item.get("url"):
                results.append({"url": item["url"], "title": item.get("title") or item["url"]})
        return results, "completed"
    # {"type": "web_search_tool_result_error", "error_code": ...}
    return [], "failed"


def _server_tool_use_blocks(block: ServerToolUse) -> list[dict[str, Any]]:
    """Replay a provider-executed search as server_tool_use + its result.

    The result's search hits carry ``encrypted_content`` the API requires
    back verbatim, so only a block that kept the provider's own result
    (``raw``) can be replayed; without it the pair is dropped.
    """
    raw = block.raw if isinstance(block.raw, dict) else None
    if not block.id or raw is None or raw.get("type") != "web_search_tool_result":
        return []
    return [
        {
            "type": "server_tool_use",
            "id": block.id,
            "name": block.name or "web_search",
            "input": block.input or {},
        },
        {
            "type": "web_search_tool_result",
            "tool_use_id": block.id,
            "content": raw.get("content", []),
        },
    ]


def _build_messages(
    context: Context,
    is_oauth: bool = False,
    cache_control: dict | None = None,
    references: bool = False,
) -> list[dict[str, Any]]:
    """
    Convert Context messages to Anthropic API format.
    Batches consecutive toolResult messages into a single user message.
    ``references`` sends a search's loads as ``tool_reference`` blocks.
    """
    result: list[dict[str, Any]] = []
    all_msgs = context.messages
    i = 0

    while i < len(all_msgs):
        msg = all_msgs[i]

        if isinstance(msg, UserMessage):
            if isinstance(msg.content, str):
                text = _sanitize_surrogates(msg.content)
                if text.strip():
                    block: dict[str, Any] = {"type": "text", "text": text}
                    result.append({"role": "user", "content": [block]})
            else:
                content_blocks: list[dict[str, Any]] = []
                for block in msg.content:
                    if isinstance(block, TextContent):
                        text = sanitize_surrogates(block.text)
                        if text.strip():
                            tblock: dict[str, Any] = {"type": "text", "text": text}
                            if getattr(block, "cache_control", None):
                                tblock["cache_control"] = block.cache_control
                            content_blocks.append(tblock)
                    elif isinstance(block, ImageContent):
                        iblock: dict[str, Any] = {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": block.mime_type,
                                "data": block.data,
                            },
                        }
                        if getattr(block, "cache_control", None):
                            iblock["cache_control"] = block.cache_control
                        content_blocks.append(iblock)
                if content_blocks:
                    result.append({"role": "user", "content": content_blocks})

        elif isinstance(msg, AssistantMessage):
            content_blocks = []
            for block in msg.content:
                if isinstance(block, TextContent):
                    text = sanitize_surrogates(block.text)
                    if text:
                        content_blocks.append({"type": "text", "text": text})
                elif isinstance(block, ThinkingContent):
                    if getattr(block, "redacted", False):
                        # Redacted block: send back as redacted_thinking with opaque data
                        content_blocks.append({
                            "type": "redacted_thinking",
                            "data": block.thinking_signature or "",
                        })
                    elif not block.thinking.strip():
                        pass  # Skip empty thinking blocks
                    else:
                        content_blocks.append({
                            "type": "thinking",
                            "thinking": block.thinking,
                            "signature": block.thinking_signature or "",
                        })
                elif isinstance(block, ToolCall):
                    tc_name = block.name
                    content_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": tc_name,
                        "input": block.arguments,
                    })
                elif isinstance(block, ServerToolUse):
                    # Same-API blocks only (transform_messages drops the rest);
                    # never paired with a tool_result from the user side.
                    content_blocks.extend(_server_tool_use_blocks(block))
            if content_blocks:
                result.append({"role": "assistant", "content": content_blocks})

        elif isinstance(msg, ToolResultMessage):
            # Batch consecutive toolResult messages into a single user message
            tool_results: list[dict[str, Any]] = [_convert_tool_result_block(msg, is_oauth, references)]

            j = i + 1
            while j < len(all_msgs) and isinstance(all_msgs[j], ToolResultMessage):
                tool_results.append(_convert_tool_result_block(all_msgs[j], is_oauth, references))
                j += 1
            # A result sent as references alone drops its text; what the
            # search said beyond its loads goes beside it (``tool_loads.NOTE``).
            if references:
                from openprogram.models.providers.shared.tool_loads import search_note
                for sent in all_msgs[i:j]:
                    note = search_note(sent.details) if _reference_records(sent) else ""
                    if note:
                        tool_results.append({"type": "text", "text": sanitize_surrogates(note)})
            # The instructions those calls brought along go after the
            # results, in the same user message (``tool_attachments``).
            while j < len(all_msgs) and isinstance(all_msgs[j], AttachmentMessage):
                for block in all_msgs[j].content:
                    if isinstance(block, TextContent) and block.text.strip():
                        tblock = {"type": "text", "text": sanitize_surrogates(block.text)}
                        if getattr(block, "cache_control", None):
                            tblock["cache_control"] = block.cache_control
                        tool_results.append(tblock)
                j += 1
            if (any(_has_reference(block) for block in tool_results)
                    and not any(block.get("type") == "text" for block in tool_results)):
                # A message may not end in a result of references alone.
                tool_results.append({"type": "text", "text": TOOL_LOADED_TEXT})

            result.append({"role": "user", "content": tool_results})
            i = j
            continue

        i += 1

    result = _merge_adjacent_user_messages(result)
    _mark_final_message(result, cache_control)
    return result


def _merge_adjacent_user_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One user message for two that follow each other with no reply between.

    A reply stopped before it said anything is not sent (``context.session_dag.render``),
    so the person's next message follows the one it answered; the API wants
    the roles to alternate. The blocks keep their order; a message holding
    tool results is left as it is.
    """
    merged: list[dict[str, Any]] = []
    for message in messages:
        previous = merged[-1] if merged else None
        if (previous is not None and previous.get("role") == "user" and message.get("role") == "user"
                and not _has_tool_result(previous) and not _has_tool_result(message)):
            merged[-1] = {**previous, "content": [*previous["content"], *message["content"]]}
            continue
        merged.append(message)
    return merged


def _has_reference(block: Any) -> bool:
    return (isinstance(block, dict) and block.get("type") == "tool_result"
            and any(isinstance(item, dict) and item.get("type") == "tool_reference"
                    for item in block.get("content") or []))


def _has_tool_result(message: dict[str, Any]) -> bool:
    return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in message.get("content") or [])


# Anthropic rejects a request with more than four cache breakpoints.
_MAX_BREAKPOINTS = 4


def _mark_final_message(messages: list[dict[str, Any]], cache_control: dict | None) -> None:
    """Put the provider-default breakpoint on the last block of the request.

    The final message is a user turn: the person's message on the first
    round of a turn, a batch of tool results on every later round. Marking it
    every time caches the whole history, so the next round reads everything up
    to here and pays only for what it adds. A message the caller already
    marked keeps its own breakpoint."""
    if not cache_control or not messages or messages[-1].get("role") != "user":
        return
    content = messages[-1]["content"]
    if not content or any(isinstance(b, dict) and "cache_control" in b for b in content):
        return
    last = content[-1]
    if isinstance(last, dict) and last.get("type") in ("text", "image", "tool_result", "document"):
        content[-1] = {**last, "cache_control": cache_control}


def _cap_breakpoints(system: list[dict[str, Any]] | None, tools: list[dict[str, Any]] | None,
                     messages: list[dict[str, Any]]) -> None:
    """Make the breakpoints a request Anthropic accepts, whoever placed them.

    The request's own order is the cache prefix order (tools, system,
    messages). At most four breakpoints: the earliest message breakpoints are
    dropped first, so the final one, which covers the most, stays. A 1h
    breakpoint may not follow a 5m one: once a 5m breakpoint appears (a
    caller's mark, say), every later one takes the 5m lifetime too."""
    marked = [b for b in (tools or []) if "cache_control" in b]
    marked += [b for b in (system or []) if "cache_control" in b]
    in_messages = [b for m in messages for b in m.get("content") or []
                   if isinstance(b, dict) and "cache_control" in b]
    excess = len(marked) + len(in_messages) - _MAX_BREAKPOINTS
    for block in in_messages[:max(0, excess)]:
        if block is not in_messages[-1]:
            del block["cache_control"]
    short_seen = False
    for block in marked + in_messages:
        control = block.get("cache_control")
        if not control:
            continue
        if control.get("ttl") != "1h":
            short_seen = True
        elif short_seen:
            # A new dict: the caller's own cache_control stays untouched.
            block["cache_control"] = {k: v for k, v in control.items() if k != "ttl"}


def _build_tools(
    context: Context,
    is_oauth: bool = False,
    model: "Model | None" = None,
    strict: bool = False,
) -> list[dict[str, Any]] | None:
    """Convert Context tools to Anthropic API format.

    Schema goes through the unified ``_schema`` dialect layer: when
    ``strict`` is on (recent model + beta header set by the caller) it
    resolves to the ``openai_strict`` shape Anthropic strict tool use
    requires (additionalProperties:false + all-required), and each tool
    gets ``strict: true`` unless it contains dynamic mapping keys. Mappings
    retain their canonical schema without the strict flag.
    """
    if not context.tools:
        return None
    from openprogram.models.providers.schema import normalize_for, wants_strict_flag
    api = getattr(model, "api", None)
    mid = getattr(model, "id", None)
    tools = []
    for tool in context.tools:
        name = tool.name
        entry: dict[str, Any] = {
            "name": name,
            "description": tool.description,
            "input_schema": normalize_for(api, tool.parameters, mid),
        }
        if strict and wants_strict_flag(api, mid, tool.parameters):
            entry["strict"] = True
        if getattr(tool, "defer_loading", False):
            entry["defer_loading"] = True
        # Pass through a tool-level cache breakpoint (set by cache_policy or the
        # caller). Anthropic accepts cache_control on a tool definition.
        if getattr(tool, "cache_control", None):
            entry["cache_control"] = tool.cache_control
        tools.append(entry)
    return tools


def _build_system(
    context: Context,
    is_oauth: bool,
    cache_control: dict | None,
) -> list[dict[str, Any]] | None:
    """Build system prompt blocks for the Messages API."""
    blocks: list[dict[str, Any]] = []

    if context.system_prompt:
        sp_block: dict[str, Any] = {
            "type": "text",
            "text": _sanitize_surrogates(context.system_prompt),
        }
        if cache_control:
            sp_block["cache_control"] = cache_control
        blocks.append(sp_block)

    return blocks if blocks else None


async def stream_simple(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AsyncGenerator[AssistantMessageEvent, None]:
    """
    Stream a response from the Anthropic Messages API.
    Yields AssistantMessageEvents and stores the final AssistantMessage.
    Supports cache control and model capability headers.
    """
    opts = options or SimpleStreamOptions()

    validate_input_modalities(model, context)

    from openprogram.models.auth.provider_policy import require_supported_provider
    require_supported_provider(model.provider)
    # The credential this request goes out with, chosen by the credential
    # layer every adapter is wrapped in (``models/auth/selection.py``); its
    # own base URL and headers win over the catalogue's.
    _conn = getattr(opts, "credential", None)
    api_key = opts.api_key or ""
    if not api_key:
        # No credential anywhere — fail precisely instead of sending an
        # empty x-api-key header (a misleading upstream 401).
        from ...utils.failures.errors import ErrorReason, LLMError
        raise LLMError(
            message=(
                f"No API key configured for provider '{model.provider}'. "
                f"Add one in Settings → Model Services, or run: "
                f"openprogram providers keys {model.provider} add"
            ),
            reason=ErrorReason.AUTHENTICATION,
            retryable=False,
            provider=model.provider,
            model=model.id,
        )

    is_oauth = (_conn.kind in ("oauth", "device_code", "import")) if _conn else _is_oauth_token(api_key)
    base_url = (_conn.base_url if _conn and _conn.base_url else None) \
        or getattr(model, "base_url", None) or getattr(model, "baseUrl", None)
    from openprogram.models.providers.settings.cache_spec import with_lifetime_setting
    retention = with_lifetime_setting(getattr(opts, "cache_retention", None))
    cache_control = _get_cache_control(base_url, retention)

    _merged_headers = {**(getattr(opts, "headers", None) or {}), **(_conn.headers if _conn else {})}
    from ...shared.opencode_headers import opencode_headers
    _merged_headers.update(opencode_headers(model.provider, base_url, opts.session_id))
    # 高速档：composer 的 fast 开关随消息带 service_tier；模型声明了
    # fast 才透传（Claude 线上形态 = speed:"fast" + fast-mode beta 头）。
    _fast = getattr(opts, "service_tier", None) in ("priority", "fast") and bool(getattr(model, "fast", False))
    _references = _uses_references(model, context)
    client, is_oauth = _build_client(
        model, api_key,
        interleaved_thinking=True,
        fast=_fast,
        tool_search=_references,
        options_headers=_merged_headers or None,
        is_oauth_override=is_oauth,
        base_url_override=base_url,
    )

    # Transform messages for cross-provider compatibility
    transformed_msgs = _transform_messages(context.messages, model, _normalize_tool_call_id)
    transformed_context = Context(
        system_prompt=context.system_prompt,
        messages=transformed_msgs,
        tools=context.tools,
    )
    if _references:
        transformed_context = _deferred_tools(transformed_context)

    # Auto cache-breakpoint policy (opencode port): mark the last tool + latest
    # user message, preserving any caller-placed markers, within the 4-breakpoint
    # budget. cache_control above still covers the system block + last-block
    # default inside _build_messages/_build_system.
    if cache_control is not None:
        from openprogram.models.providers.settings.cache_policy import apply_cache_policy
        transformed_context = apply_cache_policy(
            transformed_context, "anthropic",
            # Same lifetime as the system and final breakpoints: a 1h marker
            # after a 5m one is rejected, and only some endpoints take 1h.
            ttl_seconds=3600 if cache_control.get("ttl") == "1h" else None,
        )

    messages = _build_messages(transformed_context, is_oauth=is_oauth, cache_control=cache_control,
                               references=_references)
    tools = _build_tools(
        transformed_context, is_oauth=is_oauth, model=model,
        strict=_anthropic_strict_on(model, is_oauth),
    )
    system = _build_system(transformed_context, is_oauth=is_oauth, cache_control=cache_control)
    _cap_breakpoints(system, tools, messages)

    max_tokens = opts.max_tokens if opts.max_tokens is not None else (model.max_tokens or 4096)

    params: dict[str, Any] = {
        # Strip the [1m] opt-in suffix — it's our marker, not a real id; the
        # 1M upgrade rides on the context-1m beta header set in _build_client.
        "model": _strip_1m(model.id),
        "messages": messages,
        "max_tokens": max_tokens,
        # Note: "stream": True is NOT passed to client.messages.stream() — the method itself streams
    }

    if _fast:
        # SDK 不认识 speed 这个字段，走 extra_body 直入请求体。
        params["extra_body"] = {"speed": "fast"}

    if system:
        params["system"] = system

    if opts.get("web_search"):
        tools = [*(tools or []), dict(_WEB_SEARCH_TOOL)]

    if tools:
        params["tools"] = tools
        # Caller-set pick policy, mapped to Anthropic's shapes:
        # "required" → {"type": "any"}; the framework's forced-pick
        # {"type": "function", "name": X} → {"type": "tool", "name": X};
        # "auto"/"none" → {"type": <verbatim>}. parallel_tool_calls=False
        # rides on the tool_choice object as disable_parallel_tool_use.
        tool_choice = opts.get("tool_choice")
        anthropic_choice = None
        if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
            anthropic_choice = {"type": "tool", "name": tool_choice.get("name")}
        elif tool_choice == "required":
            anthropic_choice = {"type": "any"}
        elif tool_choice in ("auto", "none"):
            anthropic_choice = {"type": tool_choice}
        if opts.get("parallel_tool_calls") is False:
            if anthropic_choice is None:
                anthropic_choice = {"type": "auto"}
            if anthropic_choice["type"] != "none":
                anthropic_choice["disable_parallel_tool_use"] = True
        if anthropic_choice is not None:
            params["tool_choice"] = anthropic_choice

    # Temperature is incompatible with extended thinking
    if opts.temperature is not None and not opts.reasoning:
        params["temperature"] = opts.temperature

    # Thinking configuration — reads from thinking.json via thinking_spec
    if opts.reasoning:
        from openprogram.models.providers.settings.thinking_spec import translate_reasoning, get_thinking_spec
        if _supports_adaptive_thinking(model.id):
            effort = translate_reasoning(model.provider or "anthropic", model.id, opts.reasoning)
            params["thinking"] = {"type": "adaptive"}
            params["output_config"] = {"effort": effort}
        else:
            spec = get_thinking_spec(model.provider or "anthropic")
            budget = (spec.get("budget_map") or {}).get(opts.reasoning, 8192)
            if hasattr(opts, "thinking_budgets") and opts.thinking_budgets:
                custom = getattr(opts.thinking_budgets, opts.reasoning, None)
                if custom is not None:
                    budget = custom
            params["thinking"] = {"type": "enabled", "budget_tokens": budget}
            if budget and max_tokens <= budget:
                raise ValueError("Output token limit must exceed the thinking budget")

    if opts.response_format is not None:
        params.setdefault("output_config", {})["format"] = {
            "type": "json_schema",
            "schema": opts.response_format.schema,
        }

    # Track partial state
    partial = AssistantMessage.empty(model)
    content_blocks: list[Any] = []
    block_index_map: dict[int, int] = {}  # anthropic index → content_blocks index
    tool_arg_buffers: dict[int, str] = {}
    server_arg_buffers: dict[int, str] = {}

    _signal = getattr(opts, "signal", None)

    def _cancelled() -> bool:
        f = getattr(_signal, "is_set", None)
        return bool(_signal is not None and callable(f) and f())

    # Apply on_payload callback (mirrors TS: const nextParams = await options?.onPayload?.(params, model))
    if opts.get("on_payload"):
        modified = opts["on_payload"](params, model)
        # Support async callbacks
        if hasattr(modified, "__await__"):
            modified = await modified
        if modified is not None:
            params = modified

    from ...recording.capture import ANTHROPIC, capture_request
    capture_request(model, params, wire=ANTHROPIC)

    yield EventStart(type="start", partial=partial)

    try:
        async with client.messages.stream(**params) as ant_stream:
            _observe_api_key_limits(model, ant_stream, is_oauth=is_oauth, base_url=base_url,
                                    account=_conn.credential_id if _conn else None, api_key=api_key)
            # <=250ms cancel poll — do not wait for the next token.
            async for event in iter_until_cancelled(ant_stream, _cancelled):
                # Dispatch on the SSE protocol field, NOT the Python class
                # name. SDK 0.91 renamed the high-level stop events
                # (ContentBlockStopEvent → ParsedContentBlockStopEvent), so a
                # __name__ match silently skipped content_block_stop — the
                # tool-arg buffer was never parsed back into ToolCall.arguments
                # and every streamed tool call arrived with arguments={}.
                # event.type ("message_start", "content_block_stop", ...) is
                # the wire-format name and stable across SDK versions; the
                # SDK's convenience events (TextEvent "text", InputJsonEvent
                # "input_json", ...) keep distinct types so nothing double-fires.
                event_type = getattr(event, "type", "") or ""

                if event_type == "message_start":
                    # Capture initial token counts from message_start
                    usage_data = getattr(event, "message", {})
                    if hasattr(usage_data, "usage"):
                        u = usage_data.usage
                        partial = partial.model_copy(update={
                            "usage": Usage(
                                input=getattr(u, "input_tokens", 0) or 0,
                                output=getattr(u, "output_tokens", 0) or 0,
                                cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                                cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0,
                            )
                        })

                elif event_type == "content_block_start":
                    block = event.content_block
                    ant_idx = event.index
                    cb_idx = len(content_blocks)
                    block_index_map[ant_idx] = cb_idx

                    if block.type == "text":
                        content_blocks.append(TextContent(type="text", text=""))
                        partial = partial.model_copy(update={"content": list(content_blocks)})
                        yield EventTextStart(type="text_start", content_index=cb_idx, partial=partial)

                    elif block.type == "thinking":
                        content_blocks.append(ThinkingContent(type="thinking", thinking=""))
                        partial = partial.model_copy(update={"content": list(content_blocks)})
                        yield EventThinkingStart(type="thinking_start", content_index=cb_idx, partial=partial)

                    elif block.type == "redacted_thinking":
                        # Opaque encrypted thinking block — preserve signature, no delta events
                        data = getattr(block, "data", "")
                        redacted_block = ThinkingContent(
                            type="thinking",
                            thinking="[Reasoning redacted]",
                            thinking_signature=data,
                            redacted=True,
                        )
                        content_blocks.append(redacted_block)
                        partial = partial.model_copy(update={"content": list(content_blocks)})
                        yield EventThinkingStart(type="thinking_start", content_index=cb_idx, partial=partial)
                        # Immediately emit end — no delta events for redacted blocks
                        yield EventThinkingEnd(type="thinking_end", content_index=cb_idx, content="[Reasoning redacted]", partial=partial)

                    elif block.type == "tool_use":
                        tc_name = block.name
                        tc = ToolCall(
                            type="toolCall",
                            id=block.id,
                            name=tc_name,
                            arguments={},
                        )
                        content_blocks.append(tc)
                        tool_arg_buffers[cb_idx] = ""
                        partial = partial.model_copy(update={"content": list(content_blocks)})
                        yield EventToolCallStart(type="toolcall_start", content_index=cb_idx, partial=partial)

                    elif block.type == "server_tool_use":
                        initial = getattr(block, "input", None)
                        stu = ServerToolUse(
                            id=block.id,
                            name=getattr(block, "name", None) or "web_search",
                            input=initial if isinstance(initial, dict) else {},
                            status="searching",
                        )
                        content_blocks.append(stu)
                        server_arg_buffers[cb_idx] = ""
                        partial = partial.model_copy(update={"content": list(content_blocks)})
                        yield EventServerToolStart(type="servertool_start", content_index=cb_idx, partial=partial)

                    elif block.type == "web_search_tool_result":
                        # Not a block of its own: it completes the matching
                        # server_tool_use, which then ends.
                        block_index_map.pop(ant_idx, None)
                        raw_result = _block_dict(block)
                        use_id = raw_result.get("tool_use_id")
                        target = next(
                            (i for i, b in enumerate(content_blocks)
                             if isinstance(b, ServerToolUse) and b.id == use_id),
                            -1,
                        )
                        if target < 0:
                            content_blocks.append(ServerToolUse(
                                id=use_id or "", name="web_search", status="searching",
                            ))
                            target = len(content_blocks) - 1
                            partial = partial.model_copy(update={"content": list(content_blocks)})
                            yield EventServerToolStart(type="servertool_start", content_index=target, partial=partial)
                        results, status = _web_search_results(raw_result.get("content"))
                        content_blocks[target] = content_blocks[target].model_copy(update={
                            "results": results, "status": status, "raw": raw_result,
                        })
                        partial = partial.model_copy(update={"content": list(content_blocks)})
                        yield EventServerToolEnd(
                            type="servertool_end",
                            content_index=target,
                            server_tool=content_blocks[target],
                            partial=partial,
                        )

                elif event_type == "content_block_delta":
                    delta = event.delta
                    ant_idx = event.index
                    cb_idx = block_index_map.get(ant_idx, -1)
                    if cb_idx < 0 or cb_idx >= len(content_blocks):
                        continue

                    if delta.type == "text_delta":
                        blk = content_blocks[cb_idx]
                        if isinstance(blk, TextContent):
                            text = _sanitize_surrogates(delta.text)
                            content_blocks[cb_idx] = blk.model_copy(update={"text": blk.text + text})
                            partial = partial.model_copy(update={"content": list(content_blocks)})
                            yield EventTextDelta(type="text_delta", content_index=cb_idx, delta=text, partial=partial)

                    elif delta.type == "thinking_delta":
                        blk = content_blocks[cb_idx]
                        if isinstance(blk, ThinkingContent):
                            content_blocks[cb_idx] = ThinkingContent(
                                type="thinking",
                                thinking=blk.thinking + delta.thinking,
                            )
                            partial = partial.model_copy(update={"content": list(content_blocks)})
                            yield EventThinkingDelta(type="thinking_delta", content_index=cb_idx, delta=delta.thinking, partial=partial)

                    elif delta.type == "citations_delta":
                        blk = content_blocks[cb_idx]
                        cit = _block_dict(getattr(delta, "citation", None) or {})
                        if isinstance(blk, TextContent) and cit.get("url"):
                            content_blocks[cb_idx] = blk.model_copy(update={"citations": [
                                *(blk.citations or []),
                                Citation(url=cit["url"], title=cit.get("title")),
                            ]})
                            partial = partial.model_copy(update={"content": list(content_blocks)})

                    elif delta.type == "input_json_delta" and cb_idx in server_arg_buffers:
                        server_arg_buffers[cb_idx] += delta.partial_json

                    elif delta.type == "input_json_delta":
                        if cb_idx in tool_arg_buffers:
                            tool_arg_buffers[cb_idx] += delta.partial_json
                            partial = partial.model_copy(update={"content": list(content_blocks)})
                            yield EventToolCallDelta(type="toolcall_delta", content_index=cb_idx, delta=delta.partial_json, partial=partial)

                    elif delta.type == "signature_delta":
                        blk = content_blocks[cb_idx]
                        if isinstance(blk, ThinkingContent):
                            sig = getattr(blk, "thinking_signature", "") or ""
                            content_blocks[cb_idx] = ThinkingContent(
                                type="thinking",
                                thinking=blk.thinking,
                                thinking_signature=sig + delta.signature,
                            )

                elif event_type == "content_block_stop":
                    ant_idx = event.index
                    cb_idx = block_index_map.get(ant_idx, -1)
                    if cb_idx < 0 or cb_idx >= len(content_blocks):
                        continue

                    blk = content_blocks[cb_idx]
                    if isinstance(blk, ServerToolUse):
                        raw_input = server_arg_buffers.pop(cb_idx, "")
                        if raw_input:
                            parsed_input = parse_streaming_json(raw_input)
                            if isinstance(parsed_input, dict):
                                content_blocks[cb_idx] = blk.model_copy(update={"input": parsed_input})
                                partial = partial.model_copy(update={"content": list(content_blocks)})
                        # servertool_end waits for its web_search_tool_result.
                    elif isinstance(blk, TextContent):
                        yield EventTextEnd(type="text_end", content_index=cb_idx, content=blk.text, partial=partial)
                    elif isinstance(blk, ThinkingContent):
                        yield EventThinkingEnd(type="thinking_end", content_index=cb_idx, content=blk.thinking, partial=partial)
                    elif isinstance(blk, ToolCall):
                        raw = tool_arg_buffers.get(cb_idx, "{}")
                        parsed = parse_streaming_json(raw) or {}
                        content_blocks[cb_idx] = ToolCall(
                            type="toolCall",
                            id=blk.id,
                            name=blk.name,
                            arguments=parsed,
                        )
                        partial = partial.model_copy(update={"content": list(content_blocks)})
                        yield EventToolCallEnd(
                            type="toolcall_end",
                            content_index=cb_idx,
                            tool_call=content_blocks[cb_idx],
                            partial=partial,
                        )

                elif event_type == "message_delta":
                    delta = getattr(event, "delta", None)
                    if delta:
                        stop_reason_raw = getattr(delta, "stop_reason", None)
                        if stop_reason_raw:
                            stop_reason = _STOP_REASON_MAP.get(stop_reason_raw, "stop")
                            partial = partial.model_copy(update={"stop_reason": stop_reason})

                    # Update usage if present
                    usage_update = getattr(event, "usage", None)
                    if usage_update:
                        cur = partial.usage
                        inp = getattr(usage_update, "input_tokens", None)
                        out = getattr(usage_update, "output_tokens", None)
                        cr = getattr(usage_update, "cache_read_input_tokens", None)
                        cw = getattr(usage_update, "cache_creation_input_tokens", None)
                        partial = partial.model_copy(update={
                            "usage": Usage(
                                input=inp if inp is not None else cur.input,
                                output=out if out is not None else cur.output,
                                cache_read=cr if cr is not None else cur.cache_read,
                                cache_write=cw if cw is not None else cur.cache_write,
                            )
                        })

            # Get final message from stream. Skipped when cancelled —
            # get_final_message() would drain the rest of the stream,
            # defeating the mid-stream break above.
            if _cancelled():
                usage = partial.usage
                stop_reason = partial.stop_reason
            else:
                try:
                    final_msg = await ant_stream.get_final_message()
                    u = final_msg.usage
                    usage = Usage(
                        input=u.input_tokens,
                        output=u.output_tokens,
                        cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                        cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0,
                    )
                    usage.total_tokens = usage.input + usage.output + usage.cache_read + usage.cache_write

                    stop_reason = _STOP_REASON_MAP.get(final_msg.stop_reason or "end_turn", "stop")
                except Exception:
                    usage = partial.usage
                    stop_reason = partial.stop_reason

            # Check cancellation
            if _cancelled():
                stop_reason = "aborted"

            usage.requested_service_tier = "priority" if _fast else None
            final = AssistantMessage(
                role="assistant",
                content=content_blocks,
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=usage,
                stop_reason=stop_reason,
                timestamp=int(time.time() * 1000),
            )

            # EventDone only accepts "stop", "length", "toolUse"
            # For "error" or "aborted", emit EventError instead
            if stop_reason in ("error", "aborted"):
                yield EventError(type="error", reason=stop_reason, error=final)
            else:
                yield EventDone(type="done", reason=stop_reason, message=final)

    except Exception as e:
        if not _cancelled():
            # Re-raise so the classification layer (_build_llm_error /
            # should_failover / retry) sees the real exception. Yielding
            # EventError here made agent_loop treat the failure as a normal
            # terminal message — the error was never classified, so
            # retry/failover could never trigger.
            raise
        # User cancel that surfaced as an exception (e.g. the connection
        # torn down mid-read) — finalize as "aborted", not an error.
        error_msg = AssistantMessage(
            role="assistant",
            content=content_blocks or [TextContent(type="text", text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=Usage(),
            stop_reason="aborted",
            error_message=str(e),
            timestamp=int(time.time() * 1000),
        )
        yield EventError(type="error", reason="aborted", error=error_msg)

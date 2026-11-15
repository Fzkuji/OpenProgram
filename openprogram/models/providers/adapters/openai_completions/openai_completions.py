"""
OpenAI Chat Completions API provider — mirrors packages/ai/src/providers/openai-completions.ts
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncGenerator

_log = logging.getLogger(__name__)

# Seconds to keep reading after finish_reason for a usage-only chunk.
USAGE_DRAIN_S = 1.5

# The ``openai`` SDK is a base dependency (installed by default). This
# guarded import only stays defensive so importing this module succeeds
# even in a stripped env and the provider *catalog* stays usable.
# Functions below re-check ``_openai`` and raise a clear error.
try:
    import openai as _openai
except ImportError:  # pragma: no cover — base dep, normally present
    _openai = None  # type: ignore[assignment]

from ...types import (
    AssistantMessage,
    AssistantMessageEvent,
    AudioContent,
    Context,
    EventDone,
    EventError,
    EventRetry,
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
    SimpleStreamOptions,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from ...utils.cancelable_stream import iter_until_cancelled
from ...utils.stream_idle import (
    StreamIdleTimeout,
    event_idle_timeout_s,
    idle_bounded,
    idle_bounded_open,
)
from ...utils.errors import classify_error
from ...utils.json_parse import parse_partial_json
from ...utils.stream_retry import PROVIDER_STREAM_MAX_ATTEMPTS, stream_backoff_seconds
from ...shared.transform_messages import transform_messages as _transform_messages
from ...shared.validate_modalities import validate_input_modalities


def _usage_from_chunk(u: Any) -> Usage:
    """Parse a completions-API usage payload into our Usage.

    Cached-prefix tokens arrive in two wire conventions — OpenAI-style
    ``prompt_tokens_details.cached_tokens`` and DeepSeek-style top-level
    ``prompt_cache_hit_tokens``. Both report ``prompt_tokens`` INCLUSIVE
    of the cached part, so split it out (same semantics as
    openai_responses.py). Reasoning tokens are already included in billable
    ``completion_tokens`` and must not be subtracted a second time.
    """
    usage = Usage(
        tokens_reported=all(getattr(u, key, None) is not None for key in ("prompt_tokens", "completion_tokens")),
        input=getattr(u, "prompt_tokens", 0) or 0,
        output=getattr(u, "completion_tokens", 0) or 0,
        total_tokens=getattr(u, "total_tokens", 0) or 0,
    )
    pd = getattr(u, "prompt_tokens_details", None)
    cached = (getattr(pd, "cached_tokens", 0) or 0) if pd else 0
    if not cached:
        cached = getattr(u, "prompt_cache_hit_tokens", 0) or 0
    if cached:
        usage.cache_read = cached
        usage.input = max(0, usage.input - cached)
    ticks = getattr(u, "cost_in_usd_ticks", None)
    if isinstance(ticks, (int, float)) and ticks >= 0:
        usage.provider_cost_usd = ticks / 10_000_000_000
    return usage


def _uses_developer_role(model: Model) -> bool:
    """Check if model uses 'developer' role instead of 'system'.

    Only OpenAI's own reasoning models take the system prompt as a
    'developer' message. Third-party OpenAI-compatible endpoints
    (deepseek, groq, ...) reject unknown roles with a 400
    (`unknown variant 'developer'`), which killed every turn that had
    a system prompt — i.e. exactly the turns where tools were enabled,
    since the tool inventory rides the system prompt.
    """
    if not getattr(model, "reasoning", False):
        return False
    provider = (getattr(model, "provider", "") or "").lower()
    base_url = (getattr(model, "base_url", "") or "").lower()
    return provider == "openai" or "api.openai.com" in base_url


def _uses_max_completion_tokens(model: Model) -> bool:
    """Check if model uses max_completion_tokens instead of max_tokens."""
    return bool(getattr(model, "reasoning", False))


def _completions_tool_call_id(id_: str) -> str:
    """Tool-call id as sent to a Chat Completions endpoint.

    History from the Responses path stores composite ids
    (``call_x|fc_y``); the ``fc_`` item-id half is Responses-only, so
    keep just the call half. Many OpenAI-compatible relays re-post the
    request to the Responses API upstream, which caps call_id at
    64 chars — anything still too long or with illegal characters is
    hashed deterministically, so the assistant/tool pair stays matched
    (both sides route through this same function).
    """
    import hashlib
    import re
    cid = id_.split("|")[0]
    if len(cid) <= 64 and re.match(r"^[a-zA-Z0-9_-]+$", cid):
        return cid
    return "tc_" + hashlib.sha256(cid.encode()).hexdigest()[:60]


def _build_messages(context: Context, model: Model) -> list[dict[str, Any]]:
    """Convert Context messages to OpenAI Chat Completions format."""
    result: list[dict[str, Any]] = []

    if context.system_prompt:
        role = "developer" if _uses_developer_role(model) else "system"
        result.append({"role": role, "content": context.system_prompt})

    # Tool messages carry text only, so a run's images and audio follow it as
    # one user message, each kind only when the model accepts it.
    accepts = set(model.input or ["text"])
    attached: list[dict[str, Any]] = []

    def flush() -> None:
        if attached:
            result.append({"role": "user", "content": [
                {"type": "text", "text": "Attached media from tool results:"}, *attached]})
            attached.clear()

    for msg in context.messages:
        if not isinstance(msg, ToolResultMessage):
            flush()
        if isinstance(msg, UserMessage):
            if isinstance(msg.content, str):
                if msg.content.strip():
                    result.append({"role": "user", "content": msg.content})
            else:
                content_blocks: list[dict[str, Any]] = []
                for block in msg.content:
                    if isinstance(block, TextContent):
                        if block.text.strip():
                            content_blocks.append({"type": "text", "text": block.text})
                    elif isinstance(block, ImageContent):
                        content_blocks.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{block.mime_type};base64,{block.data}",
                            },
                        })
                if content_blocks:
                    result.append({"role": "user", "content": content_blocks})

        elif isinstance(msg, AssistantMessage):
            tool_calls = [c for c in msg.content if isinstance(c, ToolCall)]
            text_parts = [c for c in msg.content if isinstance(c, TextContent)]
            text = " ".join(t.text for t in text_parts) if text_parts else None

            if tool_calls:
                tc_list = [
                    {
                        "id": _completions_tool_call_id(tc.id),
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in tool_calls
                ]
                entry: dict[str, Any] = {"role": "assistant", "tool_calls": tc_list}
                if text:
                    entry["content"] = text
                result.append(entry)
            else:
                result.append({"role": "assistant", "content": text or ""})

        elif isinstance(msg, ToolResultMessage):
            content_text = " ".join(
                b.text for b in msg.content if isinstance(b, TextContent)
            )
            result.append({
                "role": "tool",
                "tool_call_id": _completions_tool_call_id(msg.tool_call_id),
                "content": content_text,
            })
            for block in msg.content:
                if isinstance(block, ImageContent) and "image" in accepts:
                    attached.append({"type": "image_url",
                                     "image_url": {"url": f"data:{block.mime_type};base64,{block.data}"}})
                elif isinstance(block, AudioContent) and "audio" in accepts:
                    attached.append({"type": "input_audio",
                                     "input_audio": {"data": block.data, "format": _audio_format(block.mime_type)}})

    flush()
    return result


def _audio_format(mime_type: str) -> str:
    """The ``input_audio`` format name for a MIME type: audio/wav -> wav."""
    sub = (mime_type or "").split("/")[-1].lower()
    return {"mpeg": "mp3", "x-wav": "wav", "wave": "wav"}.get(sub, sub or "wav")


def _build_tools(context: Context, model: Model) -> list[dict[str, Any]] | None:
    if not context.tools:
        return None
    # Schema dialect + strict flag are decided by the unified
    # ``providers.schema`` layer, keyed on (api, model). Chat
    # Completions is always strict-family, so this resolves to the
    # openai_strict dialect + strict:true when enabled, except mapping tools.
    from openprogram.models.providers.schema import normalize_for, wants_strict_flag
    api = model.api
    mid = model.id
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": normalize_for(api, tool.parameters, mid),
                "strict": wants_strict_flag(api, mid, tool.parameters),
            },
        }
        for tool in context.tools
    ]


async def stream_simple(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AsyncGenerator[AssistantMessageEvent, None]:
    """Stream a response from the OpenAI Chat Completions API."""
    if _openai is None:
        raise ImportError(
            "The 'openai' Python SDK is required to use the OpenAI "
            "(Chat Completions) provider. It ships as a base dependency, so "
            "this means the installation is incomplete; reinstall the "
            "complete OpenProgram release."
        )
    opts = options or SimpleStreamOptions()

    from openprogram.models.auth.provider_policy import require_supported_provider
    require_supported_provider(model.provider)
    validate_input_modalities(model, context)

    # Match other HTTP providers' stream retry budget (default 3) so
    # transient 429/5xx/connect failures are absorbed without
    # bubbling to runtime.exec(). Override via the shared env
    # ``OPENPROGRAM_OPENAI_MAX_RETRIES``.
    import os as _os
    sdk_max_retries = int(_os.environ.get("OPENPROGRAM_OPENAI_MAX_RETRIES", "3"))
    from ...streaming.budget import provider_sdk_retries
    sdk_max_retries = provider_sdk_retries(sdk_max_retries)

    from ....auth import usage as _auth_usage
    _pooled = _auth_usage.acquire_pooled(model.provider)
    _cred_profile: str | None = None
    _cred_id: str | None = None
    _conn = None
    _client_api_key = opts.api_key
    if _pooled:
        _conn, _cred_profile, _cred_id = _pooled
        _client_api_key = _conn.auth_value

    # The credential's own base_url wins (e.g. an Aliyun Bailian api_key
    # carries its own endpoint) — else the catalog default, unless that
    # default is just the stock OpenAI endpoint (None lets the SDK use its
    # own default rather than us hardcoding it here).
    base_url = (_conn.base_url if _conn and _conn.base_url else None) \
        or (model.base_url if model.base_url != "https://api.openai.com/v1" else None)
    extra_headers = {**(opts.headers or {}), **(_conn.headers if _conn else {})}
    from ...shared.opencode_headers import opencode_headers
    extra_headers.update(opencode_headers(model.provider, base_url, opts.session_id))
    if opts.get("supports_idempotency_key") and opts.get("idempotency_key"):
        extra_headers["Idempotency-Key"] = opts["idempotency_key"]

    # SuperGrok / X Premium+ OAuth is not an api.x.ai key. Force the
    # official CLI chat proxy + identity headers even if an older
    # login seeded models at api.x.ai.
    if model.provider == "xai-subscription":
        from openprogram.models.providers.adapters.xai_subscription.headers import (
            CLI_CHAT_PROXY_BASE_URL,
            grok_cli_headers,
        )
        base_url = CLI_CHAT_PROXY_BASE_URL
        extra_headers.update(grok_cli_headers(model.id))

    request_headers = {}
    from ...catalog.local import is_local_provider
    if is_local_provider(model.provider):
        from ...settings.storage import _resolve_base_url
        base_url = _resolve_base_url(model.provider) or base_url
        if not _client_api_key:
            # Explicit SDK value prevents fallback to OPENAI_API_KEY.
            _client_api_key = "openprogram-local-no-key"
            # The SDK validates explicit omission in per-request headers.
            request_headers["Authorization"] = _openai.Omit()
    if not _client_api_key:
        # No key in the AuthStore. Fail here with a precise message —
        # NEVER hand api_key=None to the SDK, which would silently
        # fall back to the OPENAI_API_KEY env var (wrong key, wrong
        # provider, misleading 401s).
        from ...utils.errors import ErrorReason, LLMError
        # "No key" and "key stored but dead (quota / revoked)" are very
        # different user problems — diagnose which one this is.
        _diag = _auth_usage.stored_but_unusable(model.provider)
        raise LLMError(
            message=_diag or (
                f"No API key configured for provider '{model.provider}'. "
                f"Add one in Settings → Providers, or run: "
                f"openprogram providers login {model.provider} --api-key"
            ),
            reason=ErrorReason.AUTHENTICATION,
            retryable=False,
            provider=model.provider,
            model=model.id,
        )

    # Shared hardened httpx client: same proxy semantics (env + override),
    # keepalive/IPv4 hardening, and one connection pool per event loop
    # instead of a fresh SDK-built client per call. The SDK never closes
    # externally-supplied clients.
    from openprogram.host.security.url_policy import OwnerURLException, normalize_origin
    from ...utils.http_client import get_shared_async_client

    configured_url = base_url or "https://api.openai.com/v1"
    configured_origin = normalize_origin(configured_url)

    client = _openai.AsyncOpenAI(
        api_key=_client_api_key,
        base_url=base_url,
        default_headers=extra_headers or None,
        max_retries=sdk_max_retries,
        http_client=get_shared_async_client(
            "openai-sdk",
            consumer="provider.openai.sdk",
            configured_origin=configured_origin,
            owner_exception=OwnerURLException(
                consumer="provider.openai.sdk", origin=configured_origin
            ),
        ),
    )

    # Transform messages for cross-provider compatibility
    transformed_msgs = _transform_messages(context.messages, model)
    transformed_context = Context(
        system_prompt=context.system_prompt,
        messages=transformed_msgs,
        tools=context.tools,
    )

    messages = _build_messages(transformed_context, model)
    tools = _build_tools(transformed_context, model)

    params: dict[str, Any] = {
        "model": model.id,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if request_headers:
        params["extra_headers"] = request_headers

    if opts.max_tokens:
        if _uses_max_completion_tokens(model):
            params["max_completion_tokens"] = opts.max_tokens
        else:
            params["max_tokens"] = opts.max_tokens

    if opts.temperature is not None:
        params["temperature"] = opts.temperature

    output = opts.response_format
    if output is not None:
        schema_payload = {
            "name": output.name,
            "strict": output.strict,
            "schema": output.schema,
        }
        if output.description is not None:
            schema_payload["description"] = output.description
        params["response_format"] = {
            "type": "json_schema",
            "json_schema": schema_payload,
        }

    if tools:
        params["tools"] = tools
        # Caller-set pick policy. Chat Completions takes "auto" /
        # "required" / "none" verbatim; the framework's forced-pick
        # shape {"type": "function", "name": X} maps to the nested
        # {"function": {"name": X}} form this API expects.
        tool_choice = opts.get("tool_choice")
        if tool_choice is not None:
            if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                params["tool_choice"] = {
                    "type": "function",
                    "function": {"name": tool_choice.get("name")},
                }
            else:
                params["tool_choice"] = tool_choice
        if opts.get("parallel_tool_calls") is False:
            params["parallel_tool_calls"] = False

    # Non-reasoning models reject the reasoning_effort parameter.
    if opts.reasoning and getattr(model, "reasoning", False):
        from openprogram.models.providers.settings.thinking_spec import translate_reasoning
        effort = translate_reasoning(
            model.provider or "openai", model.id, opts.reasoning)
        if effort is not None:
            params["reasoning_effort"] = effort

    # Per-request speed / priority tier ("priority" = the Fast mode,
    # "flex" = cheaper-slower). The Responses path already forwards
    # this; mirror it here so Chat Completions honours the same knob.
    service_tier = opts.get("service_tier")
    if service_tier:
        params["service_tier"] = service_tier

    partial = AssistantMessage.empty(model)
    content_blocks: list[Any] = []
    text_index = -1
    thinking_index = -1
    tool_indices: dict[str, int] = {}
    tool_arg_buffers: dict[str, str] = {}
    usage = Usage()
    actual_tier = None
    finish_reason = None
    # After finish_reason, Grok/xAI often keep SSE open for a usage
    # chunk or [DONE] that never arrives. Hang here = text done, UI
    # still running. Drain usage briefly, then stop.
    finished_at = None

    _signal = getattr(opts, "signal", None)

    def _user_cancelled() -> bool:
        f = getattr(_signal, "is_set", None)
        return bool(_signal is not None and callable(f) and f())

    def _stop() -> bool:
        if _user_cancelled():
            return True
        if finished_at is not None and (time.monotonic() - finished_at) >= USAGE_DRAIN_S:
            return True
        return False

    from ...recording.capture import COMPLETIONS, capture_request
    capture_request(model, params, wire=COMPLETIONS)

    yield EventStart(type="start", partial=partial)

    try:
        from ...streaming.budget import provider_retry_attempts
        attempts = provider_retry_attempts(PROVIDER_STREAM_MAX_ATTEMPTS)
        _attempt = 0
        transport_reserved = 0
        # Set while the endpoint is unreachable and retries are spent: the
        # request waits for the network instead of failing the turn.
        network_wait: dict[str, float] | None = None
        while True:
            try:
                # Response headers or a chunk that never come end the
                # attempt as a retryable StreamIdleTimeout, retried below.
                idle_s = event_idle_timeout_s()
                async with await idle_bounded_open(
                    client.chat.completions.create(**params), idle_s,
                    label=model.provider,
                ) as stream:
                    # Per-minute headroom (``x-ratelimit-*``) the reply carries.
                    from openprogram.models.usage.quota import observe_headers
                    observe_headers(
                        model.provider, _client_api_key,
                        getattr(getattr(stream, "response", None), "headers", None),
                        account=_cred_id,
                    )
                    # <=250ms cancel poll — do not wait for the next token.
                    async for chunk in idle_bounded(
                        iter_until_cancelled(stream, _stop), idle_s,
                        label=f"{model.provider} chunk",
                    ):
                        actual_tier = getattr(chunk, "service_tier", None) or actual_tier
                        # Process usage from chunks
                        if chunk.usage:
                            usage = _usage_from_chunk(chunk.usage)

                        if not chunk.choices:
                            if finished_at is not None:
                                break
                            continue

                        delta = chunk.choices[0].delta
                        chunk_finish_reason = chunk.choices[0].finish_reason
                        # Keep an explicit terminal reason across trailing
                        # usage/empty choices chunks. A later None must not
                        # turn a real length/tool_calls finish into a stop.
                        if chunk_finish_reason is not None:
                            finish_reason = chunk_finish_reason

                        # Reasoning / thinking content (for o1/o3 models)
                        reasoning_content = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                        if reasoning_content:
                            if thinking_index == -1:
                                thinking_index = len(content_blocks)
                                content_blocks.append(ThinkingContent(type="thinking", thinking=""))
                                partial = partial.model_copy(update={"content": list(content_blocks)})
                                yield EventThinkingStart(type="thinking_start", content_index=thinking_index, partial=partial)

                            content_blocks[thinking_index] = ThinkingContent(
                                type="thinking",
                                thinking=content_blocks[thinking_index].thinking + reasoning_content,
                            )
                            partial = partial.model_copy(update={"content": list(content_blocks)})
                            yield EventThinkingDelta(
                                type="thinking_delta",
                                content_index=thinking_index,
                                delta=reasoning_content,
                                partial=partial,
                            )

                        # Text delta
                        if delta.content:
                            # Close thinking block if transitioning to text
                            if thinking_index >= 0 and text_index == -1:
                                yield EventThinkingEnd(
                                    type="thinking_end",
                                    content_index=thinking_index,
                                    content=content_blocks[thinking_index].thinking,
                                    partial=partial,
                                )

                            if text_index == -1:
                                text_index = len(content_blocks)
                                content_blocks.append(TextContent(type="text", text=""))
                                partial = partial.model_copy(update={"content": list(content_blocks)})
                                yield EventTextStart(type="text_start", content_index=text_index, partial=partial)

                            content_blocks[text_index] = TextContent(
                                type="text",
                                text=content_blocks[text_index].text + delta.content,
                            )
                            partial = partial.model_copy(update={"content": list(content_blocks)})
                            yield EventTextDelta(
                                type="text_delta",
                                content_index=text_index,
                                delta=delta.content,
                                partial=partial,
                            )

                        # Tool call deltas
                        if delta.tool_calls:
                            for tc_delta in delta.tool_calls:
                                tc_id = tc_delta.id or ""
                                idx_key = str(tc_delta.index)

                                if idx_key not in tool_indices:
                                    idx = len(content_blocks)
                                    tool_indices[idx_key] = idx
                                    tool_arg_buffers[idx_key] = ""
                                    content_blocks.append(ToolCall(
                                        type="toolCall",
                                        id=tc_id or f"call_{idx}",
                                        name=tc_delta.function.name or "",
                                        arguments={},
                                    ))
                                    partial = partial.model_copy(update={"content": list(content_blocks)})
                                    yield EventToolCallStart(type="toolcall_start", content_index=idx, partial=partial)

                                if tc_delta.function and tc_delta.function.arguments:
                                    tool_arg_buffers[idx_key] += tc_delta.function.arguments
                                    partial = partial.model_copy(update={"content": list(content_blocks)})
                                    yield EventToolCallDelta(
                                        type="toolcall_delta",
                                        content_index=tool_indices[idx_key],
                                        delta=tc_delta.function.arguments,
                                        partial=partial,
                                    )

                        if chunk_finish_reason:
                            # Finalize thinking
                            if thinking_index >= 0 and text_index == -1:
                                yield EventThinkingEnd(
                                    type="thinking_end",
                                    content_index=thinking_index,
                                    content=content_blocks[thinking_index].thinking,
                                    partial=partial,
                                )

                            # Finalize text block
                            if text_index >= 0:
                                yield EventTextEnd(
                                    type="text_end",
                                    content_index=text_index,
                                    content=content_blocks[text_index].text,
                                    partial=partial,
                                )

                            # Finalize tool calls
                            for idx_key, idx in tool_indices.items():
                                raw = tool_arg_buffers.get(idx_key, "{}")
                                parsed = parse_partial_json(raw) or {}
                                tc = content_blocks[idx]
                                content_blocks[idx] = ToolCall(
                                    type="toolCall",
                                    id=tc.id,
                                    name=tc.name,
                                    arguments=parsed,
                                )
                                partial = partial.model_copy(update={"content": list(content_blocks)})
                                yield EventToolCallEnd(
                                    type="toolcall_end",
                                    content_index=idx,
                                    tool_call=content_blocks[idx],
                                    partial=partial,
                                )

                            # finish_reason is the end of choices. Do not wait
                            # forever for [DONE] — that left Grok turns running
                            # after the last token. Keep a short drain for the
                            # trailing include_usage chunk.
                            if chunk.usage:
                                break
                            finished_at = time.monotonic()

                if transport_reserved:
                    from ...utils.recovery import current_recovery
                    recovery = current_recovery.get()
                    if recovery is not None:
                        recovery.refund("transport", transport_reserved)
                break
            except (_openai.APIError, StreamIdleTimeout) as e:
                _, retryable = classify_error(
                    e, http_status=getattr(e, "status_code", None),
                )
                # "Committed" means user-visible output (text / tool calls).
                # Thinking-only content is safe to discard and regenerate —
                # xAI reasoning models routinely die mid-thinking with a
                # retryable "Internal error during token generation", and
                # treating that half-thinking as committed turned every such
                # blip into a dead turn. The UI reconciles on turn_end with
                # the provider's final content list, so the retried stream's
                # thinking replaces the discarded prefix.
                committed = any(
                    not isinstance(b, ThinkingContent) for b in content_blocks
                )
                if committed or not retryable or _user_cancelled():
                    raise
                from ...utils.recovery import reserve_recovery, current_recovery
                from ...utils.stream_retry import (
                    _sleep_unless_aborted,
                    is_network_unreachable,
                    network_wait_allowed,
                    network_wait_delay,
                    network_wait_started,
                    retry_error_text,
                )
                unreachable = is_network_unreachable(e)
                if network_wait is not None and not unreachable:
                    # The network answered: the outage spent those retries,
                    # so this failure starts a fresh budget.
                    recovery = current_recovery.get()
                    if recovery is not None:
                        recovery.refund("transport", transport_reserved)
                    transport_reserved = 0
                    network_wait = None
                    _attempt = 0
                    _log.info("network wait: network is back")
                if network_wait is None:
                    if _attempt >= attempts - 1 or not reserve_recovery("transport"):
                        if (
                            not unreachable
                            or not network_wait_allowed(attempts)
                            or network_wait_delay(network_wait_started(), 0) is None
                        ):
                            e.transport_exhausted = True
                            raise
                        network_wait = {"started": network_wait_started(), "probes": 0}
                        _log.info(
                            "network wait: endpoint unreachable — %s; waiting for the network", e,
                        )
                    else:
                        transport_reserved += 1
                if network_wait is not None:
                    sleep_s = network_wait_delay(network_wait["started"], int(network_wait["probes"]))
                    if sleep_s is None:
                        e.transport_exhausted = True
                        raise
                    network_wait["probes"] += 1
                    retry_event = EventRetry(
                        attempt=int(network_wait["probes"]), max_attempts=None,
                        reason="network_wait", delay_ms=round(sleep_s * 1000),
                        error=retry_error_text(e),
                    )
                else:
                    recovery = current_recovery.get()
                    if recovery is not None:
                        recovery.started(provider=model.provider, model=model.id, retry_reason="transport")
                    sleep_s = stream_backoff_seconds(
                        _attempt, getattr(e, "retry_after_s", None) or None,
                    )
                    retry_event = EventRetry(
                        attempt=_attempt + 2, max_attempts=attempts,
                        reason="transport", delay_ms=round(sleep_s * 1000),
                        error=retry_error_text(e),
                    )
                    _log.info(
                        "stream retry: attempt %s/%s after %.1fs — %s",
                        _attempt + 1, attempts, sleep_s, e,
                    )
                    _attempt += 1
                # Reset per-attempt stream state so the retry starts clean.
                content_blocks = []
                text_index = -1
                thinking_index = -1
                tool_indices = {}
                tool_arg_buffers = {}
                usage = Usage()
                actual_tier = None
                finish_reason = None
                finished_at = None
                partial = AssistantMessage.empty(model)
                yield retry_event
                await _sleep_unless_aborted(sleep_s, _user_cancelled)

        # Build final message
        stop_reason_map = {
            "stop": "stop", "length": "length", "tool_calls": "toolUse",
            "function_call": "toolUse", "content_filter": "error",
        }
        # Missing/unknown termination is incomplete. A nominal stop with
        # only thinking is also incomplete. Return a length continuation to
        # the agent loop so each paid request retains its own usage record.
        stop_reason = stop_reason_map.get(finish_reason, "length")
        if stop_reason == "stop" and not any(
            not isinstance(block, ThinkingContent) for block in content_blocks
        ):
            stop_reason = "length"
        if stop_reason == "length" and tool_indices:
            content_blocks = [b for b in content_blocks if not isinstance(b, ToolCall)]
            tool_indices.clear()
        if tool_indices and stop_reason == "stop":
            stop_reason = "toolUse"

        if _user_cancelled():
            stop_reason = "aborted"

        usage.requested_service_tier = opts.get("service_tier")
        usage.service_tier = actual_tier
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
            # Clean completion — clear any transient cooldown state on the key.
            if _cred_id:
                _auth_usage.record_call_success(model.provider, _cred_profile, _cred_id)
            yield EventDone(type="done", reason=stop_reason, message=final)

    except _openai.APIError as e:
        # Cool this key down (429 → rate_limit, 402 → billing, 401/403 →
        # needs_reauth, 5xx → server_error) so the next request rotates.
        if _cred_id:
            _auth_usage.record_call_failure(
                model.provider, _cred_profile, _cred_id,
                getattr(e, "status_code", None), str(e),
            )
        # Re-raise so the classification layer (_build_llm_error /
        # should_failover / retry) sees the real exception — yielding
        # EventError here made agent_loop treat the failure as a normal
        # terminal message and the error was never classified.
        raise
    except Exception as e:
        # Non-API error (connection/timeout/etc.) — cool down briefly so a flaky
        # key/endpoint rotates rather than being hammered.
        if _cred_id:
            _auth_usage.record_call_failure(model.provider, _cred_profile, _cred_id, None, str(e))
        if not _user_cancelled():
            raise  # same rationale as the APIError branch above
        # User cancel that surfaced as an exception — finalize as
        # "aborted", not an error, preserving streamed content.
        error_msg = AssistantMessage(
            role="assistant",
            content=content_blocks or [TextContent(type="text", text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=usage,
            stop_reason="aborted",
            error_message=str(e),
            timestamp=int(time.time() * 1000),
        )
        yield EventError(type="error", reason="aborted", error=error_msg)

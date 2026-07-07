"""
OpenAI Chat Completions API provider — mirrors packages/ai/src/providers/openai-completions.ts
"""
from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator

# The ``openai`` SDK is a base dependency (installed by default). This
# guarded import only stays defensive so importing this module succeeds
# even in a stripped env and the provider *catalog* stays usable.
# Functions below re-check ``_openai`` and raise a clear error.
try:
    import openai as _openai
except ImportError:  # pragma: no cover — base dep, normally present
    _openai = None  # type: ignore[assignment]

from ..types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    EventDone,
    EventError,
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
from ..utils.json_parse import parse_partial_json
from .._shared.transform_messages import transform_messages as _transform_messages
from .._shared.validate_modalities import validate_input_modalities


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


def _build_messages(context: Context, model: Model) -> list[dict[str, Any]]:
    """Convert Context messages to OpenAI Chat Completions format."""
    result: list[dict[str, Any]] = []

    if context.system_prompt:
        role = "developer" if _uses_developer_role(model) else "system"
        result.append({"role": role, "content": context.system_prompt})

    # Historical workaround for the older ``claude-max-api-proxy`` npm
    # package: it did NOT understand the OpenAI multi-part content array
    # and stringified the list with JS's default toString(), so every
    # user message arrived at Claude as ``[object Object]``. Meridian
    # (the recommended replacement) handles multi-part content correctly,
    # but we keep this flatten-text-only path so the older proxy still
    # works for text-only flows. Mixed content (images) is unsupported by
    # claude-max-api-proxy regardless — we still emit the array form for
    # it so Meridian gets a proper request and the legacy proxy fails
    # loudly, which is the right behaviour.
    _flatten_text_only = model.provider == "claude-code"

    for msg in context.messages:
        if isinstance(msg, UserMessage):
            if isinstance(msg.content, str):
                result.append({"role": "user", "content": msg.content})
            else:
                all_text = all(
                    isinstance(b, TextContent) for b in msg.content
                )
                if _flatten_text_only and all_text:
                    result.append({
                        "role": "user",
                        "content": "\n".join(b.text for b in msg.content),
                    })
                    continue
                content_blocks: list[dict[str, Any]] = []
                for block in msg.content:
                    if isinstance(block, TextContent):
                        content_blocks.append({"type": "text", "text": block.text})
                    elif isinstance(block, ImageContent):
                        content_blocks.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{block.mime_type};base64,{block.data}",
                            },
                        })
                result.append({"role": "user", "content": content_blocks})

        elif isinstance(msg, AssistantMessage):
            tool_calls = [c for c in msg.content if isinstance(c, ToolCall)]
            text_parts = [c for c in msg.content if isinstance(c, TextContent)]
            text = " ".join(t.text for t in text_parts) if text_parts else None

            if tool_calls:
                tc_list = [
                    {
                        "id": tc.id,
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
                "tool_call_id": msg.tool_call_id,
                "content": content_text,
            })

    return result


def _build_tools(context: Context, model: Model) -> list[dict[str, Any]] | None:
    if not context.tools:
        return None
    # Schema dialect + strict flag are decided by the unified
    # ``providers._schema`` layer, keyed on (api, model). Chat
    # Completions is always strict-family, so this resolves to the
    # openai_strict dialect + strict:true when the env toggle is on.
    from openprogram.providers._schema import normalize_for, wants_strict_flag
    api = model.api
    mid = model.id
    use_strict = wants_strict_flag(api, mid)
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": normalize_for(api, tool.parameters, mid),
                "strict": use_strict,
            },
        }
        for tool in context.tools
    ]


def _make_empty_assistant(model: Model) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=Usage(),
        stop_reason="stop",
        timestamp=int(time.time() * 1000),
    )


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
            "this usually means a broken env — reinstall with "
            "`pip install -U openprogram` (or `pip install openai`)."
        )
    opts = options or SimpleStreamOptions()

    validate_input_modalities(model, context)

    # (Removed: Meridian profile-header injection for claude-code. claude-code
    # now connects DIRECT via the anthropic-messages wire, so it never reaches
    # openai_completions — this branch was dead.)

    # Match other HTTP providers' stream retry budget (default 3) so
    # transient 429/5xx/connect failures are absorbed without
    # bubbling to runtime.exec(). Override via the shared env
    # ``OPENPROGRAM_OPENAI_MAX_RETRIES``.
    import os as _os
    sdk_max_retries = int(_os.environ.get("OPENPROGRAM_OPENAI_MAX_RETRIES", "3"))

    # Per-request credential from an AuthStore pool — enables multi-key rotation
    # + cooldown for providers whose keys live in the pool. No-op (returns None)
    # for OAuth / claude-code providers, which keep using opts.api_key.
    # The call outcome is reported against this credential below (see the report_*
    # calls), so a 429 cools it down and the next request rotates to another key.
    from ...auth import usage as _auth_usage
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

    if not _client_api_key:
        if model.provider == "claude-code":
            # The local Meridian daemon authenticates via Claude Code's
            # own OAuth and ignores the key — but the openai SDK requires
            # a non-empty string.
            _client_api_key = "claude-code"
        else:
            # No key in the AuthStore. Fail here with a precise message —
            # NEVER hand api_key=None to the SDK, which would silently
            # fall back to the OPENAI_API_KEY env var (wrong key, wrong
            # provider, misleading 401s).
            from ..utils.errors import ErrorReason, LLMError
            raise LLMError(
                message=(
                    f"No API key configured for provider '{model.provider}'. "
                    f"Add one in Settings → Providers, or run: "
                    f"openprogram providers login {model.provider} --api-key"
                ),
                reason=ErrorReason.AUTHENTICATION,
                retryable=False,
                provider=model.provider,
                model=model.id,
            )

    client = _openai.AsyncOpenAI(
        api_key=_client_api_key,
        base_url=base_url,
        default_headers=extra_headers or None,
        max_retries=sdk_max_retries,
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

    if opts.max_tokens:
        if _uses_max_completion_tokens(model):
            params["max_completion_tokens"] = opts.max_tokens
        else:
            params["max_tokens"] = opts.max_tokens

    if opts.temperature is not None:
        params["temperature"] = opts.temperature

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

    if opts.reasoning:
        from openprogram.providers.thinking_spec import translate_reasoning
        params["reasoning_effort"] = translate_reasoning(
            model.provider or "openai", model.id, opts.reasoning)

    # Per-request speed / priority tier ("priority" = the Fast mode,
    # "flex" = cheaper-slower). The Responses path already forwards
    # this; mirror it here so Chat Completions honours the same knob.
    service_tier = opts.get("service_tier")
    if service_tier:
        params["service_tier"] = service_tier

    partial = _make_empty_assistant(model)
    content_blocks: list[Any] = []
    text_index = -1
    thinking_index = -1
    tool_indices: dict[str, int] = {}
    tool_arg_buffers: dict[str, str] = {}
    usage = Usage()

    yield EventStart(type="start", partial=partial)

    try:
        async with await client.chat.completions.create(**params) as stream:
            async for chunk in stream:
                # Process usage from chunks
                if chunk.usage:
                    u = chunk.usage
                    usage = Usage(
                        input=getattr(u, "prompt_tokens", 0) or 0,
                        output=getattr(u, "completion_tokens", 0) or 0,
                        total_tokens=getattr(u, "total_tokens", 0) or 0,
                    )
                    # Check for reasoning tokens
                    details = getattr(u, "completion_tokens_details", None)
                    if details:
                        reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0
                        if reasoning_tokens:
                            usage.output = (getattr(u, "completion_tokens", 0) or 0) - reasoning_tokens

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason

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

                if finish_reason:
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

        # Build final message
        stop_reason_map = {"stop": "stop", "length": "length", "tool_calls": "toolUse"}
        stop_reason = stop_reason_map.get(finish_reason or "", "stop")
        if tool_indices and stop_reason == "stop":
            stop_reason = "toolUse"

        signal = getattr(opts, "signal", None)
        if signal and callable(getattr(signal, "is_set", None)) and signal.is_set():
            stop_reason = "aborted"

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
                _auth_usage.report_success(model.provider, _cred_profile, _cred_id)
            yield EventDone(type="done", reason=stop_reason, message=final)

    except _openai.APIError as e:
        # Cool this key down (429 → rate_limit, 402 → billing, 401/403 →
        # needs_reauth, 5xx → server_error) so the next request rotates.
        if _cred_id:
            _auth_usage.report_failure(
                model.provider, _cred_profile, _cred_id,
                getattr(e, "status_code", None), str(e),
            )
        error_msg = AssistantMessage(
            role="assistant",
            content=[TextContent(type="text", text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=Usage(),
            stop_reason="error",
            error_message=str(e),
            timestamp=int(time.time() * 1000),
        )
        yield EventError(type="error", reason="error", error=error_msg)
    except Exception as e:
        # Non-API error (connection/timeout/etc.) — cool down briefly so a flaky
        # key/endpoint rotates rather than being hammered.
        if _cred_id:
            _auth_usage.report_failure(model.provider, _cred_profile, _cred_id, None, str(e))
        error_msg = AssistantMessage(
            role="assistant",
            content=content_blocks or [TextContent(type="text", text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=usage,
            stop_reason="error",
            error_message=str(e),
            timestamp=int(time.time() * 1000),
        )
        yield EventError(type="error", reason="error", error=error_msg)

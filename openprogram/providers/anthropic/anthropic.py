"""
Anthropic Messages API provider — mirrors packages/ai/src/providers/anthropic.ts

Full parity including:
- OAuth token detection (sk-ant-oat) → adaptive thinking effort levels
- Cache control retention (ephemeral / 1h)
- Beta headers: fine-grained-tool-streaming + interleaved-thinking
- sanitize_surrogates on all text content
- Empty content block filtering
- Usage capture from message_start event (not just end)
- All stop reasons: pause_turn, sensitive, refusal
- Claude Code tool name normalization for OAuth tokens
"""
from __future__ import annotations

import re
import time
from typing import Any, AsyncGenerator

# The ``anthropic`` Python SDK is an optional extra (``openprogram[anthropic]``)
# — importing this module must succeed without it so the provider
# *catalog* (which side-effect-imports this package) stays usable in
# environments that haven't installed the SDK yet. Any actual streaming
# call below re-checks ``_anthropic`` and raises a clear error.
try:
    import anthropic as _anthropic
except ImportError:  # pragma: no cover — SDK is an optional extra
    _anthropic = None  # type: ignore[assignment]

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
    ThinkingLevel,
    ToolCall,
    ToolResultMessage,
    Usage,
    UsageCost,
    UserMessage,
)
from ..utils.event_stream import EventStream
from ..utils.json_parse import parse_partial_json, parse_streaming_json
from ..utils.sanitize_unicode import sanitize_surrogates
from .._shared.transform_messages import transform_messages as _transform_messages
from .._shared.validate_modalities import validate_input_modalities

# Anthropic beta features
_BETA_FINE_GRAINED = "fine-grained-tool-streaming-2025-05-14"
_BETA_INTERLEAVED = "interleaved-thinking-2025-05-14"
_BETA_OAUTH = "oauth-2025-04-20"
_BETA_CLAUDE_CODE = "claude-code-20250219"
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
    unified ``_schema.wants_strict_flag`` — AND *not* the Claude Code
    OAuth path. OAuth requests impersonate the Claude Code CLI with its
    own header set; we don't layer the structured-outputs beta on top
    of that stealth path. Direct API-key requests get strict.
    """
    if is_oauth:
        return False
    from openprogram.providers._schema import wants_strict_flag
    return wants_strict_flag(getattr(model, "api", None), getattr(model, "id", None))

# Claude Code version for OAuth stealth mode
_CLAUDE_CODE_VERSION = "2.1.62"

# Claude Code canonical tool name lookup (case-insensitive → canonical)
_CLAUDE_CODE_TOOLS = [
    "Read", "Write", "Edit", "Bash", "Grep", "Glob",
    "AskUserQuestion", "EnterPlanMode", "ExitPlanMode", "KillShell",
    "NotebookEdit", "Skill", "Task", "TaskOutput", "TodoWrite",
    "WebFetch", "WebSearch",
]
_CC_TOOL_LOOKUP = {t.lower(): t for t in _CLAUDE_CODE_TOOLS}


def _to_claude_code_name(name: str) -> str:
    """Convert tool name to Claude Code canonical casing."""
    return _CC_TOOL_LOOKUP.get(name.lower(), name)


def _from_claude_code_name(name: str, tools: list | None = None) -> str:
    """Map Claude Code tool name back to registered tool name."""
    if tools:
        lower = name.lower()
        for tool in tools:
            tname = tool.name if hasattr(tool, "name") else tool.get("name", "")
            if tname.lower() == lower:
                return tname
    return name


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
    (see providers/cache_spec.py) rather than being hardcoded here.
    """
    from openprogram.providers.cache_spec import get_cache_spec, ttl_for_retention

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
) -> tuple[_anthropic.AsyncAnthropic, bool]:
    """
    Build the Anthropic async client with appropriate headers.
    Mirrors createClient() in TypeScript.

    Returns (client, is_oauth_token).
    """
    if _anthropic is None:
        raise ImportError(
            "The 'anthropic' Python SDK is required to use the Anthropic "
            "provider. Install it with `pip install anthropic` (or, when "
            "installing OpenProgram, `pip install 'openprogram[anthropic]'`)."
        )
    is_oauth = _is_oauth_token(api_key)
    base_url = getattr(model, "base_url", None) or getattr(model, "baseUrl", None)
    model_headers = model.headers or {}

    # Adaptive thinking models don't use the interleaved-thinking beta (it's deprecated for them)
    needs_interleaved_beta = interleaved_thinking and not _supports_adaptive_thinking(model.id)

    beta_features = [_BETA_FINE_GRAINED]
    if needs_interleaved_beta:
        beta_features.append(_BETA_INTERLEAVED)
    # Strict tool use needs its own beta header. Only the regular
    # API-key path (not Claude Code OAuth) and recent models qualify.
    if _anthropic_strict_on(model, is_oauth):
        beta_features.append(_BETA_STRUCTURED_OUTPUTS)
    # 1M context — opt-in via the model id's [1m] suffix. The bare id is
    # sent in the request body (see _build_params); here we just flip the
    # beta header on.
    if _wants_1m(model.id):
        beta_features.append(_BETA_CONTEXT_1M)

    # SDK-level retry budget: Anthropic SDK retries 429/5xx/transport
    # errors with its own exponential backoff. Default is 2; we raise
    # to 3 by default to match the stream-level retry budget of our
    # other HTTP providers (see openai-codex + utils/stream_retry).
    # ``OPENPROGRAM_ANTHROPIC_MAX_RETRIES`` env overrides.
    import os as _os
    sdk_max_retries = int(_os.environ.get("OPENPROGRAM_ANTHROPIC_MAX_RETRIES", "3"))

    if is_oauth:
        # OAuth: Bearer auth + Claude Code identity headers
        default_headers = {
            "accept": "application/json",
            "anthropic-beta": f"{_BETA_CLAUDE_CODE},{_BETA_OAUTH},{','.join(beta_features)}",
            "user-agent": f"claude-cli/{_CLAUDE_CODE_VERSION}",
            "x-app": "cli",
            **model_headers,
            **(options_headers or {}),
        }
        client = _anthropic.AsyncAnthropic(
            api_key=None,
            auth_token=api_key,
            base_url=base_url,
            default_headers=default_headers,
            max_retries=sdk_max_retries,
            http_client=_shared_http_client(),
        )
        # CRITICAL: even with api_key=None the SDK falls back to the
        # ANTHROPIC_API_KEY env var and sends it as x-api-key ALONGSIDE our
        # Bearer auth_token. If that env key is pay-as-you-go (sk-ant-api…),
        # Anthropic prefers x-api-key and bills it — the subscription OAuth is
        # ignored → 400 "credit balance too low". The worker process inherits
        # that env var; CLI test processes don't, which is why this only
        # reproduced under the worker. Force x-api-key OFF so ONLY the OAuth
        # Bearer authenticates.
        client.api_key = None
    else:
        # Regular API key auth
        default_headers = {
            "accept": "application/json",
            "anthropic-beta": ",".join(beta_features),
            **model_headers,
            **(options_headers or {}),
        }
        client = _anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            default_headers=default_headers,
            max_retries=sdk_max_retries,
            http_client=_shared_http_client(),
        )

    return client, is_oauth


def _shared_http_client():
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
    Returns None on any failure so the SDK falls back to its own client.
    """
    try:
        from openprogram.providers.utils.http_client import get_shared_async_client
        return get_shared_async_client("anthropic")
    except Exception:
        return None


def _convert_tool_result_block(tr_msg: ToolResultMessage, is_oauth: bool = False) -> dict[str, Any]:
    """Convert a single ToolResultMessage to an Anthropic tool_result block."""
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


def _build_messages(
    context: Context,
    is_oauth: bool = False,
    cache_control: dict | None = None,
) -> list[dict[str, Any]]:
    """
    Convert Context messages to Anthropic API format.
    Batches consecutive toolResult messages into a single user message.
    """
    result: list[dict[str, Any]] = []
    all_msgs = context.messages
    i = 0

    while i < len(all_msgs):
        msg = all_msgs[i]
        is_last = i == len(all_msgs) - 1

        if isinstance(msg, UserMessage):
            if isinstance(msg.content, str):
                text = _sanitize_surrogates(msg.content)
                if text.strip():
                    block: dict[str, Any] = {"type": "text", "text": text}
                    if is_last and cache_control:
                        block["cache_control"] = cache_control
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
                # Provider-default breakpoint on the last block of the final
                # user turn. Only apply it when the caller did not already mark
                # a breakpoint somewhere in this message, so an explicit
                # caller-supplied prefix breakpoint is not silently shadowed.
                caller_marked = any("cache_control" in b for b in content_blocks)
                if is_last and cache_control and content_blocks and not caller_marked:
                    content_blocks[-1] = {**content_blocks[-1], "cache_control": cache_control}
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
                    tc_name = _to_claude_code_name(block.name) if is_oauth else block.name
                    content_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": tc_name,
                        "input": block.arguments,
                    })
            if content_blocks:
                result.append({"role": "assistant", "content": content_blocks})

        elif isinstance(msg, ToolResultMessage):
            # Batch consecutive toolResult messages into a single user message
            tool_results: list[dict[str, Any]] = [_convert_tool_result_block(msg, is_oauth)]

            j = i + 1
            while j < len(all_msgs) and isinstance(all_msgs[j], ToolResultMessage):
                tool_results.append(_convert_tool_result_block(all_msgs[j], is_oauth))
                j += 1

            result.append({"role": "user", "content": tool_results})
            i = j
            continue

        i += 1

    return result


def _build_tools(
    context: Context,
    is_oauth: bool = False,
    model: "Model | None" = None,
    strict: bool = False,
) -> list[dict[str, Any]] | None:
    """Convert Context tools to Anthropic API format, with Claude Code
    name normalization.

    Schema goes through the unified ``_schema`` dialect layer: when
    ``strict`` is on (recent model + beta header set by the caller) it
    resolves to the ``openai_strict`` shape Anthropic strict tool use
    requires (additionalProperties:false + all-required), and each tool
    gets ``strict: true``. Otherwise it's passthrough — current
    behavior, unchanged.
    """
    if not context.tools:
        return None
    from openprogram.providers._schema import normalize_for
    api = getattr(model, "api", None)
    mid = getattr(model, "id", None)
    tools = []
    for tool in context.tools:
        name = _to_claude_code_name(tool.name) if is_oauth else tool.name
        entry: dict[str, Any] = {
            "name": name,
            "description": tool.description,
            "input_schema": normalize_for(api, tool.parameters, mid),
        }
        if strict:
            entry["strict"] = True
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
    """Build system prompt blocks, adding Claude Code identity for OAuth."""
    blocks: list[dict[str, Any]] = []

    if is_oauth:
        # Claude Code identity MUST be first for OAuth
        cc_block: dict[str, Any] = {
            "type": "text",
            "text": "You are Claude Code, Anthropic's official CLI for Claude.",
        }
        if cache_control:
            cc_block["cache_control"] = cache_control
        blocks.append(cc_block)

    if context.system_prompt:
        sp_block: dict[str, Any] = {
            "type": "text",
            "text": _sanitize_surrogates(context.system_prompt),
        }
        if cache_control:
            sp_block["cache_control"] = cache_control
        blocks.append(sp_block)

    return blocks if blocks else None


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
    """
    Stream a response from the Anthropic Messages API.
    Yields AssistantMessageEvents and stores the final AssistantMessage.
    Full parity with TypeScript including OAuth, cache control, beta headers.
    """
    opts = options or SimpleStreamOptions()

    validate_input_modalities(model, context)

    api_key = opts.api_key or ""
    if not api_key:
        # Unified resolution: covers a plain api-key AND a subscription OAuth
        # token (sk-ant-oat, kind=oauth/cli_delegated) from the AuthStore.
        # _build_client below sniffs the sk-ant-oat prefix and switches to
        # Bearer + Claude Code beta headers, so claude-code subscriptions
        # connect direct to api.anthropic.com (no Meridian daemon).
        from openprogram.auth.resolver import resolve_api_key_sync
        # claude-code is an alias for the anthropic credential pool — its
        # subscription OAuth token lives under provider_id="anthropic", so
        # resolve there (a claude-code/<id> model otherwise hits an empty
        # claude-code pool and 401s with "no API key").
        _pool = "anthropic" if model.provider == "claude-code" else model.provider
        api_key = resolve_api_key_sync(_pool) or ""
    if not api_key:
        # No credential anywhere — fail precisely instead of sending an
        # empty x-api-key header (a misleading upstream 401).
        from ..utils.errors import ErrorReason, LLMError
        raise LLMError(
            message=(
                f"No API key configured for provider '{model.provider}'. "
                f"Add one in Settings -> Providers, or run: "
                f"openprogram providers login {model.provider} --api-key"
            ),
            reason=ErrorReason.AUTHENTICATION,
            retryable=False,
            provider=model.provider,
            model=model.id,
        )

    is_oauth = _is_oauth_token(api_key)
    base_url = getattr(model, "base_url", None) or getattr(model, "baseUrl", None)
    cache_control = _get_cache_control(base_url, getattr(opts, "cache_retention", None))

    client, is_oauth = _build_client(
        model, api_key,
        interleaved_thinking=True,
        options_headers=getattr(opts, "headers", None),
    )

    # Transform messages for cross-provider compatibility
    transformed_msgs = _transform_messages(context.messages, model, _normalize_tool_call_id)
    transformed_context = Context(
        system_prompt=context.system_prompt,
        messages=transformed_msgs,
        tools=context.tools,
    )

    # Auto cache-breakpoint policy (opencode port): mark the last tool + latest
    # user message, preserving any caller-placed markers, within the 4-breakpoint
    # budget. cache_control above still covers the system block + last-block
    # default inside _build_messages/_build_system.
    if cache_control is not None:
        from openprogram.providers.cache_policy import apply_cache_policy
        _retention = getattr(opts, "cache_retention", None)
        transformed_context = apply_cache_policy(
            transformed_context, "anthropic",
            ttl_seconds=3600 if _retention == "long" else None,
        )

    messages = _build_messages(transformed_context, is_oauth=is_oauth, cache_control=cache_control)
    tools = _build_tools(
        transformed_context, is_oauth=is_oauth, model=model,
        strict=_anthropic_strict_on(model, is_oauth),
    )
    system = _build_system(transformed_context, is_oauth=is_oauth, cache_control=cache_control)

    max_tokens = opts.max_tokens or (model.max_tokens // 3 if model.max_tokens else 4096)

    params: dict[str, Any] = {
        # Strip the [1m] opt-in suffix — it's our marker, not a real id; the
        # 1M upgrade rides on the context-1m beta header set in _build_client.
        "model": _strip_1m(model.id),
        "messages": messages,
        "max_tokens": max_tokens,
        # Note: "stream": True is NOT passed to client.messages.stream() — the method itself streams
    }

    if system:
        params["system"] = system

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
        from openprogram.providers.thinking_spec import translate_reasoning, get_thinking_spec
        if _supports_adaptive_thinking(model.id) or is_oauth:
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
                params["max_tokens"] = budget + max_tokens

    # Track partial state
    partial = _make_empty_assistant(model)
    content_blocks: list[Any] = []
    block_index_map: dict[int, int] = {}  # anthropic index → content_blocks index
    tool_arg_buffers: dict[int, str] = {}

    # Apply on_payload callback (mirrors TS: const nextParams = await options?.onPayload?.(params, model))
    if opts.get("on_payload"):
        modified = opts["on_payload"](params, model)
        # Support async callbacks
        if hasattr(modified, "__await__"):
            modified = await modified
        if modified is not None:
            params = modified

    yield EventStart(type="start", partial=partial)

    try:
        async with client.messages.stream(**params) as ant_stream:
            async for event in ant_stream:
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
                        if is_oauth:
                            tc_name = _from_claude_code_name(tc_name, context.tools)
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
                            content_blocks[cb_idx] = TextContent(type="text", text=blk.text + text)
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
                    if isinstance(blk, TextContent):
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

            # Get final message from stream
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
            signal = getattr(opts, "signal", None)
            _is_set = getattr(signal, "is_set", None)
            if signal and callable(_is_set) and _is_set():
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
                yield EventDone(type="done", reason=stop_reason, message=final)

    except Exception as e:
        signal = getattr(opts, "signal", None)
        _is_set_fn = getattr(signal, "is_set", None)
        is_aborted = bool(signal and callable(_is_set_fn) and _is_set_fn())
        stop = "aborted" if is_aborted else "error"

        error_msg = AssistantMessage(
            role="assistant",
            content=content_blocks or [TextContent(type="text", text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=Usage(),
            stop_reason=stop,
            error_message=str(e),
            timestamp=int(time.time() * 1000),
        )
        yield EventError(type="error", reason=stop, error=error_msg)

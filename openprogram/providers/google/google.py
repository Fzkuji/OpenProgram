"""
Google Generative AI provider — mirrors packages/ai/src/providers/google.ts

Uses the new google-genai SDK (google.genai) which supersedes the deprecated
google.generativeai package.
"""
from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator

from .._shared.validate_modalities import validate_input_modalities
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


# ---------------------------------------------------------------------------
# Message conversion
# ---------------------------------------------------------------------------

def _build_contents(context: Context) -> list[Any]:
    """Convert Context messages to google.genai Content objects."""
    from google.genai import types as gtypes

    result: list[Any] = []

    for msg in context.messages:
        if isinstance(msg, UserMessage):
            if isinstance(msg.content, str):
                result.append(gtypes.Content(role="user", parts=[gtypes.Part(text=msg.content)]))
            else:
                parts: list[Any] = []
                for block in msg.content:
                    if isinstance(block, TextContent):
                        parts.append(gtypes.Part(text=block.text))
                    elif isinstance(block, ImageContent):
                        parts.append(gtypes.Part(
                            inline_data=gtypes.Blob(
                                mime_type=block.mime_type,
                                data=block.data,
                            )
                        ))
                result.append(gtypes.Content(role="user", parts=parts))

        elif isinstance(msg, AssistantMessage):
            parts = []
            for block in msg.content:
                if isinstance(block, TextContent):
                    parts.append(gtypes.Part(text=block.text))
                elif isinstance(block, ToolCall):
                    fc_kwargs: dict[str, Any] = {
                        "function_call": gtypes.FunctionCall(
                            name=block.name,
                            args=block.arguments,
                        )
                    }
                    # Restore thought_signature (required when thinking mode was on)
                    if block.thought_signature:
                        import base64
                        try:
                            fc_kwargs["thought_signature"] = base64.b64decode(block.thought_signature)
                        except Exception:
                            fc_kwargs["thought_signature"] = block.thought_signature.encode("utf-8")
                    parts.append(gtypes.Part(**fc_kwargs))
            if parts:
                result.append(gtypes.Content(role="model", parts=parts))

        elif isinstance(msg, ToolResultMessage):
            content_text = " ".join(
                b.text for b in msg.content if isinstance(b, TextContent)
            )
            result.append(gtypes.Content(
                role="user",
                parts=[gtypes.Part(
                    function_response=gtypes.FunctionResponse(
                        name=msg.tool_name,
                        response={"output": content_text},
                    )
                )],
            ))

    return result


def _build_config(
    model: Model,
    context: Context,
    opts: SimpleStreamOptions,
) -> Any:
    """Build GenerateContentConfig from options and context."""
    from google.genai import types as gtypes

    tools: list[Any] | None = None
    if context.tools:
        # The genai SDK's ``parameters=`` kwarg is Gemini's OpenAPI-3.0
        # subset slot (``parameters_json_schema=`` is the separate
        # fuller-mode kwarg). It rejects ``additionalProperties`` /
        # ``anyOf`` / ``$schema`` / … — so run through the
        # ``gemini_openapi`` dialect, which strips all of them. (Was
        # only popping ``$schema`` here, under-cleaning everything else.)
        from openprogram.providers._schema import normalize
        func_decls = []
        for tool in context.tools:
            params = normalize(tool.parameters, "gemini_openapi")
            func_decls.append(
                gtypes.FunctionDeclaration(
                    name=tool.name,
                    description=tool.description,
                    parameters=params,
                )
            )
        tools = [gtypes.Tool(function_declarations=func_decls)]

    # Thinking budget
    # gemini-3-pro-preview (and other Gemini thinking models) will think by
    # default, consuming output tokens before any text is produced.  When the
    # caller has not explicitly requested reasoning we disable thinking so that
    # the full max_output_tokens budget is available for the text response.
    if opts.reasoning:
        from openprogram.providers.thinking_spec import translate_reasoning
        budget = translate_reasoning(model.provider or "google", model.id, opts.reasoning)
        thinking_config: Any = gtypes.ThinkingConfig(
            thinking_budget=budget if isinstance(budget, int) else 8192
        )
    else:
        # thinking_budget=0 disables thinking on models that support it;
        # on non-thinking models this field is silently ignored.
        thinking_config: Any = gtypes.ThinkingConfig(thinking_budget=0)

    output = opts.response_format
    return gtypes.GenerateContentConfig(
        system_instruction=context.system_prompt or None,
        max_output_tokens=opts.max_tokens or None,
        temperature=opts.temperature,
        tools=tools,
        thinking_config=thinking_config,
        response_mime_type="application/json" if output is not None else None,
        response_json_schema=output.schema if output is not None else None,
    )


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


# ---------------------------------------------------------------------------
# stream_simple — main streaming entry point
# ---------------------------------------------------------------------------

def _map_finish_reason(reason: Any) -> str:
    value = str(getattr(reason, "value", reason) or "").upper().rsplit(".", 1)[-1]
    if value == "MAX_TOKENS":
        return "length"
    if value in {
        "SAFETY",
        "RECITATION",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "MALFORMED_FUNCTION_CALL",
        "IMAGE_SAFETY",
    }:
        return "error"
    return "stop"

async def stream_simple(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AsyncGenerator[AssistantMessageEvent, None]:
    """Stream a response from the Google Generative AI API using google.genai SDK."""
    validate_input_modalities(model, context)
    try:
        from google import genai
    except ImportError:
        raise ImportError(
            "The Google GenAI SDK is missing; reinstall the complete OpenProgram release."
        )

    opts = options or SimpleStreamOptions()
    api_key = opts.api_key

    # Configure SDK-level retry. google-genai exposes full
    # HttpRetryOptions; we match the other providers' shape:
    # 3 attempts, exponential backoff base 2, ±25% jitter, retry on
    # standard transient statuses. Env override via
    # ``OPENPROGRAM_GOOGLE_MAX_RETRIES``.
    from google.genai.types import HttpRetryOptions
    from openprogram.providers.utils.http_client import build_google_http_options
    from openprogram.security.url_policy import OwnerURLException, normalize_origin
    import os as _os
    from ..budget import provider_retry_attempts
    sdk_attempts = provider_retry_attempts(
        int(_os.environ.get("OPENPROGRAM_GOOGLE_MAX_RETRIES", "3")),
    )
    configured_origin = normalize_origin(
        getattr(model, "base_url", None)
        or "https://generativelanguage.googleapis.com"
    )
    http_options = build_google_http_options(
        configured_origin,
        owner_exception=OwnerURLException(
            consumer="provider.google.sdk", origin=configured_origin
        ),
        retry_options=HttpRetryOptions(
            attempts=sdk_attempts,
            initial_delay=1.0,
            max_delay=30.0,
            exp_base=2.0,
            jitter=0.25,
            http_status_codes=[429, 500, 502, 503, 504],
        ),
    )
    client = genai.Client(api_key=api_key, http_options=http_options)

    contents = _build_contents(context)
    config = _build_config(model, context, opts)

    partial = _make_empty_assistant(model)
    content_blocks: list[Any] = []
    current_block: TextContent | ThinkingContent | None = None
    usage_final = Usage()
    terminal_reason = "stop"

    _signal = getattr(opts, "signal", None)

    def _cancelled() -> bool:
        f = getattr(_signal, "is_set", None)
        return bool(_signal is not None and callable(f) and f())

    def _block_index() -> int:
        return len(content_blocks) - 1

    def _is_thinking_part(part: Any) -> bool:
        return getattr(part, "thought", False) is True

    def _retain_thought_signature(existing: str | None, new_sig: Any) -> str | None:
        if new_sig:
            import base64
            if isinstance(new_sig, bytes):
                return base64.b64encode(new_sig).decode("ascii")
            return str(new_sig)
        return existing

    def _merge_terminal_reason(current: str, new: str) -> str:
        priority = {"stop": 0, "length": 1, "error": 2}
        return new if priority.get(new, 0) > priority.get(current, 0) else current

    yield EventStart(type="start", partial=partial)

    try:
        stream = await client.aio.models.generate_content_stream(
            model=model.id,
            contents=contents,
            config=config,
        )

        async for chunk in stream:
            # Caller cancel (Stop button): raising abandons the SDK stream;
            # the except below finalizes the turn as "aborted".
            if _cancelled():
                from openprogram.providers.utils.errors import StreamAborted
                raise StreamAborted("stream cancelled by caller signal")
            if chunk.usage_metadata and chunk.usage_metadata.total_token_count:
                um = chunk.usage_metadata
                usage_final = Usage(
                    input=um.prompt_token_count or 0,
                    output=um.candidates_token_count or 0,
                    total_tokens=um.total_token_count or 0,
                )

            prompt_feedback = getattr(chunk, "prompt_feedback", None)
            block_reason = getattr(prompt_feedback, "block_reason", None)
            if block_reason is not None and str(
                getattr(block_reason, "value", block_reason)
            ) != "BLOCKED_REASON_UNSPECIFIED":
                terminal_reason = _merge_terminal_reason(terminal_reason, "error")

            for candidate in (chunk.candidates or []):
                finish_reason = getattr(candidate, "finish_reason", None)
                if finish_reason is not None:
                    terminal_reason = _merge_terminal_reason(
                        terminal_reason,
                        _map_finish_reason(finish_reason),
                    )
                if not candidate.content or not candidate.content.parts:
                    continue
                for part in candidate.content.parts:
                    # Handle function_call parts
                    fc = getattr(part, "function_call", None)
                    if fc:
                        # Close current block
                        if current_block is not None:
                            if isinstance(current_block, TextContent):
                                yield EventTextEnd(type="text_end", content_index=_block_index(), content=current_block.text, partial=partial)
                            elif isinstance(current_block, ThinkingContent):
                                yield EventThinkingEnd(type="thinking_end", content_index=_block_index(), content=current_block.thinking, partial=partial)
                            current_block = None

                        idx = len(content_blocks)
                        args = dict(fc.args) if fc.args else {}
                        ts_b64: str | None = None
                        ts_raw = getattr(part, "thought_signature", None)
                        if ts_raw:
                            import base64
                            if isinstance(ts_raw, bytes):
                                ts_b64 = base64.b64encode(ts_raw).decode("ascii")
                            elif isinstance(ts_raw, str):
                                ts_b64 = ts_raw
                        tc = ToolCall(
                            type="toolCall",
                            id=f"call_{idx}_{fc.name}",
                            name=fc.name,
                            arguments=args,
                            thought_signature=ts_b64,
                        )
                        content_blocks.append(tc)
                        partial = partial.model_copy(update={"content": list(content_blocks)})
                        yield EventToolCallStart(type="toolcall_start", content_index=idx, partial=partial)
                        yield EventToolCallDelta(type="toolcall_delta", content_index=idx, delta=json.dumps(args), partial=partial)
                        yield EventToolCallEnd(type="toolcall_end", content_index=idx, tool_call=tc, partial=partial)
                        continue

                    # Handle text/thinking parts
                    part_text = getattr(part, "text", None)
                    if part_text is not None:
                        is_thinking = _is_thinking_part(part)

                        # Check if we need to switch block type
                        if (current_block is None or
                            (is_thinking and not isinstance(current_block, ThinkingContent)) or
                            (not is_thinking and not isinstance(current_block, TextContent))):

                            # Close previous block
                            if current_block is not None:
                                if isinstance(current_block, TextContent):
                                    yield EventTextEnd(type="text_end", content_index=_block_index(), content=current_block.text, partial=partial)
                                elif isinstance(current_block, ThinkingContent):
                                    yield EventThinkingEnd(type="thinking_end", content_index=_block_index(), content=current_block.thinking, partial=partial)

                            # Start new block
                            if is_thinking:
                                current_block = ThinkingContent(type="thinking", thinking="")
                                content_blocks.append(current_block)
                                partial = partial.model_copy(update={"content": list(content_blocks)})
                                yield EventThinkingStart(type="thinking_start", content_index=_block_index(), partial=partial)
                            else:
                                current_block = TextContent(type="text", text="")
                                content_blocks.append(current_block)
                                partial = partial.model_copy(update={"content": list(content_blocks)})
                                yield EventTextStart(type="text_start", content_index=_block_index(), partial=partial)

                        # Append to current block
                        if isinstance(current_block, ThinkingContent):
                            current_block = ThinkingContent(
                                type="thinking",
                                thinking=current_block.thinking + part_text,
                                thinking_signature=_retain_thought_signature(
                                    getattr(current_block, "thinking_signature", None),
                                    getattr(part, "thought_signature", None),
                                ),
                            )
                            content_blocks[_block_index()] = current_block
                            partial = partial.model_copy(update={"content": list(content_blocks)})
                            yield EventThinkingDelta(type="thinking_delta", content_index=_block_index(), delta=part_text, partial=partial)
                        elif isinstance(current_block, TextContent):
                            current_block = TextContent(type="text", text=current_block.text + part_text)
                            content_blocks[_block_index()] = current_block
                            partial = partial.model_copy(update={"content": list(content_blocks)})
                            yield EventTextDelta(type="text_delta", content_index=_block_index(), delta=part_text, partial=partial)

        # Close final block
        if current_block is not None:
            if isinstance(current_block, TextContent):
                yield EventTextEnd(type="text_end", content_index=_block_index(), content=current_block.text, partial=partial)
            elif isinstance(current_block, ThinkingContent):
                yield EventThinkingEnd(type="thinking_end", content_index=_block_index(), content=current_block.thinking, partial=partial)

        has_tool_calls = any(isinstance(b, ToolCall) for b in content_blocks)
        stop_reason = (
            "toolUse" if has_tool_calls and terminal_reason == "stop" else terminal_reason
        )
        final_content = (
            content_blocks
            if terminal_reason == "stop"
            else [block for block in content_blocks if not isinstance(block, ToolCall)]
        )

        final = AssistantMessage(
            role="assistant",
            content=final_content,
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=usage_final,
            stop_reason=stop_reason,
            timestamp=int(time.time() * 1000),
        )
        if stop_reason == "error":
            yield EventError(type="error", reason="error", error=final)
        else:
            yield EventDone(type="done", reason=stop_reason, message=final)

    except Exception as e:
        # User cancel is not an error: finalize as "aborted" (anthropic's
        # cancel semantics), preserving whatever content already streamed.
        stop = "aborted" if _cancelled() else "error"
        error_msg = AssistantMessage(
            role="assistant",
            content=content_blocks or [TextContent(type="text", text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=usage_final,
            stop_reason=stop,
            error_message=str(e),
            timestamp=int(time.time() * 1000),
        )
        yield EventError(type="error", reason=stop, error=error_msg)

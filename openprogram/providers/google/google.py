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
    context: Context,
    opts: SimpleStreamOptions,
) -> Any:
    """Build GenerateContentConfig from options and context."""
    from google.genai import types as gtypes

    tools: list[Any] | None = None
    if context.tools:
        func_decls = []
        for tool in context.tools:
            params = dict(tool.parameters)
            params.pop("$schema", None)
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
        budget_map = {"minimal": 512, "low": 2048, "medium": 8192, "high": 24576, "xhigh": 32768}
        thinking_config: Any = gtypes.ThinkingConfig(
            thinking_budget=budget_map.get(opts.reasoning, 8192)
        )
    else:
        # thinking_budget=0 disables thinking on models that support it;
        # on non-thinking models this field is silently ignored.
        thinking_config: Any = gtypes.ThinkingConfig(thinking_budget=0)

    return gtypes.GenerateContentConfig(
        system_instruction=context.system_prompt or None,
        max_output_tokens=opts.max_tokens or None,
        temperature=opts.temperature,
        tools=tools,
        thinking_config=thinking_config,
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
        raise ImportError("google-genai package required: pip install google-genai")

    opts = options or SimpleStreamOptions()
    api_key = opts.api_key
    client = genai.Client(api_key=api_key)

    contents = _build_contents(context)
    config = _build_config(context, opts)

    partial = _make_empty_assistant(model)
    content_blocks: list[Any] = []
    current_block: TextContent | ThinkingContent | None = None
    usage_final = Usage()

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

    yield EventStart(type="start", partial=partial)

    try:
        stream = await client.aio.models.generate_content_stream(
            model=model.id,
            contents=contents,
            config=config,
        )

        async for chunk in stream:
            if chunk.usage_metadata and chunk.usage_metadata.total_token_count:
                um = chunk.usage_metadata
                usage_final = Usage(
                    input=um.prompt_token_count or 0,
                    output=um.candidates_token_count or 0,
                    total_tokens=um.total_token_count or 0,
                )

            for candidate in (chunk.candidates or []):
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
        stop_reason = "toolUse" if has_tool_calls else "stop"

        final = AssistantMessage(
            role="assistant",
            content=content_blocks,
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=usage_final,
            stop_reason=stop_reason,
            timestamp=int(time.time() * 1000),
        )
        yield EventDone(
            type="done",
            reason=stop_reason if stop_reason != "stop" else "stop",
            message=final,
        )

    except Exception as e:
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

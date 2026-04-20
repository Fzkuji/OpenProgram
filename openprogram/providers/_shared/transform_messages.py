"""
Cross-provider message transformation — mirrors packages/ai/src/providers/transform-messages.ts

Normalizes messages for cross-provider compatibility:
- Tool call ID normalization for providers with different ID format requirements
- Thinking block handling (keep signatures for same model, convert to text for different models)
- Signature stripping for cross-model handoffs
- Orphaned tool call handling (synthetic error results)
- Error/aborted message skipping
"""
from __future__ import annotations

import time
from typing import Any, Callable

from ..types import (
    AssistantMessage,
    Message,
    Model,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
)

NormalizeToolCallIdFn = Callable[[str, Model, AssistantMessage], str]


def transform_messages(
    messages: list[Message],
    model: Model,
    normalize_tool_call_id: NormalizeToolCallIdFn | None = None,
) -> list[Message]:
    """
    Transform messages for cross-provider compatibility.
    Mirrors transformMessages() in TypeScript.
    """
    tool_call_id_map: dict[str, str] = {}

    # First pass: transform content blocks
    transformed: list[Message] = []
    for msg in messages:
        if hasattr(msg, "role") and msg.role == "user":
            transformed.append(msg)
            continue

        if isinstance(msg, ToolResultMessage) or (hasattr(msg, "role") and msg.role == "toolResult"):
            normalized_id = tool_call_id_map.get(msg.tool_call_id)
            if normalized_id and normalized_id != msg.tool_call_id:
                transformed.append(ToolResultMessage(
                    role="toolResult",
                    tool_call_id=normalized_id,
                    tool_name=msg.tool_name,
                    content=msg.content,
                    details=msg.details,
                    is_error=msg.is_error,
                    timestamp=msg.timestamp,
                ))
            else:
                transformed.append(msg)
            continue

        if isinstance(msg, AssistantMessage) or (hasattr(msg, "role") and msg.role == "assistant"):
            is_same_model = (
                getattr(msg, "provider", None) == model.provider
                and getattr(msg, "api", None) == model.api
                and getattr(msg, "model", None) == model.id
            )

            new_content: list[Any] = []
            for block in msg.content:
                if isinstance(block, ThinkingContent):
                    if getattr(block, "redacted", False):
                        # Redacted blocks: opaque encrypted payload, valid only for same model
                        if is_same_model:
                            new_content.append(block)
                        # else: drop silently — cannot convert to text
                        continue
                    if is_same_model and getattr(block, "thinking_signature", None):
                        new_content.append(block)
                    elif not block.thinking or block.thinking.strip() == "":
                        continue
                    elif is_same_model:
                        new_content.append(block)
                    else:
                        new_content.append(TextContent(type="text", text=block.thinking))

                elif isinstance(block, TextContent):
                    if is_same_model:
                        new_content.append(block)
                    else:
                        new_content.append(TextContent(type="text", text=block.text))

                elif isinstance(block, ToolCall):
                    normalized_tc = block

                    if not is_same_model and getattr(block, "thought_signature", None):
                        normalized_tc = ToolCall(
                            type="toolCall",
                            id=block.id,
                            name=block.name,
                            arguments=block.arguments,
                        )

                    if not is_same_model and normalize_tool_call_id:
                        normalized_id = normalize_tool_call_id(block.id, model, msg)
                        if normalized_id != block.id:
                            tool_call_id_map[block.id] = normalized_id
                            normalized_tc = ToolCall(
                                type="toolCall",
                                id=normalized_id,
                                name=normalized_tc.name,
                                arguments=normalized_tc.arguments,
                            )

                    new_content.append(normalized_tc)
                else:
                    new_content.append(block)

            transformed.append(AssistantMessage(
                role="assistant",
                content=new_content,
                api=msg.api,
                provider=msg.provider,
                model=msg.model,
                usage=msg.usage,
                stop_reason=msg.stop_reason,
                error_message=getattr(msg, "error_message", None),
                timestamp=msg.timestamp,
            ))
            continue

        transformed.append(msg)

    # Second pass: insert synthetic tool results for orphaned calls + skip error/aborted
    result: list[Message] = []
    pending_tool_calls: list[ToolCall] = []
    existing_tool_result_ids: set[str] = set()

    for msg in transformed:
        if isinstance(msg, AssistantMessage) or (hasattr(msg, "role") and msg.role == "assistant"):
            if pending_tool_calls:
                for tc in pending_tool_calls:
                    if tc.id not in existing_tool_result_ids:
                        result.append(ToolResultMessage(
                            role="toolResult",
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                            content=[TextContent(type="text", text="No result provided")],
                            is_error=True,
                            timestamp=int(time.time() * 1000),
                        ))
                pending_tool_calls = []
                existing_tool_result_ids = set()

            if getattr(msg, "stop_reason", None) in ("error", "aborted"):
                continue

            tool_calls = [c for c in msg.content if isinstance(c, ToolCall)]
            if tool_calls:
                pending_tool_calls = tool_calls
                existing_tool_result_ids = set()

            result.append(msg)

        elif isinstance(msg, ToolResultMessage) or (hasattr(msg, "role") and msg.role == "toolResult"):
            existing_tool_result_ids.add(msg.tool_call_id)
            result.append(msg)

        elif hasattr(msg, "role") and msg.role == "user":
            if pending_tool_calls:
                for tc in pending_tool_calls:
                    if tc.id not in existing_tool_result_ids:
                        result.append(ToolResultMessage(
                            role="toolResult",
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                            content=[TextContent(type="text", text="No result provided")],
                            is_error=True,
                            timestamp=int(time.time() * 1000),
                        ))
                pending_tool_calls = []
                existing_tool_result_ids = set()
            result.append(msg)
        else:
            result.append(msg)

    return result

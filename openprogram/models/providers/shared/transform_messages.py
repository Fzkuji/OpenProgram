"""
Cross-provider message transformation — mirrors packages/ai/src/providers/transform-messages.ts

Normalizes messages for cross-provider compatibility:
- Tool call ID normalization for providers with different ID format requirements
- Thinking block handling (keep signatures for same model, convert to text for different models)
- Signature stripping for cross-model handoffs
- Orphaned tool call handling (synthetic error results)
- Interrupted-history repair (request projection only)
"""
from __future__ import annotations

from typing import Any, Callable

from ..types import (
    AssistantMessage,
    Message,
    Model,
    ServerToolUse,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
)

NormalizeToolCallIdFn = Callable[[str, Model, AssistantMessage], str]


_INTERRUPTED = "[The saved assistant response was interrupted; it is not a completed response.]"
_REPAIR_SOURCE = "interrupted_history"


def _synthetic(message: ToolResultMessage) -> bool:
    return isinstance(message.details, dict) and message.details.get("source") == _REPAIR_SOURCE


def _repair_tool_results(messages: list[Message]) -> list[Message]:
    """Pair saved results before conversion; never mutate the execution ledger.

    Results can arrive after a user message or an earlier repair placeholder.
    Bind them to the nearest preceding occurrence of their original call ID,
    before a provider normalizer can change it. Unpaired protocol results stay
    in the original history, but cannot be submitted as valid tool responses.
    """
    owners: dict[str, int] = {}
    outputs: dict[tuple[int, str], ToolResultMessage] = {}
    for index, msg in enumerate(messages):
        if getattr(msg, "role", None) == "assistant":
            seen = set()
            for block in msg.content:
                if isinstance(block, ToolCall):
                    if block.id in seen:
                        raise ValueError("Saved assistant message has duplicate tool call IDs")
                    seen.add(block.id)
                    owners[block.id] = index
        elif getattr(msg, "role", None) == "toolResult" and msg.tool_call_id in owners:
            key = (owners[msg.tool_call_id], msg.tool_call_id)
            previous = outputs.get(key)
            # A saved real receipt (including a real error) outranks a repair.
            # For repeated saved receipts, use the latest observed output.
            if previous is None or _synthetic(previous) or not _synthetic(msg):
                outputs[key] = msg

    repaired: list[Message] = []
    for index, msg in enumerate(messages):
        if getattr(msg, "role", None) == "toolResult":
            continue
        if getattr(msg, "role", None) != "assistant":
            repaired.append(msg)
            continue
        if getattr(msg, "stop_reason", None) in {"error", "aborted"}:
            # Keep committed partial text and calls, not incomplete reasoning
            # signatures. The original error and usage remain untouched.
            content = [block for block in msg.content if not isinstance(block, ThinkingContent)]
            if not any(isinstance(block, TextContent) and block.text == _INTERRUPTED for block in content):
                content.insert(0, TextContent(text=_INTERRUPTED))
            msg = msg.model_copy(update={"content": content})
        repaired.append(msg)
        for block in msg.content:
            if not isinstance(block, ToolCall):
                continue
            output = outputs.get((index, block.id))
            if output is None:
                output = ToolResultMessage(
                    tool_call_id=block.id, tool_name=block.name,
                    content=[TextContent(text=(
                        "Tool result is unavailable in saved history. Execution may have started; "
                        "its outcome is unknown. Check current state before retrying. "
                        "This placeholder does not confirm success or failure."
                    ))],
                    details={"source": _REPAIR_SOURCE, "outcome": "unknown"},
                    is_error=True, timestamp=msg.timestamp,
                )
            repaired.append(output)
    return repaired


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
    for msg in _repair_tool_results(messages):
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
            # Results are now adjacent to this occurrence, so a mapping from
            # an earlier foreign turn must not rewrite a reused same-model ID.
            tool_call_id_map = {}
            is_same_model = (
                getattr(msg, "provider", None) == model.provider
                and getattr(msg, "api", None) == model.api
                and getattr(msg, "model", None) == model.id
            )

            new_content: list[Any] = []
            for block in msg.content:
                if isinstance(block, ServerToolUse) or (
                    isinstance(block, dict) and block.get("type") == "serverToolUse"
                ):
                    # A provider-executed search only means something to the
                    # API that ran it (its native item shape); another API
                    # drops it, as it drops foreign thinking signatures. It
                    # never pairs with a toolResult, so no repair either.
                    if getattr(msg, "api", None) == model.api:
                        new_content.append(
                            block if isinstance(block, ServerToolUse)
                            else ServerToolUse.model_validate(block)
                        )
                    continue
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
                carried_tools=getattr(msg, "carried_tools", None),
                timestamp=msg.timestamp,
            ))
            continue

        transformed.append(msg)

    # Each group of tool results is followed by the instructions its calls
    # brought along (``tool_attachments``), the same in the turn and after.
    from .tool_attachments import with_attachment_messages
    from .media_budget import bound_images, without_unsupported_images
    # Then the media the request cannot carry (``media_budget``).
    return bound_images(without_unsupported_images(with_attachment_messages(transformed), model))

"""
Shared utilities for Google Generative AI and Google Vertex AI providers.

Handles message/tool conversion to Gemini Content format, thought signature
management, tool call ID normalisation, and stop reason mapping.

Mirrors google-shared.ts
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from openprogram.providers.utils.sanitize_unicode import sanitize_surrogates

if TYPE_CHECKING:
    from openprogram.providers.types import Context, Model, StopReason, Tool

# Models accessed via Google APIs that require explicit tool call IDs
_REQUIRES_TOOL_CALL_ID_PREFIXES = ("claude-", "gpt-oss-")

# Base64 validation pattern for thought signatures
_BASE64_PATTERN = re.compile(r"^[A-Za-z0-9+/]+=*$")

# Sentinel that tells the Gemini API to skip thought signature validation.
# Used for unsigned function call parts replayed from other providers.
_SKIP_THOUGHT_SIGNATURE = "skip_thought_signature_validator"


# ---------------------------------------------------------------------------
# Thought signature helpers
# ---------------------------------------------------------------------------

def is_thinking_part(part: dict[str, Any]) -> bool:
    """Return True if a Gemini Part represents a thinking (thought summary) block."""
    return part.get("thought") is True


def retain_thought_signature(
    existing: str | None,
    incoming: str | None,
) -> str | None:
    """Preserve the last non-empty thought signature within a streaming block."""
    if isinstance(incoming, str) and incoming:
        return incoming
    return existing


def _is_valid_thought_signature(signature: str | None) -> bool:
    if not signature:
        return False
    if len(signature) % 4 != 0:
        return False
    return bool(_BASE64_PATTERN.match(signature))


def _resolve_thought_signature(
    is_same_provider_and_model: bool,
    signature: str | None,
) -> str | None:
    return signature if is_same_provider_and_model and _is_valid_thought_signature(signature) else None


# ---------------------------------------------------------------------------
# Tool call ID helpers
# ---------------------------------------------------------------------------

def requires_tool_call_id(model_id: str) -> bool:
    """Return True for models that require explicit tool call IDs via Google APIs."""
    return any(model_id.startswith(p) for p in _REQUIRES_TOOL_CALL_ID_PREFIXES)


# ---------------------------------------------------------------------------
# Message conversion
# ---------------------------------------------------------------------------

def convert_messages(model: "Model", context: "Context") -> list[dict[str, Any]]:
    """Convert internal messages to Gemini Content[] format."""
    from openprogram.providers.providers.transform_messages import transform_messages

    def normalize_tool_call_id(id_: str) -> str:
        if not requires_tool_call_id(model.id):
            return id_
        return re.sub(r"[^a-zA-Z0-9_-]", "_", id_)[:64]

    contents: list[dict[str, Any]] = []
    transformed = transform_messages(context.messages, model, normalize_tool_call_id)

    for msg in transformed:
        role = getattr(msg, "role", None)

        if role == "user":
            content_val = msg.content
            if isinstance(content_val, str):
                contents.append({"role": "user", "parts": [{"text": sanitize_surrogates(content_val)}]})
            else:
                parts: list[dict[str, Any]] = []
                for item in content_val:
                    item_type = getattr(item, "type", None)
                    if item_type == "text":
                        parts.append({"text": sanitize_surrogates(item.text)})
                    elif item_type == "image":
                        parts.append({"inlineData": {"mimeType": item.mime_type, "data": item.data}})

                # Filter images if model doesn't support them
                if "image" not in (model.input or []):
                    parts = [p for p in parts if "text" in p]

                if not parts:
                    continue
                contents.append({"role": "user", "parts": parts})

        elif role == "assistant":
            parts = []
            is_same = msg.provider == model.provider and msg.model == model.id

            for block in msg.content:
                block_type = getattr(block, "type", None)

                if block_type == "text":
                    text = getattr(block, "text", "") or ""
                    if not text.strip():
                        continue
                    sig = _resolve_thought_signature(is_same, getattr(block, "text_signature", None))
                    part: dict[str, Any] = {"text": sanitize_surrogates(text)}
                    if sig:
                        part["thoughtSignature"] = sig
                    parts.append(part)

                elif block_type == "thinking":
                    thinking = getattr(block, "thinking", "") or ""
                    if not thinking.strip():
                        continue
                    if is_same:
                        sig = _resolve_thought_signature(is_same, getattr(block, "thinking_signature", None))
                        part = {"thought": True, "text": sanitize_surrogates(thinking)}
                        if sig:
                            part["thoughtSignature"] = sig
                        parts.append(part)
                    else:
                        parts.append({"text": sanitize_surrogates(thinking)})

                elif block_type == "toolCall":
                    sig = _resolve_thought_signature(is_same, getattr(block, "thought_signature", None))
                    is_gemini3 = "gemini-3" in model.id.lower()
                    # For Gemini 3, use the skip_thought_signature_validator sentinel for unsigned tool calls
                    # instead of a text fallback — this properly handles cross-provider replay
                    effective_sig = sig or (_SKIP_THOUGHT_SIGNATURE if is_gemini3 else None)
                    func_call: dict[str, Any] = {
                        "name": block.name,
                        "args": getattr(block, "arguments", {}) or {},
                    }
                    if requires_tool_call_id(model.id):
                        func_call["id"] = block.id
                    part = {"functionCall": func_call}
                    if effective_sig:
                        part["thoughtSignature"] = effective_sig
                    parts.append(part)

            if not parts:
                continue
            contents.append({"role": "model", "parts": parts})

        elif role == "toolResult":
            from openprogram.providers.types import TextContent, ImageContent
            text_parts = [c for c in msg.content if getattr(c, "type", None) == "text"]
            text_result = "\n".join(c.text for c in text_parts)
            image_parts_raw = (
                [c for c in msg.content if getattr(c, "type", None) == "image"]
                if "image" in (model.input or [])
                else []
            )

            has_text = bool(text_result)
            has_images = bool(image_parts_raw)
            supports_multimodal = "gemini-3" in model.id

            response_value = (
                sanitize_surrogates(text_result) if has_text
                else "(see attached image)" if has_images
                else ""
            )

            image_inline = [
                {"inlineData": {"mimeType": img.mime_type, "data": img.data}}
                for img in image_parts_raw
            ]

            include_id = requires_tool_call_id(model.id)
            func_response: dict[str, Any] = {
                "name": msg.tool_name,
                "response": {"error": response_value} if msg.is_error else {"output": response_value},
            }
            if has_images and supports_multimodal:
                func_response["parts"] = image_inline
            if include_id:
                func_response["id"] = msg.tool_call_id

            func_response_part = {"functionResponse": func_response}

            # Merge into existing user turn with function responses if present
            last = contents[-1] if contents else None
            if last and last.get("role") == "user" and any("functionResponse" in p for p in last.get("parts", [])):
                last["parts"].append(func_response_part)
            else:
                contents.append({"role": "user", "parts": [func_response_part]})

            # For older models: images in a separate user message
            if has_images and not supports_multimodal:
                contents.append({
                    "role": "user",
                    "parts": [{"text": "Tool result image:"}, *image_inline],
                })

    return contents


# ---------------------------------------------------------------------------
# Tool conversion
# ---------------------------------------------------------------------------

def convert_tools(
    tools: "list[Tool]",
    use_parameters: bool = False,
) -> list[dict[str, Any]] | None:
    """Convert tools to Gemini function declarations format."""
    if not tools:
        return None
    key = "parameters" if use_parameters else "parametersJsonSchema"
    return [
        {
            "functionDeclarations": [
                {
                    "name": t.name,
                    "description": t.description,
                    key: t.parameters,
                }
                for t in tools
            ]
        }
    ]


# ---------------------------------------------------------------------------
# Stop reason mapping
# ---------------------------------------------------------------------------

# Gemini FinishReason string values → our StopReason
_FINISH_REASON_MAP: dict[str, "StopReason"] = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
}

_ERROR_FINISH_REASONS = {
    "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "SAFETY",
    "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT", "IMAGE_RECITATION",
    "IMAGE_OTHER", "RECITATION", "FINISH_REASON_UNSPECIFIED", "OTHER",
    "LANGUAGE", "MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL", "NO_IMAGE",
}


def map_stop_reason(reason: Any) -> "StopReason":
    """Map a Gemini FinishReason (enum or string) to our StopReason."""
    reason_str = str(reason).upper() if reason is not None else "STOP"
    if reason_str in _FINISH_REASON_MAP:
        return _FINISH_REASON_MAP[reason_str]
    if reason_str in _ERROR_FINISH_REASONS:
        return "error"
    return "stop"


def map_stop_reason_string(reason: str) -> "StopReason":
    """Map a raw finish reason string to StopReason."""
    return _FINISH_REASON_MAP.get(reason.upper(), "error")


# ---------------------------------------------------------------------------
# Tool choice mapping
# ---------------------------------------------------------------------------

def map_tool_choice(choice: str) -> str:
    """Map a tool choice string to Gemini FunctionCallingConfigMode value."""
    mapping = {"auto": "AUTO", "none": "NONE", "any": "ANY"}
    return mapping.get(choice, "AUTO")

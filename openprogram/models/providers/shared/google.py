"""
Shared utilities for Google Generative AI and Google Vertex AI providers.

Handles message/tool conversion to Gemini Content format, thought signature
management, tool call ID normalisation, and stop reason mapping.

Mirrors google-shared.ts
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from openprogram.models.providers.utils.sanitize_unicode import sanitize_surrogates

if TYPE_CHECKING:
    from openprogram.models.providers.types import Context, Model, StopReason, Tool

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
    from openprogram.models.providers.shared.transform_messages import transform_messages

    # transform_messages 用 3 参签名 (id, model, msg) 调; 闭包里 model
    # 已经在了, msg 这里不用, 但参数数量必须对齐。
    def normalize_tool_call_id(id_: str, _model=None, _msg=None) -> str:
        if not requires_tool_call_id(model.id):
            return id_
        return re.sub(r"[^a-zA-Z0-9_-]", "_", id_)[:64]

    contents: list[dict[str, Any]] = []
    transformed = transform_messages(context.messages, model, normalize_tool_call_id)

    pending_audio: list[dict[str, Any]] = []
    for msg in transformed:
        role = getattr(msg, "role", None)
        if pending_audio and role != "toolResult":
            contents.append({"role": "user", "parts": pending_audio})
            pending_audio = []

        if role == "user":
            content_val = msg.content
            if isinstance(content_val, str):
                contents.append({"role": "user", "parts": [{"text": sanitize_surrogates(content_val)}]})
            else:
                model_input = model.input or []
                parts: list[dict[str, Any]] = []
                for item in content_val:
                    item_type = getattr(item, "type", None)
                    if item_type == "text":
                        parts.append({"text": sanitize_surrogates(item.text)})
                    elif item_type in ("image", "video", "audio") and item_type in model_input:
                        parts.append({"inlineData": {"mimeType": item.mime_type, "data": item.data}})

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

            # A recording a tool returned, heard by models that accept audio.
            audio_inline = [
                {"inlineData": {"mimeType": clip.mime_type, "data": clip.data}}
                for clip in msg.content
                if getattr(clip, "type", None) == "audio" and "audio" in (model.input or [])
            ]
            if audio_inline:
                # Held until the run of function responses ends, which must stay together.
                pending_audio += [{"text": f"Audio returned by {msg.tool_name}:"}, *audio_inline]

    if pending_audio:
        contents.append({"role": "user", "parts": pending_audio})
    return _merge_adjacent_user_turns(contents)


def _merge_adjacent_user_turns(contents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One user turn for two that follow each other with no model turn between.

    A reply stopped before it said anything is not sent (``context.render``),
    so the person's next message follows the one it answered; Gemini wants
    the roles to alternate. Parts keep their order; a turn holding function
    responses is left as it is.
    """
    def responses(turn: dict[str, Any]) -> bool:
        return any(isinstance(p, dict) and "functionResponse" in p for p in turn.get("parts") or [])

    merged: list[dict[str, Any]] = []
    for turn in contents:
        previous = merged[-1] if merged else None
        if (previous is not None and previous.get("role") == "user" and turn.get("role") == "user"
                and not responses(previous) and not responses(turn)):
            merged[-1] = {**previous, "parts": [*previous["parts"], *turn["parts"]]}
            continue
        merged.append(turn)
    return merged


# ---------------------------------------------------------------------------
# Tool conversion
# ---------------------------------------------------------------------------

def convert_tools(
    tools: "list[Tool]",
    use_parameters: bool = False,
) -> list[dict[str, Any]] | None:
    """Convert tools to Gemini function declarations format.

    Two schema slots, picked by ``use_parameters``:
      * ``parameters`` — Gemini's OpenAPI-3.0 subset. REJECTS
        ``additionalProperties`` / ``anyOf`` / ``$schema`` / … so the
        schema MUST run through the ``gemini_openapi`` dialect first
        (was raw-passed before → latent 400s).
      * ``parametersJsonSchema`` — the fuller JSON-Schema mode; standard
        schema is accepted, so ``passthrough`` (deep-copy only).
    """
    if not tools:
        return None
    from openprogram.models.providers.schema import normalize
    key = "parameters" if use_parameters else "parametersJsonSchema"
    dialect = "gemini_openapi" if use_parameters else "passthrough"
    return [
        {
            "functionDeclarations": [
                {
                    "name": t.name,
                    "description": t.description,
                    key: normalize(t.parameters, dialect),
                }
                for t in tools
            ]
        }
    ]


# ---------------------------------------------------------------------------
# Provider-side web search (Google Search grounding)
# ---------------------------------------------------------------------------

GOOGLE_SEARCH_TOOL: dict[str, Any] = {"google_search": {}}


def google_search_applies(has_function_tools: bool) -> bool:
    """Whether a ``web_search=True`` request may carry the google_search tool.

    generateContent rejects google_search next to function declarations on
    Gemini 2.x, and Google documents the combination for Gemini 3 only on
    the Interactions API (which these adapters do not speak). Function tools
    win: a request that has them goes without grounding rather than failing.
    """
    return not has_function_tools


def _get(obj: Any, *names: str) -> Any:
    """First present field among camelCase / snake_case spellings."""
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _char_offset(text: str, byte_offset: Any) -> int | None:
    """Gemini segment offsets count UTF-8 bytes; citations count characters."""
    if not isinstance(byte_offset, int):
        return None
    return len(text.encode("utf-8")[:byte_offset].decode("utf-8", errors="ignore"))


def grounding_search_blocks(metadata: Any, first_id: int = 0) -> list[dict[str, Any]]:
    """``serverToolUse`` block dicts for a candidate's ``groundingMetadata``.

    One block per ``webSearchQueries`` entry (one block without a query when
    only sources came back); every block lists all ``groundingChunks`` web
    sources, since Gemini does not say which query found which page.
    """
    if not metadata:
        return []
    results: list[dict[str, Any]] = []
    for chunk in _get(metadata, "groundingChunks", "grounding_chunks") or []:
        web = _get(chunk, "web")
        url = _get(web, "uri") if web is not None else None
        if url:
            results.append({"url": url, "title": _get(web, "title") or url})
    queries = [q for q in (_get(metadata, "webSearchQueries", "web_search_queries") or []) if q]
    if not queries and not results:
        return []
    inputs = [{"query": q} for q in queries] or [{}]
    return [
        {
            "type": "serverToolUse",
            "id": f"google_search_{first_id + n}",
            "name": "web_search",
            "input": query_input,
            "status": "completed",
            "results": list(results),
        }
        for n, query_input in enumerate(inputs)
    ]


def grounding_citations(metadata: Any, text: str) -> list[dict[str, Any]]:
    """``[{url, title, start, end}]`` from ``groundingSupports`` over ``text``."""
    if not metadata:
        return []
    chunks = _get(metadata, "groundingChunks", "grounding_chunks") or []
    citations: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for support in _get(metadata, "groundingSupports", "grounding_supports") or []:
        segment = _get(support, "segment") or {}
        start = _char_offset(text, _get(segment, "startIndex", "start_index") or 0)
        end = _char_offset(text, _get(segment, "endIndex", "end_index"))
        for idx in _get(support, "groundingChunkIndices", "grounding_chunk_indices") or []:
            if not isinstance(idx, int) or not 0 <= idx < len(chunks):
                continue
            web = _get(chunks[idx], "web")
            url = _get(web, "uri") if web is not None else None
            if not url or (url, start, end) in seen:
                continue
            seen.add((url, start, end))
            citations.append({"url": url, "title": _get(web, "title"), "start": start, "end": end})
    return citations


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

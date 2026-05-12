"""
Token counter: per-message token usage with explicit source tagging.

Routing (best → worst):
  1. provider_usage  — AssistantMessage already carries Usage with real
     input/output/cache_read/cache_write from the provider. Always wins.
  2. tiktoken        — for any OpenAI-family model (provider=openai,
     openai-codex, azure-openai-responses, openrouter, ...).
  3. anthropic_count — anthropic.messages.count_tokens() if SDK + API
     key available, for claude-* models.
  4. heuristic       — 4 chars/token (2 for JSON dense content). Always
     usable, never accurate. Tagged so the UI can warn.

Every result carries the source so callers can disclose precision.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openprogram.providers.types import Model


@dataclass
class TokenCount:
    """Per-message token breakdown + source tag."""
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    source: str = "heuristic"

    @property
    def total(self) -> int:
        # cache_read counts against the prompt budget too; cache_write is
        # a one-time creation cost. For "how much context did this message
        # consume" the relevant number is input + cache_read + output.
        return self.input + self.cache_read + self.output


# ─── OpenAI-family tiktoken cache ───────────────────────────────────────

_TIKTOKEN_ENCODERS: dict[str, Any] = {}


def _tiktoken_encoder(model_id: str):
    """Return a cached tiktoken encoder for model_id, or None if tiktoken
    isn't installed / the model has no mapping."""
    if model_id in _TIKTOKEN_ENCODERS:
        return _TIKTOKEN_ENCODERS[model_id]
    try:
        import tiktoken
    except ImportError:
        _TIKTOKEN_ENCODERS[model_id] = None
        return None
    try:
        enc = tiktoken.encoding_for_model(model_id)
    except Exception:
        # Newer models often map to o200k_base (gpt-4o, o1, etc.) or
        # cl100k_base (gpt-4, gpt-3.5). Try those before giving up.
        try:
            if model_id.startswith(("gpt-4o", "gpt-5", "o1", "o3", "o4")):
                enc = tiktoken.get_encoding("o200k_base")
            else:
                enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            enc = None
    _TIKTOKEN_ENCODERS[model_id] = enc
    return enc


_OPENAI_FAMILY_PROVIDERS = frozenset({
    "openai", "openai-codex", "azure-openai-responses",
    "azure-openai", "opencode", "openrouter", "github-copilot",
    "vercel-ai-gateway", "groq", "cerebras", "kimi-coding",
    "mistral", "xai", "zai", "minimax", "minimax-cn", "huggingface",
})

_ANTHROPIC_FAMILY_PROVIDERS = frozenset({
    "anthropic", "claude-code", "amazon-bedrock",
})

_GOOGLE_FAMILY_PROVIDERS = frozenset({
    "google", "gemini-subscription",
})


def _extract_text(msg_content: Any) -> str:
    """Flatten message content (string or list of blocks) into plain text.

    Best-effort; we just need a char-count surrogate for unknown blocks
    so the heuristic has something to chew on. Images/audio/video get a
    fixed token estimate via _estimate_modality_tokens.
    """
    if msg_content is None:
        return ""
    if isinstance(msg_content, str):
        return msg_content
    if isinstance(msg_content, list):
        parts: list[str] = []
        for block in msg_content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if isinstance(block, dict):
                # text / thinking / image / etc.
                t = block.get("type")
                if t == "text" or t == "thinking":
                    parts.append(str(block.get("text") or block.get("thinking") or ""))
                elif t == "toolCall":
                    parts.append(block.get("name", ""))
                    args = block.get("arguments")
                    if args is not None:
                        try:
                            parts.append(json.dumps(args))
                        except Exception:
                            parts.append(str(args))
                else:
                    # Unknown block — dump it as JSON so chars matter.
                    try:
                        parts.append(json.dumps(block, default=str))
                    except Exception:
                        parts.append(str(block))
                continue
            # Pydantic model: use model_dump if available.
            if hasattr(block, "model_dump"):
                try:
                    parts.append(json.dumps(block.model_dump(), default=str))
                    continue
                except Exception:
                    pass
            parts.append(str(block))
        return "\n".join(parts)
    # Fallback: stringify.
    return str(msg_content)


def _estimate_modality_tokens(msg_content: Any) -> int:
    """Image/video/audio blocks contribute non-text tokens. We use a flat
    estimate so the heuristic doesn't undercount a vision-heavy turn.
    These numbers are provider-typical mid-points, not exact.
    """
    if not isinstance(msg_content, list):
        return 0
    extra = 0
    for block in msg_content:
        t = None
        if isinstance(block, dict):
            t = block.get("type")
        elif hasattr(block, "type"):
            t = getattr(block, "type", None)
        if t == "image":
            extra += 1500   # ~1024px image, mid-range
        elif t == "video":
            extra += 4000   # short clip — providers vary wildly
        elif t == "audio":
            extra += 1500   # ~1min audio
    return extra


def _chars_to_tokens(text: str, dense_json: bool = False) -> int:
    """Heuristic: 4 chars/token for prose, 2 for dense JSON / code."""
    if not text:
        return 0
    ratio = 2.0 if dense_json else 4.0
    return max(1, int(round(len(text) / ratio)))


# ─── Provider-usage extraction ──────────────────────────────────────────

def _from_provider_usage(msg: dict[str, Any]) -> TokenCount | None:
    """If this is an assistant message with a real Usage payload, use it
    verbatim. AssistantMessage.usage is populated by every provider's
    stream handler from the API's reported usage block.
    """
    if msg.get("role") not in ("assistant", "model"):
        return None
    usage = msg.get("usage")
    if not usage:
        return None
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if not isinstance(usage, dict):
        return None
    inp = int(usage.get("input") or 0)
    out = int(usage.get("output") or 0)
    cr = int(usage.get("cache_read") or 0)
    cw = int(usage.get("cache_write") or 0)
    if inp == 0 and out == 0 and cr == 0 and cw == 0:
        return None
    # The "input" the provider reports excludes cached tokens for
    # Anthropic; we keep that semantic (cache_read is its own column).
    return TokenCount(
        input=inp, output=out,
        cache_read=cr, cache_write=cw,
        source="provider_usage",
    )


# ─── tiktoken path ──────────────────────────────────────────────────────

def _from_tiktoken(msg: dict[str, Any], model: "Model | None") -> TokenCount | None:
    if model is None:
        return None
    provider = getattr(model, "provider", "") or ""
    if provider not in _OPENAI_FAMILY_PROVIDERS:
        return None
    enc = _tiktoken_encoder(getattr(model, "id", "") or "")
    if enc is None:
        return None
    text = _extract_text(msg.get("content"))
    role = msg.get("role")
    extra = _estimate_modality_tokens(msg.get("content"))
    try:
        n = len(enc.encode(text)) + extra
    except Exception:
        return None
    if role in ("assistant", "model"):
        return TokenCount(output=n, source="tiktoken")
    return TokenCount(input=n, source="tiktoken")


# ─── Anthropic count_tokens path ───────────────────────────────────────

def _from_anthropic_count(msg: dict[str, Any], model: "Model | None") -> TokenCount | None:
    # Single-message counting via anthropic SDK is expensive (an API
    # round-trip). We skip it in append_message() — the heuristic is
    # close enough until the message lands in a real provider call,
    # at which point provider_usage will overwrite it.
    return None


# ─── Heuristic path ─────────────────────────────────────────────────────

def _from_heuristic(msg: dict[str, Any]) -> TokenCount:
    content = msg.get("content")
    text = _extract_text(content)
    extra = _estimate_modality_tokens(content)
    # If the content carries JSON-heavy blocks (tool calls / tool
    # results), use the denser ratio.
    has_json = False
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") in ("toolCall", "tool_result"):
                has_json = True
                break
    if msg.get("role") in ("toolResult", "tool"):
        has_json = True
    n = _chars_to_tokens(text, dense_json=has_json) + extra
    if msg.get("role") in ("assistant", "model"):
        return TokenCount(output=n, source="heuristic")
    return TokenCount(input=n, source="heuristic")


# ─── Public entry point ────────────────────────────────────────────────

def count_tokens(
    msg: dict[str, Any], model: "Model | None" = None
) -> TokenCount | None:
    """Count tokens for a single message — only when an authoritative
    source is available.

    Returns None if neither the provider's own usage block nor a real
    tokenizer (tiktoken) can produce a number. The earlier heuristic
    (chars / 4) is intentionally NOT a fallback: any displayed token
    count must come from a real measurement, never an estimate.

    `msg` is the serialized message dict. `model` lets tiktoken pick
    the right encoding; pass None to skip tiktoken too.
    """
    result = _from_provider_usage(msg)
    if result is not None:
        return result
    result = _from_tiktoken(msg, model)
    if result is not None:
        return result
    return None

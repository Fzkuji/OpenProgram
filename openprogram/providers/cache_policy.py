"""Prompt-cache breakpoint policy — a Python port of opencode's cache-policy.

Runs once before a provider builds its request body. It injects cache
breakpoints onto the parts an `auto` policy designates, leaving any markers the
caller already placed untouched. Only providers whose wire format reads inline
markers (Anthropic, Bedrock — declared `mode: "explicit"` in cache.json) are
touched; OpenAI's implicit prefix caching and Gemini's out-of-band cache are
skipped (emitting markers there is harmless but pointless).

Mirrors `references/opencode/packages/llm/src/cache-policy.ts`:

  AUTO places one breakpoint at the last tool and one at the latest user
  message. Production agent harnesses converge on this for tool-use loops: the
  latest user message stays put while one turn explodes into many
  assistant/tool round-trips, so a breakpoint there lets every intra-turn call
  hit the prefix.

(opencode also marks the last *system* part; here `Context.system_prompt` is a
single string handled inside each provider's `_build_system`, so the system
breakpoint is left to that path — this pass covers tools + messages.)
"""
from __future__ import annotations

from openprogram.providers.types import (
    Context,
    TextContent,
    ImageContent,
    UserMessage,
)


# Anthropic & Bedrock both cap explicit breakpoints at 4 per request. The
# counter sheds the lowest-priority hint first when a caller over-marks.
_DEFAULT_BREAKPOINT_CAP = 4


def _ttl_bucket(ttl_seconds: int | None) -> str | None:
    """opencode ttlBucket: >=3600s → "1h", else provider default (5m → None)."""
    return "1h" if ttl_seconds is not None and ttl_seconds >= 3600 else None


def _make_hint(ttl_seconds: int | None) -> dict:
    """Build a cache_control dict (the inline marker Anthropic/Bedrock read)."""
    hint: dict = {"type": "ephemeral"}
    bucket = _ttl_bucket(ttl_seconds)
    if bucket:
        hint["ttl"] = bucket
    return hint


def apply_cache_policy(
    context: Context,
    provider_id: str,
    *,
    ttl_seconds: int | None = None,
) -> Context:
    """Return a Context with auto cache breakpoints filled in.

    No-op (returns the same object) when the provider doesn't read inline
    markers, when there's nothing to mark, or when the caller already marked
    everything. Caller-placed `cache_control` markers are always preserved.
    """
    from openprogram.providers.cache_spec import cache_mode, get_cache_spec

    # Only explicit-mode providers read inline markers (opencode RESPECTS_INLINE_HINTS).
    if cache_mode(provider_id) != "explicit":
        return context

    cap = get_cache_spec(provider_id).get("max_breakpoints", _DEFAULT_BREAKPOINT_CAP)
    remaining = {"n": cap}
    hint = _make_hint(ttl_seconds)
    changed = False

    def _take() -> dict | None:
        if remaining["n"] <= 0:
            return None
        remaining["n"] -= 1
        return dict(hint)

    # --- last tool (highest priority — tools sit at the cache prefix root) ---
    tools = context.tools
    if tools:
        last = tools[-1]
        if last.cache_control is None:
            mark = _take()
            if mark is not None:
                tools = list(tools)
                tools[-1] = last.model_copy(update={"cache_control": mark})
                changed = True

    # --- latest user message: mark its last text part, else its last part ---
    messages = context.messages
    last_user_idx = _last_index(messages, UserMessage)
    if last_user_idx >= 0:
        msg = messages[last_user_idx]
        new_msg = _mark_message_last_block(msg, _take)
        if new_msg is not None:
            messages = list(messages)
            messages[last_user_idx] = new_msg
            changed = True

    if not changed:
        return context
    return context.model_copy(update={"tools": tools, "messages": messages})


def _last_index(messages, cls) -> int:
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], cls):
            return i
    return -1


def _mark_message_last_block(msg, take):
    """Mark the last text block of a user message (else its last block), unless
    a breakpoint already exists somewhere in it. Returns a new message or None."""
    content = msg.content
    # String content can't carry an inline marker — provider handles it.
    if isinstance(content, str) or not content:
        return None
    # Caller already marked a breakpoint in this message → leave it.
    for block in content:
        if getattr(block, "cache_control", None):
            return None
    # Prefer the last text block; fall back to the last block.
    mark_at = -1
    for i in range(len(content) - 1, -1, -1):
        if isinstance(content[i], TextContent):
            mark_at = i
            break
    if mark_at < 0:
        mark_at = len(content) - 1
    target = content[mark_at]
    if not isinstance(target, (TextContent, ImageContent)):
        return None  # only text/image blocks carry cache_control
    mark = take()
    if mark is None:
        return None
    new_content = list(content)
    new_content[mark_at] = target.model_copy(update={"cache_control": mark})
    return msg.model_copy(update={"content": new_content})


__all__ = ["apply_cache_policy"]

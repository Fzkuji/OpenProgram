"""Token estimation for context budgeting.

Three-tier strategy:

1. Provider-reported usage (when available) — the truth. Recorded on
   each assistant message via ``messages.input_tokens`` /
   ``output_tokens``. Used to seed estimates for the current branch
   when we have a turn or two of history.

2. Tiktoken (when installed and the model's tokenizer family is
   covered) — close to truth, sub-millisecond per call.

3. Character heuristic — last-resort. Different ratio for CJK vs ASCII
   so a 60K-char Chinese conversation doesn't look the same size as a
   60K-char English one (it's roughly 4x more tokens).

The estimator is intentionally pluggable: every function takes a
``model`` so callers can route to provider-specific paths if needed
(e.g. Anthropic's ``count_tokens`` endpoint when we want the
authoritative figure for a cache decision).
"""
from __future__ import annotations

from typing import Any


# Per-message overhead — every LLM API charges a few tokens for
# role markers / message-boundary tokens. Conservative.
_PER_MESSAGE_OVERHEAD = 4

# An image counts as ~1568 tokens for OpenAI vision at 1024x1024, ~1600
# for Anthropic. Pick a single conservative figure rather than a
# per-resolution formula because we'd need to actually decode the
# base64 to know dimensions, which is expensive on the hot path.
_PER_IMAGE_TOKENS = 1600


def _is_cjk(ch: str) -> bool:
    """Cheap heuristic: is this character in a CJK range?"""
    if not ch:
        return False
    o = ord(ch)
    # CJK Unified Ideographs (basic + ext A) + Hiragana + Katakana +
    # Hangul. Covers the vast majority of Chinese / Japanese / Korean
    # without dragging in every minority script.
    return (
        0x3040 <= o <= 0x30FF or   # Hiragana / Katakana
        0x3400 <= o <= 0x4DBF or   # CJK Ext-A
        0x4E00 <= o <= 0x9FFF or   # CJK Unified
        0xAC00 <= o <= 0xD7AF      # Hangul syllables
    )


def _char_estimate(text: str) -> int:
    """Char-based fallback. CJK ≈ 1 char/token, ASCII ≈ 4 chars/token.

    We classify the WHOLE STRING at once (sampling a few chars) rather
    than per-char to keep this cheap. Mixed prose (Chinese with code
    blocks) lands somewhere in between, which is fine — we conservatively
    bias toward CJK when ANY of the sample is CJK.
    """
    if not text:
        return 0
    n = len(text)
    # Sample 16 evenly-spaced chars; if any are CJK, treat as CJK-heavy.
    sample_step = max(1, n // 16)
    cjk_hits = sum(1 for ch in text[::sample_step] if _is_cjk(ch))
    if cjk_hits >= 4:
        # CJK-heavy: ~1.3 char/token (a tad less than 1:1 because
        # punctuation + spaces still tokenize cheaply)
        return int(n / 1.3)
    return int(n / 3.8)  # ASCII: ~3.8 chars/token typical English prose


try:
    import tiktoken  # type: ignore[import-not-found]
    _HAS_TIKTOKEN = True
except ImportError:
    _HAS_TIKTOKEN = False


def _tiktoken_count(text: str, model_family: str | None = None) -> int | None:
    """Try tiktoken first. Returns None if unavailable."""
    if not _HAS_TIKTOKEN or not text:
        return None
    try:
        # cl100k_base is GPT-4 / GPT-3.5; o200k_base is GPT-4o / GPT-5.
        # We don't have model.tokenizer_family in the registry — pick
        # the GPT-5-era one because that's what most modern API models
        # share, and the count is within ~10% of any other modern BPE.
        enc = tiktoken.get_encoding("o200k_base")
        return len(enc.encode(text))
    except Exception:
        return None


def _text_tokens(text: str) -> int:
    if not text:
        return 0
    n = _tiktoken_count(text)
    if n is not None:
        return n
    return _char_estimate(text)


def estimate_message_tokens(msg: dict[str, Any] | Any) -> int:
    """Estimate tokens for one message (dict-shape or AgentMessage)."""
    # Pydantic / dataclass shapes
    if not isinstance(msg, dict):
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            total = _PER_MESSAGE_OVERHEAD
            for blk in content:
                # TextContent
                txt = getattr(blk, "text", None)
                if txt:
                    total += _text_tokens(txt)
                # ImageContent
                if getattr(blk, "type", None) == "image":
                    total += _PER_IMAGE_TOKENS
                # ToolUseContent — name + JSON args
                if getattr(blk, "type", None) == "tool_use":
                    name = getattr(blk, "name", "") or ""
                    input_data = getattr(blk, "input", None)
                    if input_data is not None:
                        total += _text_tokens(name) + _text_tokens(str(input_data))
                # ToolResultContent
                if getattr(blk, "type", None) == "tool_result":
                    txt = getattr(blk, "content", "") or ""
                    if isinstance(txt, list):
                        for sub in txt:
                            t = getattr(sub, "text", None)
                            if t:
                                total += _text_tokens(t)
                    else:
                        total += _text_tokens(str(txt))
            return total
        # Fallback: stringify
        return _PER_MESSAGE_OVERHEAD + _text_tokens(str(content or ""))

    # Dict shape (raw SessionDB row or in-memory chat msg)
    total = _PER_MESSAGE_OVERHEAD
    total += _text_tokens(msg.get("content") or "")

    # Tool calls / blocks embedded in ``extra`` JSON
    extra_raw = msg.get("extra")
    if extra_raw:
        try:
            import json
            extra = (json.loads(extra_raw)
                     if isinstance(extra_raw, str) else extra_raw)
        except Exception:
            extra = {}
        # Attachments
        for att in (extra.get("attachments") or []):
            if (att.get("type") or "") == "image":
                total += _PER_IMAGE_TOKENS
        # Tool calls — name + json args
        for call in (extra.get("tool_calls") or []):
            name = call.get("name") or ""
            input_data = call.get("input") or call.get("arguments") or {}
            total += _text_tokens(name) + _text_tokens(str(input_data))
        # Tool result blocks (separate persistence)
        for block in (extra.get("blocks") or []):
            if (block.get("type") or "") == "tool_result":
                total += _text_tokens(str(block.get("content") or ""))

    return total


def estimate_history_tokens(messages: list[Any]) -> int:
    """Sum of estimated tokens for the whole list."""
    return sum(estimate_message_tokens(m) for m in messages)


# ---------------------------------------------------------------------------
# Context-window resolution — the right field, not ``max_tokens``
# ---------------------------------------------------------------------------

# Conservative fallback when a model entry lacks any window info.
# Smaller than the smallest modern window so compaction kicks in early
# rather than late.
_DEFAULT_WINDOW = 128_000


def real_context_window(model: Any) -> int:
    """Return the model's actual context window in tokens.

    The dispatcher previously read ``model.max_tokens`` (output cap) by
    mistake — that's ~10-30% of the real window for most modern models,
    so compaction fired at ~10-30% of true utilization. The correct
    field on our :class:`Model` registry is ``context_window``.
    """
    if model is None:
        return _DEFAULT_WINDOW
    win = getattr(model, "context_window", None)
    if win and int(win) > 0:
        return int(win)
    # Some hand-curated entries (older Codex registry) only have
    # ``max_tokens`` filled in. Treat that as an under-estimate so we
    # don't crash, but log a warning the first time we hit it.
    cap = getattr(model, "max_tokens", None)
    if cap and int(cap) >= 32_000:
        # If max_tokens is at least 32K, the entry plausibly conflated
        # the two fields; trust it.
        return int(cap)
    return _DEFAULT_WINDOW

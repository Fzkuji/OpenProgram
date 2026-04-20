"""
Context overflow detection utilities.

Detects context window overflow errors from various LLM providers using
regex patterns and usage-based checks.

Mirrors overflow.ts
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.providers.types import AssistantMessage

# Regex patterns to detect context overflow errors from different providers.
OVERFLOW_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"prompt is too long", re.IGNORECASE),                         # Anthropic
    re.compile(r"input is too long for requested model", re.IGNORECASE),      # Amazon Bedrock
    re.compile(r"exceeds the context window", re.IGNORECASE),                  # OpenAI (Completions & Responses)
    re.compile(r"input token count.*exceeds the maximum", re.IGNORECASE),     # Google (Gemini)
    re.compile(r"maximum prompt length is \d+", re.IGNORECASE),               # xAI (Grok)
    re.compile(r"reduce the length of the messages", re.IGNORECASE),          # Groq
    re.compile(r"maximum context length is \d+ tokens", re.IGNORECASE),       # OpenRouter
    re.compile(r"exceeds the limit of \d+", re.IGNORECASE),                   # GitHub Copilot
    re.compile(r"exceeds the available context size", re.IGNORECASE),         # llama.cpp server
    re.compile(r"greater than the context length", re.IGNORECASE),            # LM Studio
    re.compile(r"context window exceeds limit", re.IGNORECASE),               # MiniMax
    re.compile(r"exceeded model token limit", re.IGNORECASE),                 # Kimi For Coding
    re.compile(r"too large for model with \d+ maximum context length", re.IGNORECASE),  # Mistral
    re.compile(r"model_context_window_exceeded", re.IGNORECASE),              # z.ai
    re.compile(r"context[_ ]length[_ ]exceeded", re.IGNORECASE),              # Generic fallback
    re.compile(r"too many tokens", re.IGNORECASE),                            # Generic fallback
    re.compile(r"token limit exceeded", re.IGNORECASE),                       # Generic fallback
]

_STATUS_CODE_RE = re.compile(r"^4(00|13)\s*(status code)?\s*\(no body\)", re.IGNORECASE)


def is_context_overflow(message: "AssistantMessage", context_window: int | None = None) -> bool:
    """Check if an assistant message represents a context overflow error.

    Args:
        message: The assistant message to check.
        context_window: Optional context window size for detecting silent overflow (z.ai style).

    Returns:
        True if the message indicates a context overflow.
    """
    # Case 1: Error-based overflow
    if message.stop_reason == "error" and message.error_message:
        if any(p.search(message.error_message) for p in OVERFLOW_PATTERNS):
            return True
        # Cerebras and Mistral return 400/413 with no body
        if _STATUS_CODE_RE.match(message.error_message):
            return True

    # Case 2: Silent overflow (z.ai style) — successful but usage exceeds context window
    if context_window and message.stop_reason == "stop":
        input_tokens = message.usage.input + message.usage.cache_read
        if input_tokens > context_window:
            return True

    return False


def get_overflow_patterns() -> list[re.Pattern[str]]:
    """Return the overflow patterns (for testing purposes)."""
    return list(OVERFLOW_PATTERNS)

"""
Retry logic for agent errors.

Extracted from pi_coding_agent.core.agent_session into a standalone module
so Runtime or custom clients can use it without depending on AgentSession.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from openprogram.providers.utils.overflow import is_context_overflow

_RETRY_PATTERN = re.compile(
    r"overloaded|rate.?limit|too many requests|429|500|502|503|504|"
    r"service.?unavailable|server error|internal error|connection.?error|"
    r"connection.?refused|other side closed|fetch failed|upstream.?connect|"
    r"reset before headers|terminated|retry delay",
    re.IGNORECASE,
)


@dataclass
class RetrySettings:
    """Auto-retry configuration."""
    enabled: bool = True
    max_retries: int = 3
    base_delay_ms: int = 2000


DEFAULT_RETRY_SETTINGS = RetrySettings()


def is_retryable_error(msg: Any, context_window: int = 0) -> bool:
    """True if this error response should trigger a retry.

    Context-overflow errors are *not* retryable — those should be handled by
    compaction instead. Only transient errors (rate limits, 5xx, connection
    issues) match the retry pattern.
    """
    if getattr(msg, "stop_reason", "") != "error":
        return False
    if context_window and is_context_overflow(msg, context_window):
        return False
    err = getattr(msg, "error_message", "") or ""
    return bool(_RETRY_PATTERN.search(err))


def compute_backoff_ms(attempt: int, base_delay_ms: int = 2000) -> int:
    """Exponential backoff delay: base * 2^(attempt-1).

    attempt=1 -> base, attempt=2 -> 2*base, attempt=3 -> 4*base, ...
    """
    if attempt < 1:
        return 0
    return base_delay_ms * (2 ** (attempt - 1))

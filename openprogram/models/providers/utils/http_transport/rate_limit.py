"""Parse provider rate-limit response headers into structured objects.

Providers return limit telemetry on ordinary responses, under two
naming schemes:

  * OpenAI:    ``x-ratelimit-{limit,remaining,reset}-{requests,tokens}``
  * Anthropic: ``anthropic-ratelimit-{requests,tokens,input-tokens,
               output-tokens}-{limit,remaining,reset}`` (API-key accounts)

``models/usage/quota.py`` turns these into quota rows. This module is the
parser; it has no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


def _int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


@dataclass
class RateLimitInfo:
    """Normalised rate-limit snapshot from one response's headers."""

    limit_requests: Optional[int] = None
    remaining_requests: Optional[int] = None
    reset_requests: Optional[str] = None
    limit_tokens: Optional[int] = None
    remaining_tokens: Optional[int] = None
    reset_tokens: Optional[str] = None
    limit_input_tokens: Optional[int] = None
    remaining_input_tokens: Optional[int] = None
    reset_input_tokens: Optional[str] = None
    limit_output_tokens: Optional[int] = None
    remaining_output_tokens: Optional[int] = None
    reset_output_tokens: Optional[str] = None
    raw: dict = field(default_factory=dict)

    @property
    def present(self) -> bool:
        """True if any rate-limit field was found."""
        return bool(self.raw)

    @property
    def is_throttled(self) -> bool:
        """True when a request or token bucket is exhausted."""
        return self.remaining_requests == 0 or self.remaining_tokens == 0

    @property
    def is_low(self) -> bool:
        """True when <10% of either bucket remains — worth warning on."""
        def low(rem: Optional[int], lim: Optional[int]) -> bool:
            return rem is not None and lim not in (None, 0) and rem <= max(1, lim // 10)
        return low(self.remaining_requests, self.limit_requests) or \
            low(self.remaining_tokens, self.limit_tokens)


def _get(headers: Any, name: str) -> Optional[str]:
    try:
        value = headers.get(name) if headers is not None else None
    except Exception:
        return None
    if isinstance(value, bytes):
        return value.decode("latin-1")
    return str(value) if isinstance(value, (str, int, float)) and not isinstance(value, bool) else None


def parse_rate_limit(headers: Any) -> RateLimitInfo:
    """Build a :class:`RateLimitInfo` from response headers (dict / httpx).

    Handles both the OpenAI and Anthropic naming schemes; missing fields
    stay ``None``. ``headers=None`` yields an empty (``present == False``)
    snapshot.
    """
    info = RateLimitInfo()
    if headers is None:
        return info

    raw: dict[str, str] = {}

    # OpenAI scheme: x-ratelimit-{limit,remaining,reset}-{requests,tokens}
    pairs = [
        ("limit_requests", "x-ratelimit-limit-requests"),
        ("remaining_requests", "x-ratelimit-remaining-requests"),
        ("reset_requests", "x-ratelimit-reset-requests"),
        ("limit_tokens", "x-ratelimit-limit-tokens"),
        ("remaining_tokens", "x-ratelimit-remaining-tokens"),
        ("reset_tokens", "x-ratelimit-reset-tokens"),
    ]
    # Anthropic scheme: anthropic-ratelimit-{requests,tokens}-{limit,remaining,reset}
    pairs += [
        ("limit_requests", "anthropic-ratelimit-requests-limit"),
        ("remaining_requests", "anthropic-ratelimit-requests-remaining"),
        ("reset_requests", "anthropic-ratelimit-requests-reset"),
        ("limit_tokens", "anthropic-ratelimit-tokens-limit"),
        ("remaining_tokens", "anthropic-ratelimit-tokens-remaining"),
        ("reset_tokens", "anthropic-ratelimit-tokens-reset"),
        ("limit_input_tokens", "anthropic-ratelimit-input-tokens-limit"),
        ("remaining_input_tokens", "anthropic-ratelimit-input-tokens-remaining"),
        ("reset_input_tokens", "anthropic-ratelimit-input-tokens-reset"),
        ("limit_output_tokens", "anthropic-ratelimit-output-tokens-limit"),
        ("remaining_output_tokens", "anthropic-ratelimit-output-tokens-remaining"),
        ("reset_output_tokens", "anthropic-ratelimit-output-tokens-reset"),
    ]

    for attr, header in pairs:
        v = _get(headers, header)
        if v is None:
            continue
        raw[header] = v
        if attr.startswith("reset_"):
            # reset is a duration ("1s") or timestamp — keep as string.
            if getattr(info, attr) is None:
                setattr(info, attr, v)
        else:
            iv = _int(v)
            if iv is not None and getattr(info, attr) is None:
                setattr(info, attr, iv)

    info.raw = raw
    return info


__all__ = [
    "RateLimitInfo",
    "parse_rate_limit",
]

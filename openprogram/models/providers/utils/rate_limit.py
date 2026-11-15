"""Parse provider rate-limit response headers into structured objects.

Providers return limit telemetry on ordinary responses, under three
naming schemes:

  * OpenAI:    ``x-ratelimit-{limit,remaining,reset}-{requests,tokens}``
  * Anthropic: ``anthropic-ratelimit-{requests,tokens,input-tokens,
               output-tokens}-{limit,remaining,reset}`` (API-key accounts)
  * Codex (ChatGPT sign-in): ``x-codex-{primary,secondary}-{used-percent,
    window-minutes,reset-at}``, ``x-codex-credits-{has-credits,unlimited,
    balance}`` and the WebSocket frame ``codex.rate_limits``, read as
    codex-rs ``codex-api/src/rate_limits.rs`` reads them.

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


# Codex (ChatGPT sign-in)

@dataclass
class CodexWindow:
    """One Codex usage window: percent used, its length and when it resets."""

    used_percent: float
    window_minutes: Optional[int] = None
    resets_at: Optional[int] = None  # Unix seconds


@dataclass
class CodexCredits:
    has_credits: bool
    unlimited: bool
    balance: Optional[str] = None


@dataclass
class CodexRateLimits:
    """One Codex limit family (``codex`` by default, or a per-model one)."""

    limit_id: str = "codex"
    limit_name: Optional[str] = None
    primary: Optional[CodexWindow] = None
    secondary: Optional[CodexWindow] = None
    credits: Optional[CodexCredits] = None
    plan_type: Optional[str] = None

    @property
    def present(self) -> bool:
        return self.primary is not None or self.secondary is not None or self.credits is not None


def _float(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    text = str(v).strip().lower() if v is not None else ""
    if text in ("true", "1"):
        return True
    if text in ("false", "0"):
        return False
    return None


def _codex_window(headers: Any, prefix: str, which: str) -> Optional[CodexWindow]:
    used = _float(_get(headers, f"{prefix}-{which}-used-percent"))
    if used is None:
        return None
    minutes = _int(_get(headers, f"{prefix}-{which}-window-minutes"))
    resets = _int(_get(headers, f"{prefix}-{which}-reset-at"))
    # codex-rs drops an all-zero window: the header family is sent empty.
    if used == 0 and not minutes and resets is None:
        return None
    return CodexWindow(used_percent=used, window_minutes=minutes, resets_at=resets)


def _codex_family(headers: Any, limit_id: str) -> CodexRateLimits:
    prefix = "x-" + limit_id.strip().lower().replace("_", "-")
    name = (_get(headers, f"{prefix}-limit-name") or "").strip() or None
    credits = None
    if limit_id == "codex":
        has = _bool(_get(headers, "x-codex-credits-has-credits"))
        unlimited = _bool(_get(headers, "x-codex-credits-unlimited"))
        if has is not None and unlimited is not None:
            balance = (_get(headers, "x-codex-credits-balance") or "").strip() or None
            credits = CodexCredits(has_credits=has, unlimited=unlimited, balance=balance)
    return CodexRateLimits(
        limit_id=limit_id.strip().lower().replace("-", "_"),
        limit_name=name,
        primary=_codex_window(headers, prefix, "primary"),
        secondary=_codex_window(headers, prefix, "secondary"),
        credits=credits,
    )


def _header_names(headers: Any) -> list[str]:
    try:
        return [str(k).lower() for k in headers.keys()]
    except Exception:
        return []


def parse_codex_rate_limits(headers: Any) -> list[CodexRateLimits]:
    """Every Codex limit family the headers carry, ``codex`` first.

    A family is named by its ``x-<id>-primary-used-percent`` header, so a
    per-model limit (``x-codex-other-primary-used-percent``) comes back as
    its own entry. Families with no window and no credits are left out.
    """
    if headers is None:
        return []
    out = [_codex_family(headers, "codex")]
    others = sorted({
        name[len("x-"):-len("-primary-used-percent")]
        for name in _header_names(headers)
        if name.startswith("x-") and name.endswith("-primary-used-percent")
    } - {"codex"})
    out += [_codex_family(headers, other) for other in others]
    return [family for family in out if family.present]


def _frame_window(value: Any) -> Optional[CodexWindow]:
    if not isinstance(value, dict):
        return None
    used = _float(value.get("used_percent"))
    if used is None:
        return None
    return CodexWindow(
        used_percent=used,
        window_minutes=_int(value.get("window_minutes")),
        resets_at=_int(value.get("reset_at")),
    )


def parse_codex_rate_limit_frame(frame: Any) -> Optional[CodexRateLimits]:
    """The WebSocket ``codex.rate_limits`` frame, or ``None`` for any other frame."""
    if not isinstance(frame, dict) or frame.get("type") != "codex.rate_limits":
        return None
    limits = frame.get("rate_limits") if isinstance(frame.get("rate_limits"), dict) else {}
    credits = None
    raw_credits = frame.get("credits")
    if isinstance(raw_credits, dict):
        has = _bool(raw_credits.get("has_credits"))
        unlimited = _bool(raw_credits.get("unlimited"))
        if has is not None and unlimited is not None:
            balance = raw_credits.get("balance")
            credits = CodexCredits(
                has_credits=has, unlimited=unlimited,
                balance=str(balance).strip() if balance not in (None, "") else None,
            )
    limit_id = frame.get("metered_limit_name") or frame.get("limit_name") or "codex"
    plan = frame.get("plan_type")
    return CodexRateLimits(
        limit_id=str(limit_id).strip().lower().replace("-", "_"),
        primary=_frame_window(limits.get("primary")),
        secondary=_frame_window(limits.get("secondary")),
        credits=credits,
        plan_type=str(plan) if isinstance(plan, str) else None,
    )


__all__ = [
    "CodexCredits",
    "CodexRateLimits",
    "CodexWindow",
    "RateLimitInfo",
    "parse_codex_rate_limit_frame",
    "parse_codex_rate_limits",
    "parse_rate_limit",
]

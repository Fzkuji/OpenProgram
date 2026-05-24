"""Structured LLM error types for retry / recovery decisions.

Replaces the previous "raise RuntimeError(formatted_string)" pattern
that made callers regex-parse messages to decide whether to retry,
reauth, surface to user, or fall back. Now ``runtime.exec()``
(and provider streams) emit :class:`LLMError` carrying structured
fields:

  * ``reason`` — categorical (transport / rate_limit / auth / etc)
  * ``retryable`` — boolean already decided
  * ``http_status`` — when applicable
  * ``retry_after_s`` — server-supplied hint, honored by the backoff
  * ``attempts``, ``elapsed_s``, ``had_image`` — observability for
    upstream circuit-breakers / metrics
  * ``cause`` — original exception preserved for traceback

Design follows opencode's ``LLMError`` reason classification
(references/opencode/packages/llm/src/route/executor.ts) and
OpenClaw's onRetry observability (references/openclaw/src/infra/
retry.ts). The two share the same insight: providers fail in a
small number of *kinds*, and every kind has a specific recovery
strategy — RuntimeError + message regex loses that information.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Optional


class ErrorReason(str, enum.Enum):
    """Categorical reason for an LLM call failure.

    Distinguishes errors so the caller (dispatcher / GUI agent /
    web UI) can route to the right recovery path:

      * ``TRANSPORT`` — retry (with backoff)
      * ``RATE_LIMIT`` — retry honoring Retry-After
      * ``PROVIDER_INTERNAL`` — retry (transient upstream)
      * ``AUTHENTICATION`` — surface reauth flow
      * ``AUTHORIZATION`` — surface scope / plan error
      * ``INVALID_REQUEST`` — surface to user, don't retry
      * ``CONTEXT_LENGTH`` — trim prompt and retry at upper layer
      * ``CONTENT_POLICY`` — surface or rewrite, don't blindly retry
      * ``UNKNOWN`` — conservative: surface message, don't retry
    """
    TRANSPORT = "transport"
    RATE_LIMIT = "rate_limit"
    PROVIDER_INTERNAL = "provider"
    AUTHENTICATION = "auth"
    AUTHORIZATION = "authz"
    INVALID_REQUEST = "invalid"
    CONTEXT_LENGTH = "context"
    CONTENT_POLICY = "policy"
    UNKNOWN = "unknown"


@dataclass
class LLMError(Exception):
    """Structured exception raised when an LLM call fails terminally.

    "Terminally" means: either ``runtime.exec()`` exhausted its retry
    budget, or the underlying error was classified non-retryable
    (auth, invalid request, context overflow). Provider streams use
    the same type when they give up so upper layers see a single
    error shape regardless of which provider failed.

    Use ``raise LLMError(...) from original_exc`` to preserve the
    original traceback for debugging.
    """
    message: str
    reason: ErrorReason = ErrorReason.UNKNOWN
    retryable: bool = False
    http_status: Optional[int] = None
    retry_after_s: Optional[float] = None
    attempts: int = 1
    elapsed_s: float = 0.0
    had_image: bool = False
    provider: Optional[str] = None
    model: Optional[str] = None
    last_error_type: Optional[str] = None
    cause: Optional[BaseException] = None

    def __post_init__(self) -> None:
        # Initialise Exception with the human-readable message so
        # str(exc) without our __str__ override still reads cleanly.
        super().__init__(self.message)

    def __str__(self) -> str:
        parts = [self.message]
        tags: list[str] = []
        if self.reason != ErrorReason.UNKNOWN:
            tags.append(f"reason={self.reason.value}")
        if self.retryable:
            tags.append("retryable")
        if self.http_status:
            tags.append(f"status={self.http_status}")
        if self.attempts > 1:
            tags.append(f"attempts={self.attempts}")
        if self.elapsed_s:
            tags.append(f"elapsed={self.elapsed_s:.1f}s")
        if self.retry_after_s:
            tags.append(f"retry_after={self.retry_after_s:.1f}s")
        if self.had_image:
            tags.append("had_image")
        if tags:
            parts.append(f"[{', '.join(tags)}]")
        return " ".join(parts)


# Tokens that mark the exception text as a permanent error even when
# no HTTP status is available (CLI provider errors, ValueError from
# auth resolution, etc). Kept as substrings so we can match the
# noisy multi-line strings provider SDKs hand us.
_PERMANENT_MARKERS_AUTH = (
    "unauthorized",
    "invalid api key",
    "invalid_api_key",
    "login expired",
    "login failed",
    "re-auth",
    "no api key",
)
_PERMANENT_MARKERS_INVALID = (
    "not a valid image",
    "invalid image",
    "image data is not",
)
_PERMANENT_MARKERS_CONTEXT = (
    "context length",
    "context window",
    "maximum context",
    "context_length_exceeded",
)
_PERMANENT_MARKERS_POLICY = (
    "content policy",
    "content_policy",
    "safety system",
)
# Transport / transient keywords for the fallback path when we don't
# have a status code and the exception type is something opaque
# (CLI subprocess error string, third-party SDK wrapper, etc).
_TRANSIENT_KEYWORDS = (
    "rate limit",
    "overloaded",
    "service unavailable",
    "service_unavailable",
    "upstream connect",
    "connection refused",
    "connection reset",
    "connection aborted",
    "broken pipe",
    "temporarily",
    "timeout",
    "timed out",
)
# Exception class names that always mean "network layer hiccup". Kept
# as names (not isinstance) so we don't have to import httpx /
# requests at module load — providers that never use them won't pay
# the import cost.
_TRANSPORT_EXC_NAMES = frozenset({
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "RemoteProtocolError",
    "ProtocolError",
    "NetworkError",
    "StreamIdleTimeout",
    "StreamTotalTimeout",
})


def classify_error(
    exc: BaseException,
    *,
    http_status: Optional[int] = None,
    error_text: str = "",
) -> tuple[ErrorReason, bool]:
    """Best-effort classification of an arbitrary exception.

    Returns ``(reason, retryable)``. Logic order:

      1. HTTP status (most reliable) — 429 / 5xx retryable, 4xx not
      2. Exception type name — transport-layer types are retryable
      3. Message substring fallback — covers CLI providers / SDK
         wrappers that don't expose a status

    Callers may pass ``error_text`` when the body text isn't already
    in ``str(exc)`` (e.g. an httpx response body that's separate
    from the exception).
    """
    msg = (str(exc) + " " + (error_text or "")).lower()

    if http_status is not None:
        if http_status == 429:
            return ErrorReason.RATE_LIMIT, True
        if http_status in (500, 502, 503, 504):
            return ErrorReason.PROVIDER_INTERNAL, True
        if http_status == 401:
            return ErrorReason.AUTHENTICATION, False
        if http_status == 403:
            return ErrorReason.AUTHORIZATION, False
        if http_status == 400:
            if any(m in msg for m in _PERMANENT_MARKERS_CONTEXT):
                return ErrorReason.CONTEXT_LENGTH, False
            if any(m in msg for m in _PERMANENT_MARKERS_POLICY):
                return ErrorReason.CONTENT_POLICY, False
            return ErrorReason.INVALID_REQUEST, False
        # Other 4xx — assume client-side, non-retryable.
        if 400 <= http_status < 500:
            return ErrorReason.INVALID_REQUEST, False
        # Other 5xx beyond the well-known retry set — be conservative
        # and treat as retryable provider internal.
        if 500 <= http_status < 600:
            return ErrorReason.PROVIDER_INTERNAL, True

    exc_type = type(exc).__name__
    if exc_type in _TRANSPORT_EXC_NAMES:
        return ErrorReason.TRANSPORT, True

    if any(m in msg for m in _PERMANENT_MARKERS_AUTH):
        return ErrorReason.AUTHENTICATION, False
    if any(m in msg for m in _PERMANENT_MARKERS_INVALID):
        return ErrorReason.INVALID_REQUEST, False
    if any(m in msg for m in _PERMANENT_MARKERS_CONTEXT):
        return ErrorReason.CONTEXT_LENGTH, False
    if any(m in msg for m in _PERMANENT_MARKERS_POLICY):
        return ErrorReason.CONTENT_POLICY, False
    if "rate limit" in msg or "overloaded" in msg:
        return ErrorReason.RATE_LIMIT, True
    if any(k in msg for k in _TRANSIENT_KEYWORDS):
        return ErrorReason.TRANSPORT, True

    return ErrorReason.UNKNOWN, False


def parse_retry_after(headers: Any | None, error_text: str = "") -> Optional[float]:
    """Extract ``Retry-After`` seconds from response headers.

    Handles both numeric form (``Retry-After: 5``) and HTTP-date form
    (returns None for date form — we don't compute "until that time").

    ``headers`` accepts anything dict-like or httpx.Headers; ``None``
    is OK. ``error_text`` is reserved for future body-pattern parsing.
    """
    if headers is None:
        return None
    try:
        v = headers.get("Retry-After") or headers.get("retry-after")
    except AttributeError:
        return None
    if not v:
        return None
    try:
        s = float(v)
        return s if s > 0 else None
    except (TypeError, ValueError):
        # HTTP-date form — skip rather than parse; the exponential
        # backoff base is a reasonable fallback.
        return None


def had_image(content: Any) -> bool:
    """True if ``content`` (the exec() content list) contains an image block.

    Accepts the canonical block list (``[{"type": "image", ...}, ...]``).
    Image-bearing failures cost more to retry, so observability layers
    care about this signal even when the failure itself is generic.
    """
    if not isinstance(content, (list, tuple)):
        return False
    for b in content:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t in ("image", "image_url", "input_image"):
            return True
    return False


__all__ = [
    "ErrorReason",
    "LLMError",
    "classify_error",
    "parse_retry_after",
    "had_image",
]

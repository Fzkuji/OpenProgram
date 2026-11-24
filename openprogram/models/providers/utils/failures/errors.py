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
import html
import re
from dataclasses import dataclass
from typing import Any, Optional


class ExecInterrupt(BaseException):
    """Hard-stop signal that bypasses every ``except Exception`` retry layer.

    Both ``runtime.exec`` (and ``async_exec``) and
    ``stream_retry.retry_stream`` wrap their per-attempt body in
    ``except Exception`` so a transport hiccup becomes a retry. That is
    exactly what must NOT happen to a caller's hard stop — a sample-level
    watchdog, a turn cancel, a deadline kill. ``TimeoutError`` /
    ``RuntimeError`` subclass ``Exception`` and get swallowed; a
    ``BaseException`` does not.

    So a caller that needs to abort an in-flight ``exec()`` / stream
    *now* — regardless of how many retry layers are between it and the
    network — raises this (e.g. from a ``signal`` handler or an
    ``asyncio`` cancel shim). It propagates cleanly out of both nested
    loops because neither ``except Exception`` catches it, and the loops
    additionally re-raise it explicitly for self-documentation.

    Contract: only the OUTERMOST owner of a call (the dispatcher, the
    benchmark runner) should raise this; provider/transport code never
    does. Sits beside ``KeyboardInterrupt`` / ``SystemExit`` /
    ``asyncio.CancelledError`` in the "not an error, a control signal"
    category.
    """


class StreamAborted(Exception):
    """User-initiated cancel observed mid-stream.

    Raised by a provider's stream-reading loop when the caller's cancel
    signal (``SimpleStreamOptions.signal``) is set. Providers catch it in
    their error path (by re-checking the signal) and finalize the turn
    with ``stop_reason="aborted"`` — the anthropic provider's cancel
    semantics — instead of retrying or reporting an error. Unwinding the
    raise out of the ``async with client.stream(...)`` block is what
    actually closes the connection.
    """


class ErrorReason(str, enum.Enum):
    """Categorical reason for an LLM call failure.

    Distinguishes errors so the caller (dispatcher / GUI agent /
    web UI) can route to the right recovery path:

      * ``TRANSPORT`` — retry (with backoff)
      * ``RATE_LIMIT`` — retry honoring Retry-After
      * ``PROVIDER_INTERNAL`` — retry (transient upstream)
      * ``AUTHENTICATION`` — surface reauth flow
      * ``AUTHORIZATION`` — surface scope / plan error
      * ``NETWORK_BLOCK`` — a network edge in front of the provider refused
        the address the request came from; the user changes their route
      * ``INVALID_REQUEST`` — surface to user, don't retry
      * ``CONTEXT_LENGTH`` — trim prompt and retry at upper layer
      * ``CONTENT_POLICY`` — surface or rewrite, don't blindly retry
      * ``TIMEOUT`` — caller-supplied ``timeout_s`` deadline hit;
        retry budget irrelevant, decision is "give up this call"
      * ``UNKNOWN`` — conservative: surface message, don't retry
    """
    TRANSPORT = "transport"
    RATE_LIMIT = "rate_limit"
    PROVIDER_INTERNAL = "provider"
    AUTHENTICATION = "auth"
    AUTHORIZATION = "authz"
    NETWORK_BLOCK = "blocked"
    INVALID_REQUEST = "invalid"
    CONTEXT_LENGTH = "context"
    CONTENT_POLICY = "policy"
    TIMEOUT = "timeout"
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
    # Which limit or refusal the failure was (:func:`failure_cause`), carried
    # from the failed reply so it survives being re-raised.
    failure: Optional[dict] = None

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
# An account out of money. Providers send it as 402, as 429 (OpenAI's
# ``insufficient_quota``) and also as 401 or 403 (OpenCode Zen answers a
# zero balance with 401 ``CreditsError`` "Insufficient balance"), so the body
# decides before the status code: topping up is the fix, not signing in.
# Phrasings from the adapters' own bodies and the peers' billing tables
# (hermes-agent ``_BILLING_PATTERNS``, openclaw ``failover-matches`` billing).
BILLING_MARKERS = (
    "creditserror",
    "insufficient credit",
    "insufficient_credit",
    "insufficient balance",
    "insufficient_balance",
    "insufficient account balance",
    "insufficient funds",
    "insufficient_quota",
    "insufficient quota",
    "insufficient_user_quota",
    "credit balance is too low",
    "credits have been exhausted",
    "used all available credits",
    "out of credits",
    "top up your credits",
    "payment required",
    "payment_required",
    "billing hard limit",
    "billing_hard_limit_reached",
    "exceeded your current quota",
    "arrearage",
    "余额不足",
    "欠费",
)


def is_billing_exhausted(text: str) -> bool:
    """Whether an error body says the account has no credit or balance left."""
    low = (text or "").lower()
    return any(m in low for m in BILLING_MARKERS)


# A spent quota arrives as 429 like ordinary throttling, but resets hours
# or days out rather than seconds. Retrying it can only fail, so it is
# classified as a plan/authorization problem instead of a rate limit.
_PERMANENT_MARKERS_QUOTA = (
    "usage_limit_reached",
    "usage limit reached",
    "quota_exceeded",
    "quota exceeded",
    "用量上限",  # MiniMax Token Plan, sent as a 429 rate_limit_error (2056)
) + BILLING_MARKERS
# Provider-internal generation failures that arrive WITHOUT an HTTP
# status (e.g. xAI's mid-stream ``APIError: Internal error during token
# generation``). Same recovery as a 5xx: retryable provider_internal.
_TRANSIENT_MARKERS_INTERNAL = (
    "internal error",
    "internal_error",
    "internal server error",
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
    # httpx
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "RemoteProtocolError",
    "ProtocolError",
    "NetworkError",
    "ReadError",
    "WriteError",
    # openai / anthropic SDK wrappers around the httpx failures above
    "APIConnectionError",
    "APITimeoutError",
    # SSE-layer (openai-codex)
    "StreamIdleTimeout",
    "StreamTotalTimeout",
    # Python builtins
    "ConnectionError",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "ConnectionRefusedError",
    "TimeoutError",
    "BrokenPipeError",
    # asyncio low-level
    "IncompleteReadError",
})


_WEB_PAGE_START = re.compile(r"<!doctype\s+html|<html[\s>]", re.IGNORECASE)
_WEB_PAGE_HIDDEN = re.compile(r"<(style|script|head)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_WEB_PAGE_TAG = re.compile(r"<[^>]+>")
WEB_PAGE_TEXT_CHARS = 400


def web_page_text(body: str) -> Optional[str]:
    """The words of a response body that is a web page, or ``None`` when it is not one.

    A block or outage page is markup around one sentence; shown raw it
    hides that sentence behind its stylesheet.
    """
    if not body or not _WEB_PAGE_START.search(body[:2000]):
        return None
    start = _WEB_PAGE_START.search(body).start()
    words = html.unescape(_WEB_PAGE_TAG.sub(" ", _WEB_PAGE_HIDDEN.sub(" ", body[start:])))
    text = " ".join(words.split())
    return text if len(text) <= WEB_PAGE_TEXT_CHARS else text[:WEB_PAGE_TEXT_CHARS - 1] + "\u2026"


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
    if isinstance(exc, LLMError):
        return exc.reason, exc.retryable
    msg = (str(exc) + " " + (error_text or "")).lower()

    if http_status is not None:
        if http_status == 429:
            # A spent subscription quota also arrives as 429, but it
            # resets hours or days out — calling it RATE_LIMIT sends the
            # caller into a retry it can never win, and tells the user
            # "too many requests" when the real answer is "your plan is
            # out until <date>".
            if any(m in msg for m in _PERMANENT_MARKERS_QUOTA):
                return ErrorReason.AUTHORIZATION, False
            return ErrorReason.RATE_LIMIT, True
        if http_status in (401, 402, 403) and is_billing_exhausted(msg):
            # No credit left, whatever status the provider chose for it.
            return ErrorReason.AUTHORIZATION, False
        if http_status in (500, 502, 503, 504):
            return ErrorReason.PROVIDER_INTERNAL, True
        if http_status == 401:
            return ErrorReason.AUTHENTICATION, False
        if http_status == 403:
            # The provider's API answers in JSON. A web page instead comes
            # from the network edge in front of it, which refused the
            # address the request came from: nothing about the account.
            if getattr(exc, "web_page", False) or web_page_text(error_text or str(exc)) is not None:
                return ErrorReason.NETWORK_BLOCK, False
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
    # A sign-in whose refresh token is spent: only signing in again helps.
    if any(cls.__name__ == "AuthNeedsReauthError" for cls in type(exc).__mro__):
        return ErrorReason.AUTHENTICATION, False
    # SDKs re-raise network failures under their own types ("Connection
    # error.") with the httpx error as the cause.
    cause = exc.__cause__ or exc.__context__
    for _ in range(3):
        if cause is None:
            break
        if type(cause).__name__ in _TRANSPORT_EXC_NAMES:
            return ErrorReason.TRANSPORT, True
        cause = cause.__cause__ or cause.__context__

    if is_billing_exhausted(msg):
        return ErrorReason.AUTHORIZATION, False
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
    if any(m in msg for m in _TRANSIENT_MARKERS_INTERNAL):
        return ErrorReason.PROVIDER_INTERNAL, True
    if any(k in msg for k in _TRANSIENT_KEYWORDS):
        return ErrorReason.TRANSPORT, True
    # Wrapped transport errors: ProviderStreamError("RemoteProtocolError: …")
    # carries the original exception NAME in its message (not its type), so
    # the type check above misses it. Scan the message for a known transport
    # name so a wrapped mid-stream hiccup still reads as retryable transport.
    if any(t.lower() in msg for t in _TRANSPORT_EXC_NAMES):
        return ErrorReason.TRANSPORT, True

    return ErrorReason.UNKNOWN, False


def taxonomy_fields(exc: BaseException) -> tuple[str | None, bool | None, float | None]:
    """``(reason_value, retryable, retry_after_s)`` for an exception — exactly
    what ``AssistantMessage.error_reason / error_retryable / error_retry_after_s``
    carry. An :class:`LLMError` already holds them; anything else is classified.
    See docs/design/providers/reliability/error-taxonomy-propagation.md.
    """
    if isinstance(exc, LLMError):
        return exc.reason.value, exc.retryable, exc.retry_after_s
    if type(exc).__name__ == "MemoryPolicyError":
        # Required memory that failed: no retry helps; ``error_kind`` names
        # it ``memory_unavailable``.
        return ErrorReason.UNKNOWN.value, False, None
    reason, retryable = classify_error(
        exc,
        http_status=getattr(exc, "http_status", None) or getattr(exc, "status_code", None),
        error_text=getattr(exc, "error_text", "") or "",
    )
    declared_retryable = getattr(exc, "retryable", None)
    if isinstance(declared_retryable, bool):
        retryable = declared_retryable
    return reason.value, retryable, getattr(exc, "retry_after_s", None)


#: Exception classes whose failure has a stable name of its own, beside the
#: broader ``ErrorReason`` (a stalled stream and a reset connection are both
#: ``transport``; a benchmark counts only the first as the environment's fault).
_KINDS = {
    "StreamIdleTimeout": "stream_idle",
    "StreamTotalTimeout": "stream_total",
    "MemoryPolicyError": "memory_unavailable",
    "ModelUnavailableError": "model_unavailable",
}


def error_kind(exc: BaseException) -> str | None:
    """The stable category of a failure, or ``None`` when it has none.

    Found on the exception or anything it wraps (``LLMError.cause``,
    ``__cause__``, ``__context__``), so a stalled stream is ``stream_idle``
    whichever reader raised it and whatever retry layer gave up on it. A
    failure with no such class but an authentication reason is ``auth``.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        kind = _KINDS.get(type(current).__name__)
        if kind is None and isinstance(current, LLMError):
            kind = _KINDS.get(current.last_error_type or "")
        if kind is not None:
            return kind
        current = (getattr(current, "cause", None) if isinstance(current, LLMError) else None) \
            or current.__cause__ or current.__context__
    reason, _retryable, _after = taxonomy_fields(exc)
    return "auth" if reason == ErrorReason.AUTHENTICATION.value else None


# What stopped a reply, in the terms a person acts on. Beside the reason
# (which says how to retry), the cause says which limit or refusal it was:
# a spent quota (and when it resets), a rejected key, a region or plan that
# is not served, or a host that could not be reached.
CAUSE_QUOTA = "quota_exhausted"
CAUSE_INVALID_KEY = "invalid_key"
CAUSE_NOT_AVAILABLE = "not_available"
CAUSE_UNREACHABLE = "unreachable"

_INVALID_KEY_MARKERS = (
    "invalid api key",
    "invalid_api_key",
    "incorrect api key",
    "invalid x-api-key",
    "api key not valid",
    "invalid authentication",
    "authentication_error",
)
_NOT_AVAILABLE_MARKERS = (
    "unsupported_country",
    "country, region, or territory",
    "not available in your region",
    "not available in your country",
    "region is not supported",
    "not supported in your region",
    "does not have access to model",
    "not available on your plan",
    "not included in your plan",
    "upgrade your plan",
    "permission_denied",
    "permission_error",
)
_UNREACHABLE_EXC_NAMES = frozenset({
    "ConnectError", "ConnectTimeout", "APIConnectionError", "ConnectionRefusedError",
    "gaierror", "SocketUnavailable",
})
_UNREACHABLE_MARKERS = (
    "connection refused",
    "name or service not known",
    "nodename nor servname",
    "getaddrinfo failed",
    "temporary failure in name resolution",
    "network is unreachable",
    "no route to host",
    "could not resolve host",
    "connecterror",
    "connecttimeout",
    "connection error",
)


def _chain(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and all(current is not s for s in seen) and len(seen) < 6:
        seen.append(current)
        current = (getattr(current, "cause", None) if isinstance(current, LLMError) else None) \
            or current.__cause__ or current.__context__
    return seen


def _body_reset(text: str, now: float) -> tuple[Optional[float], Optional[str]]:
    """``(resets_at, plan)`` from a spent-quota body (Codex: ``resets_at``,
    ``resets_in_seconds``, ``plan_type``), when it is JSON that says so."""
    import json
    start = text.find("{")
    if start < 0:
        return None, None
    try:
        body, _end = json.JSONDecoder().raw_decode(text[start:])
    except (TypeError, ValueError):
        return None, None
    error = body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else body
    if not isinstance(error, dict):
        return None, None
    reset = error.get("resets_at")
    if not isinstance(reset, (int, float)) or isinstance(reset, bool):
        seconds = error.get("resets_in_seconds")
        reset = now + seconds if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else None
    plan = error.get("plan_type")
    return (float(reset) if reset else None), (plan if isinstance(plan, str) else None)


def failure_cause(exc: BaseException, *, now: Optional[float] = None) -> Optional[dict]:
    """Which limit or refusal stopped a call, or ``None`` when it was none of them.

    Returns ``{"cause", "http_status", "provider", "limit", "resets_at",
    "account", "other_limits"}``: ``cause`` is one of :data:`CAUSE_QUOTA`,
    :data:`CAUSE_INVALID_KEY`, :data:`CAUSE_NOT_AVAILABLE`,
    :data:`CAUSE_UNREACHABLE`. For a spent quota ``limit`` names the window
    when the provider said which (from the last quota it reported),
    ``resets_at`` is Unix seconds when known, ``account`` is the credential
    whose limit is spent (null for an environment key or when unknown) and
    ``other_limits`` lists that key's other limits as
    ``{"label", "left_fraction", "resets_at"}`` ("Weekly 71% left").
    """
    import time as _time
    if isinstance(exc, LLMError) and isinstance(exc.failure, dict):
        return dict(exc.failure)
    now = _time.time() if now is None else now
    chain = _chain(exc)
    status = next((s for e in chain for s in (getattr(e, "http_status", None), getattr(e, "status_code", None))
                   if isinstance(s, int)), None)
    text = " ".join(str(e) + " " + str(getattr(e, "error_text", "") or "") for e in chain)
    lowered = text.lower()
    provider = next((getattr(e, "provider", None) for e in chain if getattr(e, "provider", None)), None)

    def result(cause: str, **extra: Any) -> dict:
        return {"cause": cause, "http_status": status, "provider": provider,
                "limit": extra.get("limit"), "resets_at": extra.get("resets_at"),
                "account": extra.get("account"), "other_limits": extra.get("other_limits") or []}

    if status == 402 or any(m in lowered for m in _PERMANENT_MARKERS_QUOTA) \
            or (status == 429 and "insufficient" in lowered):
        resets_at, _plan = _body_reset(text, now)
        retry_after = next((getattr(e, "retry_after_s", None) for e in chain
                            if isinstance(getattr(e, "retry_after_s", None), (int, float))), None)
        if resets_at is None and retry_after:
            resets_at = now + float(retry_after)
        limit = account = None
        others: list[dict] = []
        if provider:
            try:
                from openprogram.models.usage.quota import exhausted_report
                spent = exhausted_report(provider)
            except Exception:
                spent = None
            if spent is not None:
                account, limit_record, other_records = spent
                account = account or None
                limit = limit_record.label
                resets_at = resets_at or limit_record.resets_at
                others = [{"label": o.label, "left_fraction": None if o.used_fraction is None
                           else round(1 - o.used_fraction, 4), "resets_at": o.resets_at}
                          for o in other_records if o.used_fraction is not None]
        return result(CAUSE_QUOTA, limit=limit, resets_at=resets_at, account=account, other_limits=others)
    if status == 401 or any(m in lowered for m in _INVALID_KEY_MARKERS):
        return result(CAUSE_INVALID_KEY)
    if status == 403:
        if any(getattr(e, "web_page", False) for e in chain):
            return None  # the network edge refused the address: ``blocked``
        return result(CAUSE_NOT_AVAILABLE)
    if any(m in lowered for m in _NOT_AVAILABLE_MARKERS):
        return result(CAUSE_NOT_AVAILABLE)
    if status is None and (
        any(type(e).__name__ in _UNREACHABLE_EXC_NAMES for e in chain)
        or any(m in lowered for m in _UNREACHABLE_MARKERS)
    ):
        return result(CAUSE_UNREACHABLE)
    return None


def _retry_after_date_seconds(value: str) -> Optional[float]:
    """Seconds until an HTTP-date ``Retry-After`` (RFC 7231), or None.

    e.g. ``Retry-After: Wed, 21 Oct 2025 07:28:00 GMT`` → seconds from now.
    Past dates / unparseable values return None.
    """
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        dt = parsedate_to_datetime(value)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return delta if delta > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def parse_retry_after(headers: Any | None, error_text: str = "") -> Optional[float]:
    """Extract ``Retry-After`` seconds from response headers.

    Handles all three forms providers use (matching OpenClaw / OpenCode):

      * ``retry-after-ms: 1500`` — millisecond form (preferred when present)
      * ``Retry-After: 5`` — integer seconds
      * ``Retry-After: <HTTP-date>`` — absolute time → seconds-from-now

    ``headers`` accepts anything dict-like or httpx.Headers; ``None`` is OK.
    ``error_text`` is reserved for future body-pattern parsing.
    """
    if headers is None:
        return None
    try:
        ms = headers.get("retry-after-ms") or headers.get("Retry-After-Ms")
        v = headers.get("Retry-After") or headers.get("retry-after")
    except AttributeError:
        return None
    # Millisecond form first — most precise.
    if ms:
        try:
            s = float(ms) / 1000.0
            if s > 0:
                return s
        except (TypeError, ValueError):
            pass
    if not v:
        return None
    # Integer-seconds form.
    try:
        s = float(v)
        return s if s > 0 else None
    except (TypeError, ValueError):
        pass
    # HTTP-date form.
    return _retry_after_date_seconds(v)


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


@dataclass
class RetryInfo:
    """Per-attempt context passed to ``on_retry`` callbacks.

    Fired by ``runtime.exec()`` / ``async_exec()`` immediately before
    each backoff sleep — i.e. when an attempt has failed transiently
    and the next attempt is scheduled. Not fired for permanent
    failures (those raise immediately) or for the terminal failure
    that exhausts the budget (those raise via :class:`LLMError`).

    Mirrors OpenClaw's ``RetryInfo`` (references/openclaw/src/infra/
    retry.ts) — same fields, same purpose: let consumers record
    metrics, drive circuit breakers, or emit structured logs without
    coupling to provider internals.

    Fields:
      * ``attempt`` — 1-based index of the attempt that **just
        failed** (so the very first failure reports ``attempt=1``).
      * ``max_attempts`` — total budget, including the upcoming retry.
      * ``reason`` — classified ``ErrorReason`` of this failure.
      * ``sleep_s`` — backoff delay about to be slept (after
        Retry-After clamp, jitter, etc.). May be 0 if the deadline
        is about to fire.
      * ``elapsed_s`` — wall-clock elapsed since exec() started.
      * ``retry_after_s`` — server hint that influenced the sleep,
        when present.
      * ``last_error_type`` — Python exception class name.
      * ``last_error_msg`` — ``str(exc)`` for log readability.
    """
    attempt: int
    max_attempts: int
    reason: ErrorReason
    sleep_s: float
    elapsed_s: float
    retry_after_s: Optional[float] = None
    last_error_type: Optional[str] = None
    last_error_msg: Optional[str] = None


__all__ = [
    "BILLING_MARKERS",
    "CAUSE_INVALID_KEY",
    "CAUSE_NOT_AVAILABLE",
    "CAUSE_QUOTA",
    "CAUSE_UNREACHABLE",
    "ErrorReason",
    "ExecInterrupt",
    "LLMError",
    "RetryInfo",
    "StreamAborted",
    "classify_error",
    "error_kind",
    "failure_cause",
    "is_billing_exhausted",
    "parse_retry_after",
    "had_image",
]

"""Stream-level retry orchestration shared by every HTTP-based provider.

Each provider's ``_run()`` performs roughly the same dance: open an
``httpx`` stream, read SSE events, push them to ``EventStream`` until
``[DONE]``. Until the consumer has seen real data, a transport hiccup
(connection reset, 502, idle timeout) is recoverable — we can throw
away the half-open stream and re-send the identical request. The
moment any ``content`` block has been pushed to the ``EventStream``,
recovery requires either Last-Event-ID resumption (which neither the
OpenAI nor Anthropic Responses API expose) or a "discard partial,
resend" decision that crosses provider semantics.

This module factors the retry logic out so every provider gets:

  * ``retry_stream(attempt_fn, is_committed_fn, ...)`` — the
    orchestrator. Calls ``attempt_fn`` up to ``max_attempts`` times,
    classifies failures, honors ``Retry-After`` as a lower bound,
    and re-raises the last exception (which the provider should wrap
    via ``ev_stream.fail()`` so ``runtime.exec()`` catches via
    :func:`_build_llm_error` → :class:`LLMError`).
  * ``ProviderStreamError`` — exception type carrying the metadata
    ``runtime.exec()`` needs to classify (http_status, retry_after_s,
    retryable, error_text, provider).
  * ``is_retryable_status`` / ``stream_backoff_seconds`` /
    ``read_retry_after`` — building blocks providers can also call
    directly when they want custom retry shapes.

Defaults:

  * Max attempts: ``OPENPROGRAM_PROVIDER_STREAM_RETRIES`` env, default 3
  * Backoff base: 1.0s, doubled per attempt, ±25% jitter
  * Retry-After honored as lower bound with positive-only jitter
    (OpenClaw pattern: see ``references/openclaw/src/infra/retry.ts``)

The 5-min SSE idle timeout and 30-min total ceiling are provider-side
constants (each provider sets its own via ``OPENPROGRAM_SSE_IDLE_TIMEOUT_S``
/ ``OPENPROGRAM_SSE_TOTAL_TIMEOUT_S``); this module classifies them
when they bubble up but doesn't enforce them.
"""
from __future__ import annotations

import asyncio
import os
import random
import re
from typing import Any, Awaitable, Callable, Optional


# Env-tunable so heavy-reasoning deployments can extend the per-stream
# retry budget without code changes.
PROVIDER_STREAM_MAX_ATTEMPTS: int = int(
    os.environ.get("OPENPROGRAM_PROVIDER_STREAM_RETRIES", "3"),
)
_BACKOFF_BASE_S: float = float(
    os.environ.get("OPENPROGRAM_PROVIDER_STREAM_BACKOFF_S", "1.0"),
)


# Exception class names we treat as "transport hiccup": when the
# provider hasn't streamed real content yet, they're retryable.
# Kept as names (not isinstance) so we don't import httpx at module
# load — providers that never use httpx (CLI runtimes) shouldn't
# pay for it. Includes both httpx exception names and the Python
# builtin ``ConnectionError`` family.
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
    # SSE-layer
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
# CancelledError deliberately NOT included: it means upstream
# cancellation, retrying would defeat the cancel intent.


_RETRYABLE_BODY_PATTERN = re.compile(
    r"rate.?limit|overloaded|service.?unavailable|"
    r"upstream.?connect|connection.?refused|connection.?reset|"
    r"temporarily.?unavailable",
    re.IGNORECASE,
)


class ProviderStreamError(Exception):
    """Stream-layer failure carrying metadata for upstream classification.

    Providers raise this when their stream-level retry budget is
    exhausted or a non-retryable failure is observed. ``runtime.exec()``
    inspects ``http_status`` / ``retry_after_s`` / ``error_text`` via
    :func:`openprogram.providers.utils.errors.classify_error` and
    wraps the final failure in a structured :class:`LLMError`.

    Attributes:
      ``http_status``: HTTP status of the failed response, when known.
      ``retry_after_s``: Server-supplied ``Retry-After`` seconds.
        ``runtime._retry_sleep_seconds`` honors this as a lower bound.
      ``error_text``: Raw response body text (truncated by callers).
      ``retryable``: Whether the underlying failure kind was transient.
        Set ``False`` when the consumer has already received content
        (resuming would duplicate or drop events).
      ``provider``: Provider id, e.g. ``"anthropic"``, for logs.
    """

    def __init__(
        self,
        message: str,
        *,
        http_status: Optional[int] = None,
        retry_after_s: Optional[float] = None,
        error_text: str = "",
        retryable: bool = False,
        provider: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.retry_after_s = retry_after_s
        self.error_text = error_text
        self.retryable = retryable
        self.provider = provider


def is_retryable_status(status: int, error_text: str = "") -> bool:
    """True for HTTP status codes and message patterns we should retry.

    Retryable statuses: 429 (rate limit), 500/502/503/504 (provider
    internal). Retryable body patterns: rate-limit / overload /
    upstream-connect / connection-reset language that providers
    sometimes return with a 4xx but mean transient.

    Used by every HTTP provider when they receive a non-2xx response
    inside an attempt — pass the status and decoded body, get a
    boolean to feed ``ProviderStreamError(retryable=...)``.
    """
    if status in (429, 500, 502, 503, 504):
        return True
    if error_text and _RETRYABLE_BODY_PATTERN.search(error_text):
        return True
    return False


def stream_backoff_seconds(
    attempt: int, retry_after_s: Optional[float] = None,
) -> float:
    """Exponential backoff with Retry-After lower bound.

    Without a hint: ``base * 2^attempt`` × symmetric jitter ``[0.75, 1.25]``.
    With a hint: ``max(base, retry_after_s)`` × positive-only jitter
    ``[1.0, 1.25]`` — never sleep less than the server told us. Mirrors
    OpenClaw's ``computeBackoffDelay`` (references/openclaw/src/infra/
    retry.ts) which uses the same rule because ±25% symmetric jitter
    would let a quarter of retries fire before the server-specified
    deadline, defeating the backpressure.
    """
    base = _BACKOFF_BASE_S * (2 ** attempt)
    if retry_after_s and retry_after_s > 0:
        floor = max(base, retry_after_s)
        return floor * random.uniform(1.0, 1.25)
    return base * random.uniform(0.75, 1.25)


def read_retry_after(headers: Any) -> Optional[float]:
    """Extract numeric ``Retry-After`` from httpx-style headers.

    Honors only the seconds form (e.g. ``Retry-After: 5``); HTTP-date
    form returns ``None`` (we fall back to the exponential base).
    ``headers=None`` is OK.
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
        return None


async def retry_stream(
    attempt_fn: Callable[[], Awaitable[None]],
    *,
    is_committed_fn: Callable[[], bool],
    max_attempts: int = PROVIDER_STREAM_MAX_ATTEMPTS,
    label: str = "stream",
    provider: Optional[str] = None,
) -> None:
    """Run ``attempt_fn`` repeatedly until success or non-retryable failure.

    Args:
        attempt_fn: ``async () -> None`` — body of one stream attempt
            (open httpx stream, consume SSE, push events). Raises
            on failure. Must be safe to call multiple times: callers
            should not mutate any "committed" state inside it until
            real content actually arrives.
        is_committed_fn: ``() -> bool`` — returns True if real content
            has already been pushed to the consumer (i.e. retry would
            either duplicate events or drop them). Typically
            ``lambda: bool(output.content)``.
        max_attempts: Total attempts including the first try. Default
            from ``OPENPROGRAM_PROVIDER_STREAM_RETRIES`` env (3).
        label: Used in retry log line, e.g. ``"openai-codex"``.
        provider: Stored on the final exception for downstream logs.

    Re-raises the last failure (a :class:`ProviderStreamError`) when
    the budget is exhausted or the failure was non-retryable. The
    caller is expected to wrap this via ``ev_stream.fail(exc)`` so
    ``runtime.exec()`` sees it.
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(max_attempts):
        try:
            await attempt_fn()
            return
        except ProviderStreamError as exc:
            last_exc = exc
            if exc.provider is None and provider is not None:
                exc.provider = provider
        except Exception as exc:
            # Wrap arbitrary transport / SSE errors so the upstream
            # layer always sees the same exception shape with the
            # retryable / retry_after metadata it needs.
            exc_name = type(exc).__name__
            transport_like = exc_name in _TRANSPORT_EXC_NAMES
            last_exc = ProviderStreamError(
                f"{exc_name}: {exc}",
                retryable=transport_like and not is_committed_fn(),
                provider=provider,
            )

        retryable = bool(getattr(last_exc, "retryable", False))
        last_attempt = attempt >= max_attempts - 1
        if not retryable or last_attempt:
            assert last_exc is not None
            raise last_exc

        retry_after = getattr(last_exc, "retry_after_s", None)
        sleep_s = stream_backoff_seconds(attempt, retry_after)
        print(
            f"[{label} stream retry] attempt {attempt + 1}/{max_attempts - 1} "
            f"after {sleep_s:.1f}s — {last_exc}",
            flush=True,
        )
        await asyncio.sleep(sleep_s)


__all__ = [
    "PROVIDER_STREAM_MAX_ATTEMPTS",
    "ProviderStreamError",
    "is_retryable_status",
    "read_retry_after",
    "retry_stream",
    "stream_backoff_seconds",
]

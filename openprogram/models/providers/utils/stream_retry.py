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

The stream idle bound (``stream_idle.py``, 300 s without an event) and the
optional total budget are enforced by the transports; this module classifies
the ``StreamIdleTimeout`` / ``StreamTotalTimeout`` they raise as transport
failures and retries them, but doesn't enforce them.
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import time
from typing import Any, Awaitable, Callable, Optional

from openprogram.models.providers.utils.errors import (
    ExecInterrupt,
    # One shared list, so the retry layer and the reason classifier can
    # never disagree about what a spent quota looks like.
    _PERMANENT_MARKERS_QUOTA as _QUOTA_MARKERS,
)
from openprogram.models.providers.utils.deadline import expired as _dl_expired, remaining as _dl_remaining
import logging

_log = logging.getLogger(__name__)


# Env-tunable so heavy-reasoning deployments can extend the per-stream
# retry budget without code changes.
# Default 5 (was 3): backends like the codex stream have time-windowed
# flakiness (intermittent empty {"type":"error"} events) where the SAME
# request succeeds moments later. A few extra attempts ride over most of
# those windows. Env-overridable for tuning.
PROVIDER_STREAM_MAX_ATTEMPTS: int = int(
    os.environ.get("OPENPROGRAM_PROVIDER_STREAM_RETRIES", "5"),
)
# Base 2.0s (was 1.0): with exponential backoff this spaces the retries
# (2/4/8/16s, capped) so they probe the backend across a wider time span
# rather than hammering inside one bad window.
_BACKOFF_BASE_S: float = float(
    os.environ.get("OPENPROGRAM_PROVIDER_STREAM_BACKOFF_S", "2.0"),
)
# Cap on the *exponential* component so a large attempt budget can't
# produce a multi-minute sleep. Mirrors OpenClaw (maxDelayMs 30s) and
# OpenCode (MAX_DELAY_MS 10s). A server-supplied Retry-After larger than
# this is still honored (we never sleep *less* than the server asked).
_BACKOFF_MAX_S: float = float(
    os.environ.get("OPENPROGRAM_PROVIDER_STREAM_BACKOFF_MAX_S", "30.0"),
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

# A 429 means two very different things. Per-minute throttling clears in
# seconds and is worth retrying; a spent subscription quota does not —
# codex returns ``usage_limit_reached`` with ``resets_in_seconds`` in the
# hundreds of thousands, so the backoff ladder just turns an instant
# failure into a 40-second one and reports the wrong cause. These bodies
# are terminal: fail immediately and let the caller say why.
def _is_quota_exhausted(error_text: str) -> bool:
    lowered = error_text.lower()
    return any(m in lowered for m in _QUOTA_MARKERS)


class ProviderStreamError(Exception):
    """Stream-layer failure carrying metadata for upstream classification.

    Providers raise this when their stream-level retry budget is
    exhausted or a non-retryable failure is observed. ``runtime.exec()``
    inspects ``http_status`` / ``retry_after_s`` / ``error_text`` via
    :func:`openprogram.models.providers.utils.errors.classify_error` and
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
        # A web page as the body (a block or outage page from the network
        # edge) is shown by its words; ``error_text`` keeps the body as it came.
        from openprogram.models.providers.utils.errors import web_page_text
        page = web_page_text(error_text)
        if page is not None and error_text in message:
            message = message.replace(error_text, page)
        super().__init__(message)
        self.web_page = page is not None
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

    A spent quota is the exception: it arrives as 429 but resets hours or
    days out, so it is terminal no matter what the status says. Checked
    first — the body wins over the status code.

    Used by every HTTP provider when they receive a non-2xx response
    inside an attempt — pass the status and decoded body, get a
    boolean to feed ``ProviderStreamError(retryable=...)``.
    """
    if error_text and _is_quota_exhausted(error_text):
        return False
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
    # Exponential component, capped (a server Retry-After larger than the
    # cap is still honored below — we never sleep less than asked).
    base = min(_BACKOFF_BASE_S * (2 ** attempt), _BACKOFF_MAX_S)
    if retry_after_s and retry_after_s > 0:
        floor = max(base, retry_after_s)
        return floor * random.uniform(1.0, 1.25)
    return base * random.uniform(0.75, 1.25)


RETRY_AFTER_CAP_S: float = float(
    os.environ.get("OPENPROGRAM_RETRY_AFTER_CAP_S", "300") or 300
)
"""The longest server-asked wait a retry sits through in-process."""


def retry_after_beyond_cap(retry_after_s: Optional[float]) -> bool:
    """A ``Retry-After`` longer than :data:`RETRY_AFTER_CAP_S`: give up now, don't wait.

    A plan's quota or an account's rest can ask for a wait of hours. Sat
    through in-process, the turn hangs that long before any fallback,
    credential switch or the user gets a say; past the cap the failure goes
    up at once instead, still carrying the server's hint. A shorter hint is
    honored as before: the cap is never a floor. After magpie's Retry-After
    cap (yetone/magpie, MIT).
    """
    return bool(retry_after_s and RETRY_AFTER_CAP_S > 0 and retry_after_s > RETRY_AFTER_CAP_S)


def read_retry_after(headers: Any) -> Optional[float]:
    """Extract ``Retry-After`` seconds from httpx-style headers.

    Delegates to :func:`openprogram.models.providers.utils.errors.parse_retry_after`,
    which handles the ``retry-after-ms`` (millisecond), integer-seconds, and
    HTTP-date forms. ``headers=None`` is OK.
    """
    from openprogram.models.providers.utils.errors import parse_retry_after
    return parse_retry_after(headers)



async def _sleep_unless_aborted(sleep_s: float, abort_check: Callable[[], bool] | None) -> None:
    """Sleep in short slices so cancel mid-backoff does not start another attempt."""
    if sleep_s <= 0:
        return
    end = asyncio.get_running_loop().time() + sleep_s
    while True:
        if abort_check is not None and abort_check():
            raise ExecInterrupt("cancelled")
        from .recovery import current_recovery
        state = current_recovery.get()
        if state is not None and state.phase == "cancelled":
            raise ExecInterrupt("cancelled")
        remaining = end - asyncio.get_running_loop().time()
        if remaining <= 0:
            return
        await asyncio.sleep(min(0.05, remaining))


# Connection failures that mean the endpoint is unreachable from here (no
# network, DNS down, local proxy stopped), as opposed to a server that
# answered badly. Only these wait for the network to come back.
_UNREACHABLE_EXC_NAMES = frozenset({
    "ConnectError",
    "ConnectTimeout",
    "ConnectionRefusedError",
    "gaierror",
})
_UNREACHABLE_TEXT = re.compile(
    r"^(?:ConnectError|ConnectTimeout|ConnectionRefusedError)\b|"
    r"network is unreachable|no route to host|name or service not known|"
    r"nodename nor servname|temporary failure in name resolution",
    re.IGNORECASE,
)
# How long one request waits for the network after its transport retries
# are spent on unreachable failures. 0 disables the wait.
NETWORK_WAIT_MAX_S: float = float(
    os.environ.get("OPENPROGRAM_NETWORK_WAIT_MAX_S", "1800"),
)
_NETWORK_PROBE_MIN_S = 5.0
_NETWORK_PROBE_MAX_S = 30.0


def is_network_unreachable(exc: BaseException | None) -> bool:
    """True when ``exc`` says the endpoint could not be reached at all."""
    seen = 0
    while exc is not None and seen < 4:
        if getattr(exc, "network_unreachable", False):
            return True
        if type(exc).__name__ in _UNREACHABLE_EXC_NAMES:
            return True
        if _UNREACHABLE_TEXT.search(str(exc) or ""):
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


def _now() -> float:
    return time.monotonic()


def network_wait_started() -> float:
    """Clock reading that starts a network wait (see ``network_wait_delay``)."""
    return _now()


def network_wait_delay(started: float, probe: int) -> float | None:
    """Seconds before the next probe of an unreachable endpoint.

    ``started`` is the ``network_wait_started()`` reading at which the wait began. Returns
    ``None`` once the wait would pass ``OPENPROGRAM_NETWORK_WAIT_MAX_S`` or
    the caller's deadline.
    """
    sleep_s = min(_NETWORK_PROBE_MIN_S * (2 ** min(probe, 3)), _NETWORK_PROBE_MAX_S)
    waited = _now() - started
    rem = _dl_remaining()
    if NETWORK_WAIT_MAX_S <= 0 or waited + sleep_s > NETWORK_WAIT_MAX_S:
        return None
    if rem is not None and rem <= sleep_s:
        return None
    return sleep_s


def network_wait_allowed(max_attempts: int) -> bool:
    """Whether a spent, unreachable request may wait for the network.

    Explicitly disabled recovery (a single-attempt request policy, or a
    recovery limit of 0) never waits.
    """
    if NETWORK_WAIT_MAX_S <= 0 or max_attempts <= 1:
        return False
    from .recovery import current_recovery
    state = current_recovery.get()
    return state is None or (state.limit > 0 and state.phase != "cancelled")


RetryNotice = Callable[[dict[str, Any]], None]


RETRY_ERROR_MAX_CHARS = 300


def retry_error_text(exc: BaseException | None) -> str | None:
    """The failure a retry answers, as the chat's retry row shows it."""
    if exc is None:
        return None
    message = " ".join(str(exc).split())
    name = type(exc).__name__
    text = f"{name}: {message}" if message and not message.startswith(name) else (message or name)
    return text if len(text) <= RETRY_ERROR_MAX_CHARS else text[:RETRY_ERROR_MAX_CHARS - 1] + "…"


def _notify(on_retry: RetryNotice | None, **event: Any) -> None:
    if on_retry is None:
        return
    try:
        on_retry({"type": "retry", **event})
    except Exception:
        # Caller-supplied notice callback must not break the retry loop.
        _log.debug("on_retry callback failed", exc_info=True)


def _wrap_failure(exc: BaseException, provider: Optional[str], committed: bool) -> ProviderStreamError:
    # Wrap arbitrary transport / SSE errors so the upstream layer always
    # sees the same exception shape with the retryable / retry_after
    # metadata it needs.
    exc_name = type(exc).__name__
    transport_like = exc_name in _TRANSPORT_EXC_NAMES
    wrapped = ProviderStreamError(
        f"{exc_name}: {exc}",
        retryable=transport_like and not committed,
        provider=provider,
    )
    wrapped.network_unreachable = (  # type: ignore[attr-defined]
        transport_like and is_network_unreachable(exc)
    )
    wrapped.__cause__ = exc
    return wrapped


async def retry_stream(
    attempt_fn: Callable[[], Awaitable[None]],
    *,
    is_committed_fn: Callable[[], bool],
    max_attempts: int = PROVIDER_STREAM_MAX_ATTEMPTS,
    label: str = "stream",
    provider: Optional[str] = None,
    abort_check: Callable[[], bool] | None = None,
    on_retry: RetryNotice | None = None,
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
            from ``OPENPROGRAM_PROVIDER_STREAM_RETRIES`` env (5).
        label: Used in retry log line, e.g. ``"openai-codex"``.
        provider: Stored on the final exception for downstream logs.
        on_retry: Receives a ``{"type": "retry", ...}`` event before each
            retry and while waiting for the network, for the live UI.

    When the transport retries are spent on failures that cannot reach the
    endpoint at all (no network, DNS, a stopped local proxy) and nothing
    was committed, the request waits for the network instead of failing:
    it re-tries every 5–30 s, for up to ``OPENPROGRAM_NETWORK_WAIT_MAX_S``
    (default 30 min) and never past the caller's deadline or a cancel. A
    success returns normally; a different failure once the network is back
    gets a fresh retry budget.

    Re-raises the last failure (a :class:`ProviderStreamError`) when
    the budget is exhausted or the failure was non-retryable. The
    caller is expected to wrap this via ``ev_stream.fail(exc)`` so
    ``runtime.exec()`` sees it.
    """
    while True:
        try:
            await _retry_attempts(
                attempt_fn, is_committed_fn=is_committed_fn,
                max_attempts=max_attempts, label=label, provider=provider,
                abort_check=abort_check, on_retry=on_retry,
            )
            return
        except ProviderStreamError as exc:
            if (
                not network_wait_allowed(max_attempts)
                or not getattr(exc, "transport_exhausted", False)
                or not is_network_unreachable(exc)
                or is_committed_fn()
            ):
                raise
            back = await _wait_for_network(
                attempt_fn, exc, is_committed_fn=is_committed_fn, label=label,
                provider=provider, abort_check=abort_check, on_retry=on_retry,
            )
            if back is None:
                return


def _refund_transport(count: int) -> None:
    if count <= 0:
        return
    from .recovery import current_recovery
    state = current_recovery.get()
    if state is not None:
        state.refund("transport", count)


async def _wait_for_network(
    attempt_fn: Callable[[], Awaitable[None]],
    failure: ProviderStreamError,
    *,
    is_committed_fn: Callable[[], bool],
    label: str,
    provider: Optional[str],
    abort_check: Callable[[], bool] | None,
    on_retry: RetryNotice | None,
) -> ProviderStreamError | None:
    """Re-try an unreachable request until the network answers.

    Returns ``None`` once an attempt succeeds, or the first failure that
    is not a connection failure (the network is back; the caller retries
    it normally). Raises the last failure, flagged exhausted, when the
    wait limit or the caller's deadline is reached.
    """
    from .recovery import current_recovery
    reserved = int(getattr(failure, "transport_reserved", 0) or 0)
    started = _now()
    probe = 0
    _log.info("%s network wait: endpoint unreachable — %s; waiting for the network", label, failure)
    while True:
        sleep_s = network_wait_delay(started, probe)
        if sleep_s is None:
            failure.transport_exhausted = True  # type: ignore[attr-defined]
            raise failure
        state = current_recovery.get()
        if state is not None and state.phase != "cancelled":
            state.phase = "retrying"
        probe += 1
        _notify(on_retry, attempt=probe, max_attempts=None, reason="network_wait",
                delay_ms=round(sleep_s * 1000), error=retry_error_text(failure))
        await _sleep_unless_aborted(sleep_s, abort_check)
        try:
            await attempt_fn()
        except ExecInterrupt:
            raise
        except Exception as exc:
            wrapped = exc if isinstance(exc, ProviderStreamError) else _wrap_failure(
                exc, provider, is_committed_fn(),
            )
            if is_network_unreachable(wrapped) and not is_committed_fn():
                failure = wrapped
                failure.transport_reserved = reserved  # type: ignore[attr-defined]
                continue
            # The network answered: earlier reservations were spent on the
            # outage, so the request starts a fresh retry budget.
            _refund_transport(reserved)
            if not getattr(wrapped, "retryable", False) or is_committed_fn():
                raise wrapped
            _log.info("%s network wait: network is back after %.0fs", label, _now() - started)
            return wrapped
        _refund_transport(reserved)
        _log.info("%s network wait: network is back after %.0fs", label, _now() - started)
        return None


async def _retry_attempts(
    attempt_fn: Callable[[], Awaitable[None]],
    *,
    is_committed_fn: Callable[[], bool],
    max_attempts: int,
    label: str,
    provider: Optional[str],
    abort_check: Callable[[], bool] | None,
    on_retry: RetryNotice | None,
) -> None:
    def _mark_exhausted(exc: BaseException) -> BaseException:
        # Tell runtime.exec this transport error already burned the
        # provider's OWN retry budget, so exec must not re-retry it with a
        # fresh max_retries budget — that's the max_attempts × max_retries
        # multiplication. See docs/design/providers/reliability/error-and-timeout-mechanism.html §5.
        try:
            exc.transport_exhausted = True  # type: ignore[attr-defined]
            exc.transport_reserved = transport_reserved  # type: ignore[attr-defined]
        except AttributeError:
            # An exception type without a writable __dict__ can't carry the
            # marker; exec may then retry it once more.
            _log.debug("cannot mark %s transport-exhausted", type(exc).__name__)
        return exc

    last_exc: Optional[BaseException] = None
    transport_reserved = 0
    for attempt in range(max_attempts):
        # Caller's end-to-end deadline (published by runtime.exec). Don't
        # even start another attempt past it — the inner loop being blind
        # to the outer budget is the core defect this closes.
        if _dl_expired():
            if last_exc is None:
                last_exc = ProviderStreamError(
                    f"deadline exceeded before {label} attempt {attempt + 1}",
                    retryable=False, provider=provider,
                )
            # The deadline is spent: never wait for the network after it.
            if isinstance(last_exc, ProviderStreamError):
                last_exc.network_unreachable = False  # type: ignore[attr-defined]
            raise _mark_exhausted(last_exc)
        try:
            await attempt_fn()
            _refund_transport(transport_reserved)
            return
        except ExecInterrupt:
            raise  # caller hard-stop — never retry, never swallow
        except ProviderStreamError as exc:
            last_exc = exc
            if exc.provider is None and provider is not None:
                exc.provider = provider
        except Exception as exc:
            last_exc = _wrap_failure(exc, provider, is_committed_fn())

        retryable = bool(getattr(last_exc, "retryable", False))
        last_attempt = attempt >= max_attempts - 1
        if not retryable or last_attempt:
            assert last_exc is not None
            # Budget exhausted on a retryable transport error → flag it so
            # exec doesn't multiply the retries.
            if retryable and last_attempt:
                _mark_exhausted(last_exc)
            raise last_exc

        if abort_check is not None and abort_check():
            raise ExecInterrupt("cancelled")
        from .recovery import reserve_recovery, current_recovery
        state = current_recovery.get()
        if state is not None and state.phase == "cancelled":
            raise ExecInterrupt("cancelled")
        if retry_after_beyond_cap(getattr(last_exc, "retry_after_s", None)):
            raise _mark_exhausted(last_exc)
        if not reserve_recovery("transport"):
            raise _mark_exhausted(last_exc)
        transport_reserved += 1
        recovery = current_recovery.get()
        if recovery is not None:
            recovery.started(provider=provider, retry_reason="transport")
        retry_after = getattr(last_exc, "retry_after_s", None)
        sleep_s = stream_backoff_seconds(attempt, retry_after)
        # Don't sleep past the caller's deadline — if the backoff alone
        # would cross it, give up now (flagged exhausted) instead of
        # burning the remaining budget on a sleep we can't use.
        rem = _dl_remaining()
        if rem is not None and rem <= sleep_s:
            last_exc.network_unreachable = False  # type: ignore[attr-defined]
            raise _mark_exhausted(last_exc)
        _log.info(
            "%s stream retry: attempt %s/%s after %.1fs — %s",
            label, attempt + 1, max_attempts, sleep_s, last_exc,
        )
        _notify(on_retry, attempt=attempt + 2, max_attempts=max_attempts,
                reason="transport", delay_ms=round(sleep_s * 1000),
                error=retry_error_text(last_exc))
        await _sleep_unless_aborted(sleep_s, abort_check)


__all__ = [
    "retry_error_text",
    "PROVIDER_STREAM_MAX_ATTEMPTS",
    "ProviderStreamError",
    "NETWORK_WAIT_MAX_S",
    "is_network_unreachable",
    "network_wait_allowed",
    "network_wait_delay",
    "network_wait_started",
    "is_retryable_status",
    "read_retry_after",
    "RETRY_AFTER_CAP_S",
    "retry_after_beyond_cap",
    "retry_stream",
    "stream_backoff_seconds",
    "_sleep_unless_aborted",
]

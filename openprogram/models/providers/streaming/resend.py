"""Re-sending a streamed request whose reply failed in transport.

A reply that stops arriving (the idle bound of ``utils/stream_idle.py``) or
whose connection drops before its final event says nothing about the request
itself, so the same request goes out again. This is the one place that does
it for every provider and every caller of ``stream()`` / ``stream_simple()``:
the conversation turn path, ``AgentSession`` and one-shot calls alike.

The whole request is sent again and the part already received is void, as
Codex CLI and Claude Code do for a stream that stalls: nothing of a failed
response enters the context, and no tool has run for it, because tools run
only after a response's final event. The ``retry`` event that announces the
new attempt carries ``restart=True`` when content had already been streamed,
so a consumer drops what it showed of the failed response.

The bound is the shared one. Each re-send reserves a transport recovery on
the invocation's ``RecoveryState`` (``utils/recovery.py``) and is capped at
``OPENPROGRAM_PROVIDER_STREAM_RETRIES`` attempts for one request; a request
that gets through refunds its reservations. When the bound is spent the
failure is flagged ``transport_exhausted`` so no layer above retries it again.

Not re-sent here:

* a failure that is not transport (rate limit, overload, auth, a bad
  request): the provider SDKs and the failover chain own those;
* a request under a governed reservation (a Goal or a budgeted job), which
  is one attempt by contract (``budget.provider_retry_attempts``);
* a failure an adapter's own retry loop already spent its attempts on
  (Codex, Chat Completions): it arrives flagged ``transport_exhausted``.
  Those loops stop once content has streamed. Chat Completions then raises
  the failure unflagged and it is re-sent here. Codex keeps the part it
  received and ends the response as cut short, or, with partial recovery
  off, fails with an error it declares not retryable; neither is re-sent;
* anything after a stop.
"""
from __future__ import annotations

import logging
from typing import Any

from ..types import EventRetry
from ..utils.errors import ErrorReason, ExecInterrupt, taxonomy_fields

_log = logging.getLogger(__name__)



class Resend:
    """The re-send decisions of one request, attempt after attempt."""

    def __init__(self, model: Any, opts: Any, *, governed: bool) -> None:
        from ..utils.stream_retry import PROVIDER_STREAM_MAX_ATTEMPTS

        self._model = model
        self._opts = opts
        self._max_attempts = 1 if governed else max(1, PROVIDER_STREAM_MAX_ATTEMPTS)
        self._failed = 0
        self._reserved = 0

    def _stopped(self) -> bool:
        signal = getattr(self._opts, "signal", None)
        return bool(signal is not None and callable(getattr(signal, "is_set", None)) and signal.is_set())

    def _exhausted(self, exc: BaseException) -> None:
        """Flag ``exc`` so no layer above spends another retry on it."""
        try:
            exc.transport_exhausted = True  # type: ignore[attr-defined]
            exc.transport_reserved = self._reserved  # type: ignore[attr-defined]
        except AttributeError:
            _log.debug("cannot mark %s transport-exhausted", type(exc).__name__)

    def plan(self, exc: BaseException, *, shown: bool) -> tuple[EventRetry, float] | None:
        """The retry notice and the wait before the next attempt, or ``None`` to fail with ``exc``.

        ``shown`` says whether this attempt had streamed any content.
        """
        from ..structured_output import StructuredOutputError
        from ..utils.deadline import expired, remaining
        from ..utils.recovery import current_recovery, reserve_recovery
        from ..utils.stream_retry import (
            retry_after_beyond_cap,
            retry_error_text,
            stream_backoff_seconds,
        )

        if isinstance(exc, (ExecInterrupt, StructuredOutputError)) or self._stopped():
            return None
        if getattr(exc, "transport_exhausted", False):
            return None
        reason, retryable, retry_after = taxonomy_fields(exc)
        if not retryable or reason != ErrorReason.TRANSPORT.value:
            return None
        state = current_recovery.get()
        if state is not None and state.phase == "cancelled":
            return None
        if self._failed + 1 >= self._max_attempts:
            # A single-attempt request (governed) never had retries to spend.
            if self._max_attempts > 1:
                self._exhausted(exc)
            return None
        if expired() or retry_after_beyond_cap(retry_after):
            self._exhausted(exc)
            return None
        if not reserve_recovery("transport"):
            self._exhausted(exc)
            return None
        self._reserved += 1
        wait_s = stream_backoff_seconds(self._failed, retry_after)
        left = remaining()
        if left is not None and left <= wait_s:
            self._exhausted(exc)
            return None
        self._failed += 1
        if state is not None:
            state.started(provider=getattr(self._model, "provider", None), retry_reason="transport")
        _log.info(
            "%s stream re-send: attempt %s/%s after %.1fs — %s",
            getattr(self._model, "api", "?"), self._failed + 1, self._max_attempts, wait_s, exc,
        )
        return EventRetry(
            attempt=self._failed + 1, max_attempts=self._max_attempts, reason="transport",
            delay_ms=round(wait_s * 1000), error=retry_error_text(exc), restart=True if shown else None,
        ), wait_s

    async def wait(self, wait_s: float) -> None:
        """Sit out the backoff; a stop during it ends the request instead."""
        from ..utils.stream_retry import _sleep_unless_aborted

        await _sleep_unless_aborted(wait_s, self._stopped)

    def delivered(self) -> None:
        """The request got through: its re-sends are not charged to the invocation."""
        if self._reserved <= 0:
            return
        from ..utils.recovery import current_recovery

        state = current_recovery.get()
        if state is not None:
            state.refund("transport", self._reserved)
        self._reserved = 0


def reached_provider(exc: BaseException, *, answered: bool) -> bool:
    """Whether a failed attempt may have been billed.

    ``answered``: the attempt streamed content or reported tokens, so the
    provider accepted the request. An adapter's ``start`` event is no such
    sign: adapters emit it before they send anything. A reply that never
    said anything within the idle bound is counted too, as far as this side
    can tell. A refusal (a status error) or a connection that failed before
    any content is not.
    """
    if answered:
        return True
    seen = 0
    current: BaseException | None = exc
    while current is not None and seen < 4:
        name = type(current).__name__
        if name in ("StreamIdleTimeout", "StreamTotalTimeout") or "StreamIdleTimeout" in str(current):
            return True
        current = current.__cause__ or current.__context__
        seen += 1
    return False


__all__ = ["Resend", "reached_provider"]

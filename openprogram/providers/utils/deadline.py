"""A single end-to-end wall-clock deadline, carried via a ContextVar.

The problem this solves: ``runtime.exec(timeout_s=...)`` only bounds its
OWN retry loop, and only between attempts. The provider's inner stream
retry loop (``stream_retry.retry_stream``) and the codex SSE parser run
on their own count/budget, completely blind to the caller's deadline. A
persistent transport error therefore multiplies the two loops
(``max_attempts`` × ``max_retries``) with nothing capping the total
wall-clock. See ``docs/design/providers/error-and-timeout-mechanism.html``.

Rather than thread a ``timeout`` argument through every layer (which
would break the ``_call(content, model, response_format)`` override
contract every provider implements), we publish a single **absolute
monotonic deadline** in a ContextVar — the same pattern ``runtime`` already
uses for ``_current_tools`` / ``_current_tool_policy``. ``exec`` sets it
once around its retry loop; the inner stream-retry loop and the SSE
parser read it and refuse to start a new attempt / sleep / block past it.

This module has ZERO imports beyond stdlib so both ``runtime`` (high
level) and ``providers.utils.stream_retry`` / the codex provider (low
level) can import it without an import cycle.

ContextVar propagation notes:
  * ``asyncio.run(coro)`` copies the *current* context into the Task it
    creates, so a deadline set on the calling (sync) frame reaches the
    async provider code. (``runtime._run_async`` additionally forwards the
    context when it has to offload to a worker thread.)
  * ``set_deadline`` returns a token; pass it to ``reset_deadline`` in a
    ``finally``. Nesting is safe (token-based), so an inner ``exec`` with a
    smaller remaining budget restores the outer deadline on exit.
"""

from __future__ import annotations

import contextvars
import time
from typing import Optional

# Absolute deadline as a ``time.monotonic()`` value (NOT a duration).
# ``None`` means "no deadline" — every helper degrades to a no-op, so
# callers that never set one keep the historical unbounded behaviour.
_current_deadline: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar(
    "_current_deadline", default=None,
)


def set_deadline(monotonic_deadline: Optional[float]) -> "contextvars.Token":
    """Publish an absolute ``time.monotonic()`` deadline. Returns a token
    for :func:`reset_deadline`. Pass ``None`` to explicitly clear it for
    an inner scope (e.g. a sub-call that should be unbounded)."""
    return _current_deadline.set(monotonic_deadline)


def reset_deadline(token: "contextvars.Token") -> None:
    """Restore the deadline that was in effect before the matching
    :func:`set_deadline`. Always call in a ``finally``."""
    try:
        _current_deadline.reset(token)
    except (ValueError, LookupError):
        # Token from a different context (e.g. crossed a thread/loop
        # boundary). The ContextVar copy in that scope is discarded
        # anyway, so a failed reset is harmless.
        pass


def get_deadline() -> Optional[float]:
    """The absolute monotonic deadline in effect, or ``None``."""
    return _current_deadline.get()


def remaining() -> Optional[float]:
    """Seconds left until the deadline, or ``None`` when unset. May be
    negative once the deadline has passed (callers usually treat <= 0 as
    expired via :func:`expired`)."""
    d = _current_deadline.get()
    if d is None:
        return None
    return d - time.monotonic()


def expired() -> bool:
    """True only when a deadline is set AND already reached. ``False`` when
    no deadline is in effect (unbounded)."""
    r = remaining()
    return r is not None and r <= 0


__all__ = [
    "set_deadline",
    "reset_deadline",
    "get_deadline",
    "remaining",
    "expired",
]

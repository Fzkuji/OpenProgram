"""How large a request is: one rule for every place that asks.

The automatic compaction before a turn, the one between a turn's steps, the
gate before each provider request and the context ring all read the size of
the request from here, so they never disagree about whether it fits.

The size is the provider's own count of the last request that was sent on
the same model, plus a local estimate of what changed since (the anchor and
its delta). Claude Code (``tokenCountWithEstimation``) and Codex
(``get_total_token_usage``) measure the same way. Without a usable anchor it
is the local estimate alone.

A provider count is an anchor only when it measured one request:

* A reply that ran provider-side tools (a web search, ``serverToolUse``) is
  several model passes; xAI and OpenAI report their usage summed, so the
  count is a multiple of the prompt. Claude Code reads the last of
  Anthropic's ``usage.iterations`` for the same reason.
* A reply that failed or was stopped may carry partial or no usage.
* A count larger than the window, or more than twice the local estimate of
  the same request plus a margin, did not measure that request.

The anchor's difference from its own estimate is added, never multiplied: a
ratio taken from one bad count once scaled every later estimate by 3.95 and
refused requests that fit.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Optional

#: A count may exceed twice its request's estimate by this much before it is
#: not trusted: small requests carry fixed overheads the estimate omits.
_MARGIN = 8_000


def prompt_tokens(usage: Any) -> int:
    """The prompt size a usage record reports: input, cache reads and cache
    writes (Anthropic counts a cache write apart from ``input``)."""
    if usage is None:
        return 0
    get = (usage.get if isinstance(usage, dict) else
           (lambda key, default=0: getattr(usage, key, default)))
    return int(get("input", 0) or 0) + int(get("cache_read", 0) or 0) + int(get("cache_write", 0) or 0)


def single_pass(message: Any) -> bool:
    """Whether a reply's usage measured exactly one request."""
    if getattr(message, "stop_reason", None) in ("error", "aborted"):
        return False
    for block in getattr(message, "content", None) or []:
        if getattr(block, "type", None) == "serverToolUse":
            return False
    return True


def plausible(measured: int, estimate: int, window: int | None) -> bool:
    """Whether ``measured`` can be the size of a request estimated at
    ``estimate`` in a window of ``window``."""
    if measured <= 0:
        return False
    if window and measured > window * 1.05:
        return False
    if estimate > 0 and measured > 2 * estimate + _MARGIN:
        return False
    return True


def model_key(model: Any) -> str:
    """Anchors are kept per model: another tokenizer counts differently."""
    try:
        raw = model.model_dump_json()
    except Exception:  # noqa: BLE001 - any model-like object
        raw = f"{getattr(model, 'provider', '')}/{getattr(model, 'id', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class Anchor:
    """A provider count of one request and the local estimate of it."""

    measured: int
    estimate: int

    @property
    def offset(self) -> int:
        return self.measured - self.estimate


def anchor_from(message: Any, estimate: int, window: int | None) -> Optional[Anchor]:
    """The anchor a finished reply gives, or ``None`` when its count did not
    measure the request it answered."""
    usage = getattr(message, "usage", None)
    if usage is None or not getattr(usage, "tokens_reported", False):
        return None
    if not single_pass(message):
        return None
    measured = prompt_tokens(usage)
    if not plausible(measured, estimate, window):
        return None
    return Anchor(measured=measured, estimate=estimate)


def request_size(estimate: int, anchor: Optional[Anchor]) -> int:
    """The size of a request estimated at ``estimate``: the anchor's count
    plus the estimated change since, or the estimate alone."""
    if anchor is None:
        return estimate
    # The offset is what the estimator misses (reasoning the provider
    # counts differently, wrapper tokens). Never let it take more than
    # half of an estimate away.
    return max(estimate // 2, estimate + anchor.offset)


def apply(budget: Any, anchor: Optional[Anchor]) -> Any:
    """Put a ``BudgetAllocation`` at :func:`request_size`: the history slot
    takes the difference, the fixed slots stay as estimated."""
    if anchor is not None:
        total = budget.system_prompt + budget.tools_schema + budget.history
        budget.history = max(0, budget.history + request_size(total, anchor) - total)
    return budget


#: The latest anchor of each conversation, per model: what the compaction
#: before a turn and between its steps measure against. Kept in the worker
#: only; after a restart the first request measures again.
_latest: dict[tuple[str, str], Anchor] = {}


def remember(session_id: Optional[str], model: Any, anchor: Optional[Anchor]) -> None:
    """Keep ``anchor`` as the conversation's latest count on ``model``.

    A count that did not measure one request leaves the previous anchor in
    place: the next good count replaces it."""
    if session_id and anchor is not None:
        _latest[(session_id, model_key(model))] = anchor


def latest(session_id: Optional[str], model: Any) -> Optional[Anchor]:
    """The conversation's latest anchor on ``model``, if any."""
    if not session_id:
        return None
    return _latest.get((session_id, model_key(model)))


__all__ = ["Anchor", "anchor_from", "apply", "latest", "model_key", "plausible",
           "prompt_tokens", "remember", "request_size", "single_pass"]

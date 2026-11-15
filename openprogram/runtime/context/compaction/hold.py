"""Automatic compaction that keeps failing stops being tried for a while.

A conversation over the auto-compaction line compacts before every new
turn. When its summaries never come (the model cuts each one short, the
provider answers 429s, the summary does not shrink anything) each try
still pays for the whole conversation as input, and nothing gets shorter.

After :data:`FAILS_MOST` failures in a row of one branch's compaction on
one model, each within :data:`HELD_FOR_S` of the one
before, automatic compaction of it is held until :data:`HELD_FOR_S` has
passed since the last failure; then one is tried again. A summary that
installs clears the count. A compaction the user asks for (``/compact``)
always goes, and clears the hold when it works.

The memory is per process and bounded.

After magpie's compaction guard (yetone/magpie, MIT).
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

FAILS_MOST = 3
HELD_FOR_S = 300.0
_REMEMBERED = 1024


@dataclass
class _Failures:
    count: int
    last: float
    why: str


class CompactionHold:
    def __init__(self, *, fails_most: int = FAILS_MOST, held_for_s: float = HELD_FOR_S) -> None:
        self._fails_most = fails_most
        self._held_for_s = held_for_s
        self._failures: OrderedDict[tuple[str, ...], _Failures] = OrderedDict()
        self._lock = threading.Lock()

    def held(self, key: tuple[str, ...], *, now: float | None = None) -> str | None:
        """Why automatic compaction of ``key`` is held now, or ``None`` when it may go."""
        now = time.monotonic() if now is None else now
        with self._lock:
            failures = self._failures.get(key)
            if failures is None or failures.count < self._fails_most:
                return None
            if now - failures.last >= self._held_for_s:
                return None
            return (
                f"automatic compaction paused after {failures.count} failures in a row "
                f"(last: {failures.why})"
            )

    def failed(self, key: tuple[str, ...], why: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            failures = self._failures.pop(key, None)
            if failures is None or now - failures.last >= self._held_for_s:
                failures = _Failures(count=0, last=now, why=why)
            failures.count += 1
            failures.last = now
            failures.why = why
            self._failures[key] = failures
            while len(self._failures) > _REMEMBERED:
                self._failures.popitem(last=False)

    def done(self, key: tuple[str, ...]) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()


default_hold = CompactionHold()


def hold_key(session_id: str, model: object, branch_id: str | None = None) -> tuple[str, ...]:
    """The conversation, branch and model a compaction is of. Branches run
    in parallel and each compacts its own chain, so one branch's failures
    never hold another's."""
    model_id = getattr(model, "id", None) or str(model)
    provider = getattr(model, "provider", None) or ""
    return (str(session_id), f"{provider}/{model_id}", str(branch_id or ""))

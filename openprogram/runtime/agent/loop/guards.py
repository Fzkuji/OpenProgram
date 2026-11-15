"""Loop bounds and control signals: the iteration cap, steer/safe-point markers and the repeated-text guard."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openprogram.models.providers.types import AssistantMessage


# Chat turns follow Codex / DeepSeek: loop until the model returns
# text, the user cancels, or a caller-set ``max_iterations`` is hit.
# Codex compacts context instead of capping tool rounds; DeepSeek's
# ReactLoopAgent has no maxSteps and uses repeat-tool reminders.
# Identical failed tools are already skipped after two repeats.
# runtime.exec still defaults to 20.
MAX_INNER_ITERATIONS = None


@dataclass
class _SteeredResponse:
    message: AssistantMessage


class _SafePointStop(BaseException):
    """The durable owner paused before an external operation started."""


# Repetition recovery is deliberately narrow.  It applies only to a
# tool-enabled response and only after the provider has emitted one exact,
# sufficiently large text suffix repeatedly.  There is no total output or
# wall-clock quota here: healthy long responses take the normal path, while
# an exact repeated segment is treated as a failed response.
_REPETITION_MIN_PERIOD_CHARS = 32
_REPETITION_MAX_PERIOD_CHARS = 1024
_REPETITION_MIN_TOTAL_BYTES = 2048
_REPETITION_MIN_COUNT = 8
_REPETITION_TAIL_CHARS = 16 * 1024
_REPETITION_CHECK_BYTES = 128


class RepetitiveOutputError(RuntimeError):
    """The current provider response repeated one text segment without progress."""

    code = "repetitive_output"

    def __init__(self) -> None:
        super().__init__(self.code)
        # Agent-loop recovery handles this error in the same logical session.
        # If the shared allowance is exhausted, Runtime.exec must not create a
        # second session and replay already completed tool effects.
        self.retryable = False
        self.transport_exhausted = False


class _RepeatedTextGuard:
    """Detect one exact, long suffix period repeated consecutively.

    The provider may split one repeated sentence at arbitrary token/delta
    boundaries.  Keep a bounded tail and inspect the suffix as a repeated
    period instead of comparing individual deltas.  The detector has no
    aggregate output limit: it only retains the tail needed for this check.
    """

    def __init__(
        self,
        *,
        min_period_chars: int = _REPETITION_MIN_PERIOD_CHARS,
        max_period_chars: int = _REPETITION_MAX_PERIOD_CHARS,
        min_total_bytes: int = _REPETITION_MIN_TOTAL_BYTES,
        min_count: int = _REPETITION_MIN_COUNT,
        tail_chars: int = _REPETITION_TAIL_CHARS,
        check_bytes: int = _REPETITION_CHECK_BYTES,
    ) -> None:
        self._min_period_chars = min_period_chars
        self._max_period_chars = max_period_chars
        self._min_total_bytes = min_total_bytes
        self._min_count = min_count
        self._tail_chars = tail_chars
        self._check_bytes = check_bytes
        self._tail = ""
        self._pending_check_bytes = 0

    def observe(self, delta: Any) -> bool:
        if not isinstance(delta, str) or not delta:
            return False
        self._tail += delta
        if len(self._tail) > self._tail_chars:
            self._tail = self._tail[-self._tail_chars :]
        self._pending_check_bytes += len(delta.encode("utf-8"))
        if self._pending_check_bytes < self._check_bytes:
            return False
        self._pending_check_bytes = 0
        return self._is_repeated_suffix()

    def reset(self) -> None:
        self._tail = ""
        self._pending_check_bytes = 0

    def _is_repeated_suffix(self) -> bool:
        tail_bytes = len(self._tail.encode("utf-8"))
        if tail_bytes < self._min_total_bytes:
            return False
        max_period = min(self._max_period_chars, len(self._tail) // self._min_count)
        for period_chars in range(self._min_period_chars, max_period + 1):
            unit = self._tail[-period_chars:]
            previous = self._tail[-2 * period_chars : -period_chars]
            if unit != previous:
                continue
            unit_bytes = len(unit.encode("utf-8"))
            count = max(self._min_count, (self._min_total_bytes + unit_bytes - 1) // unit_bytes)
            repeated = unit * count
            if len(repeated.encode("utf-8")) >= self._min_total_bytes and self._tail.endswith(repeated):
                return True
        return False


def iteration_cap_for(max_iterations: int | None) -> int | None:
    """Return a caller cap, or ``None`` for an unbounded chat turn."""
    if max_iterations is None:
        return None
    return max(1, int(max_iterations))

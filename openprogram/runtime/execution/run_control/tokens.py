"""Turn cancellation tokens and the per-execution token registry."""
from __future__ import annotations

import threading


# ---------------------------------------------------------------------------
# Turn cancellation tokens — one per turn, never per session.
#
# A turn opens a fresh CancelToken; the LLM call, tool execution and every
# sub-task check that one object. Stopping trips the token of the turn that
# is running now. When the turn ends the token is retired, so a stop that
# arrives late cannot reach into the next turn. Nothing has to be reset on
# cleanup — the next turn simply gets a different object.
#
# The public names below (mark_cancelled / is_cancelled / clear_cancel)
# keep their meaning for callers and the WS protocol; they now resolve to
# the session's current token instead of a sticky boolean.
# ---------------------------------------------------------------------------


class CancellationToken:
    """Cancellation signal scoped to exactly one execution.

    Wraps a ``threading.Event`` so blocked worker threads can wait on it,
    and carries a ``retired`` flag: once the turn ends, ``cancel()`` is a
    no-op, which is what keeps a late stop from leaking into the next turn.
    """

    __slots__ = (
        "_event", "_retired", "_lock", "session_id", "execution_id",
    )

    def __init__(
        self, session_id: str, execution_id: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.execution_id = execution_id
        self._event = threading.Event()
        self._retired = False
        self._lock = threading.Lock()

    @property
    def event(self) -> threading.Event:
        """The underlying Event, for code that must block until cancelled."""
        return self._event

    def cancel(self) -> bool:
        """Trip this token. Returns False if the turn already ended."""
        with self._lock:
            if self._retired:
                return False
            self._event.set()
        # What waits on the signal hears it now (``wakeups``), not at a poll.
        from openprogram.runtime.execution.run_control.wakeups import fire
        fire(self._event)
        return True

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def retire(self) -> None:
        """End this token's life. Later ``cancel()`` calls do nothing."""
        with self._lock:
            self._retired = True

    @property
    def retired(self) -> bool:
        with self._lock:
            return self._retired


# Internal token alias retained for code that imports the cancellation type.
CancelToken = CancellationToken

_cancel_flags_lock = threading.Lock()

# (session_id, execution_id) → the token owned by that execution. A None
# execution_id is the foreground slot shared by Web, MCP and ACP turns, which
# admit one at a time; background jobs sharing a session bind their job id
# so a stop aimed at one never reaches a sibling.
# Absent when no turn is in flight, which is why a stop between turns is a
# no-op rather than a flag that poisons whatever runs next.
_current_tokens: dict[tuple[str, str | None], CancellationToken] = {}


def current_token(
    session_id: str, *, execution_id: str | None = None,
) -> CancellationToken | None:
    """The token of the turn running on this session, or None between turns."""
    with _cancel_flags_lock:
        return _current_tokens.get((session_id, execution_id))

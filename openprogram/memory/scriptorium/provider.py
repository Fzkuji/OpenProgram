"""Markdown-workspace memory, wired to the agent runtime's hooks.

Turns are not written as they happen. Each one is archived as evidence
and left to accumulate; the model is only asked to fold them into topic
files once there is a batch worth a call. Writing a paragraph per turn
would cost a model call per turn and produce memory shaped like a
transcript rather than like knowledge.
"""

from __future__ import annotations

import logging
from typing import Any

from ..provider import MemoryProvider, fence_memory

logger = logging.getLogger(__name__)

# How much conversation to gather before asking the model to write it up.
# Small enough that a long session is written in several passes rather
# than one oversized call, large enough that a short exchange does not
# trigger one at all.
WRITE_TOKEN_THRESHOLD = 16_000


class ScriptoriumMemoryProvider(MemoryProvider):
    """File-based memory: sources + topics + core."""

    _session_id: str = ""

    @property
    def name(self) -> str:
        return "scriptorium"

    def initialize(self, *, session_id: str = "", **kwargs: Any) -> None:
        self._session_id = session_id

    # -- Reading --------------------------------------------------------

    def system_prompt_block(self) -> str:
        """Core memory, injected into every session."""
        from .. import store

        try:
            text = store.core().read_text(encoding="utf-8").strip()
        except OSError:
            return ""
        # A bare heading is the empty state, not content worth injecting.
        body = "\n".join(
            line for line in text.splitlines() if not line.startswith("# ")
        ).strip()
        return fence_memory(text) if body else ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Whatever memory bears on the turn about to run."""
        if not query or not query.strip():
            return ""
        try:
            from .retrieval import inspect
            from .. import store

            found = inspect.search(store.ensure(), query, top_k=5)
        except Exception as exc:  # noqa: BLE001
            # An empty or unindexed workspace is the ordinary case on a
            # fresh install, not something to surface mid-turn.
            logger.debug("memory recall failed: %s", exc)
            return ""
        rendered = "\n\n".join(
            hit["content"]
            for hit in found.get("results", []) if hit.get("content")
        ).strip()
        return fence_memory(rendered) if rendered else ""

    # -- Writing --------------------------------------------------------

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
    ) -> None:
        """Write memory if this turn brought the session over the line.

        The turn's text is not taken from the arguments: the session
        store already holds it, in order, and reading from there is what
        lets a restart pick up where it left off. Cheap in the common
        case — a cursor lookup and a token count, no model call.
        """
        from .writing import record_turn

        try:
            record_turn(
                session_id or self._session_id,
                token_threshold=WRITE_TOKEN_THRESHOLD,
            )
        except Exception as exc:  # noqa: BLE001
            # Memory must never take a conversation down with it.
            logger.debug("memory write failed: %s", exc)

    def maintain(self, **kwargs: Any) -> dict[str, Any]:
        """Reorganise topic files. Called by the nightly scheduler."""
        from .writing import sweep

        try:
            return sweep(model=kwargs.get("model"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory maintenance failed: %s", exc)
            return {"status": "failed", "error": str(exc)}

    def on_session_end(
        self,
        messages: list[dict[str, Any]],
        *,
        session_id: str = "",
    ) -> bool:
        """Flush whatever is left, however little it is.

        The threshold exists so that short exchanges do not each cost a
        call. At the end of a session there is no later batch to join,
        so the remainder is written regardless of size.

        False when any of it is still unwritten. An unreachable model or
        a rejected edit must not be reported as a finished session: the
        watcher would mark it done and nothing would ever come back for
        those turns.
        """
        from .writing import flush

        try:
            return flush(
                session_id or self._session_id, messages,
                token_threshold=WRITE_TOKEN_THRESHOLD,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("memory flush failed: %s", exc)
            return False

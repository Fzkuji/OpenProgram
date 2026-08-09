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

from ..provider import MemoryProvider, WriteIncomplete, fence_memory

logger = logging.getLogger(__name__)

# Transaction codes that clear on their own. Everything else the write
# transaction raises is a verdict on the content the writer produced,
# and the same content next poll gets the same verdict.
RETRYABLE_CODES = frozenset({
    "CONCURRENT_UPDATE", "GIT_COMMIT_FAILED", "EMBEDDING_UNAVAILABLE",
})

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

    def system_prompt(self) -> str:
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

    def search(self, query: str, *, session_id: str = "") -> str:
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

    def write(
        self,
        messages: list[dict[str, Any]] | None = None,
        *,
        session_id: str = "",
        force: bool = False,
    ) -> WriteIncomplete | None:
        """Fold the conversation into memory once there is enough of it.

        Per turn, ``force`` False: cheap in the common case — a cursor
        lookup and a token count, no model call — and it writes only
        when the session has crossed the threshold. At a session
        boundary, ``force`` True: there is no later batch to join, so
        the remainder is written however little of it there is.

        ``messages`` is left out on the per-turn call. The session store
        already holds the conversation, in order, and reading it back
        from there is what lets a restart pick up where it left off; the
        idle watcher already has the list and passes it in.

        Nothing is returned once every turn has landed, and below the
        threshold nothing was owed. Anything still unwritten comes back
        as ``WriteIncomplete``: an unreachable model or a workspace
        another writer holds is worth another pass, a batch the
        transaction refused is not, and reporting either as finished
        would mark the session done with nothing ever coming back for
        those turns.
        """
        from .management.transaction import TransactionError
        from . import writing

        try:
            return writing.write(
                session_id or self._session_id, messages,
                token_threshold=WRITE_TOKEN_THRESHOLD, force=force,
            )
        except TransactionError as exc:
            logger.debug("memory write rejected: %s", exc)
            return WriteIncomplete(
                f"{exc.code}: {exc.message}",
                retryable=exc.code in RETRYABLE_CODES,
            )
        except Exception as exc:  # noqa: BLE001
            # Memory must never take a conversation down with it. A
            # missing CLI or an unreachable model is transient: the
            # conversation is safe in the session store, so the next
            # pass costs nothing and loses nothing.
            logger.debug("memory write deferred: %s", exc)
            return WriteIncomplete(str(exc))

    def reorganize(self, **kwargs: Any) -> dict[str, Any]:
        """Rewrite topic files. Called by the nightly scheduler."""
        from . import writing

        try:
            return writing.reorganize(model=kwargs.get("model"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory reorganize failed: %s", exc)
            return {"status": "failed", "error": str(exc)}


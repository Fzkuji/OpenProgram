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

from .backend import (
    MemoryBackend,
    WriteFailure,
    classify_memory_write_failure,
    fence_memory,
)

logger = logging.getLogger(__name__)

# How much conversation to gather before asking the model to write it up.
# Small enough that a long session is written in several passes rather
# than one oversized call, large enough that a short exchange does not
# trigger one at all.
WRITE_TOKEN_THRESHOLD = 16_000


class LocalMemoryBackend(MemoryBackend):
    """File-based memory: sources + topics + core."""

    _session_id: str = ""

    @property
    def name(self) -> str:
        return "local"

    def initialize(self, *, session_id: str = "", **kwargs: Any) -> None:
        self._session_id = session_id

    # -- Reading --------------------------------------------------------

    def system_prompt(self, *, tier: str | None = None) -> str:
        """Core memory, injected into every session.

        ``core.md`` is rendered only from blocks whose evidence is
        trusted, so what lands here is already filtered by trust. ``tier``
        is accepted so the caller's resolved authority reaches the
        backend rather than being looked up again here; per-tier
        redaction of the block itself is not yet rendered.
        """
        from . import store

        try:
            text = store.core().read_text(encoding="utf-8").strip()
        except OSError:
            return ""
        # A bare heading is the empty state, not content worth injecting.
        body = "\n".join(
            line for line in text.splitlines() if not line.startswith("# ")
        ).strip()
        return fence_memory(text) if body else ""

    def search(
        self,
        query: str,
        *,
        session_id: str = "",
        tier: str | None = None,
    ) -> str:
        """Whatever memory bears on the turn about to run.

        Pending evidence — text archived from an unpaired speaker — is
        dropped here. It stays reachable through the ``memory_search``
        tool, where the model asks for it and sees its trust_state, but
        it never enters the turn's context unasked.

        A Topic block counts as pending when any Source it cites is,
        which the index resolves before this sees it. Without that a
        block written from unvouched speech would be recalled as
        ordinary memory: the prose carries no trust marker of its own,
        so filtering the archive alone let the claim through while
        stopping only the quote it came from.
        """
        if not query or not query.strip():
            return ""
        from openprogram.agent.authority import TIER_CAPABILITIES

        # The tier arrives already resolved by the caller that authorized
        # the turn; this only reads the fixed table, and never re-checks
        # the pairing state, which could have changed since.
        #
        # A turn with no tier at all is the local owner's, the same
        # reading ``render_model_input_from`` applies to a node with no
        # authority record: the envelope exists to attribute *channel*
        # speech, so its absence means nobody but the owner was involved.
        # Every channel turn carries a tier by construction, so this
        # cannot be reached by widening a paired request.
        if tier is not None and "memory.read" not in TIER_CAPABILITIES.get(
            tier, frozenset()
        ):
            return ""
        try:
            from .retrieval import inspect
            from . import store

            found = inspect.search(store.ensure(), query, top_k=5)
        except Exception as exc:  # noqa: BLE001
            # An empty or unindexed workspace is the ordinary case on a
            # fresh install, not something to surface mid-turn.
            logger.debug("memory recall failed: %s", exc)
            return ""
        rendered = "\n\n".join(
            hit["content"]
            for hit in found.get("results", [])
            if hit.get("content") and hit.get("trust_state") != "pending"
        ).strip()
        return fence_memory(rendered) if rendered else ""

    # -- Writing --------------------------------------------------------

    def write(
        self,
        messages: list[dict[str, Any]] | None = None,
        *,
        session_id: str = "",
        force: bool = False,
    ) -> WriteFailure | None:
        """Fold the conversation into memory once there is enough of it.

        Per turn, ``force`` False: cheap in the common case — a node-marker
        scan and a token count, no model call — and it writes only
        when the session has crossed the threshold. At a session
        boundary, ``force`` True: there is no later batch to join, so
        the remainder is written however little of it there is.

        ``messages`` is left out on the per-turn call. The session store
        already holds the conversation, in order, and reading it back
        from there is what lets a restart pick up where it left off; the
        idle watcher already has the list and passes it in.

        Nothing is returned once every turn has landed, and below the
        threshold nothing was owed. Anything still unwritten comes back
        as ``WriteFailure``: an unreachable model or a workspace
        another writer holds is worth another pass, a batch the
        transaction refused is not, and reporting either as finished
        would mark the session done with nothing ever coming back for
        those turns.
        """
        from . import writing

        try:
            return writing.write(
                session_id or self._session_id, messages,
                token_threshold=WRITE_TOKEN_THRESHOLD, force=force,
            )
        except Exception as exc:  # noqa: BLE001
            # Memory must never take a conversation down with it. Retry only
            # exceptions that explicitly classify themselves as transient;
            # an unknown exception may be a permanent config/auth failure.
            logger.debug("memory write deferred: %s", exc)
            failure = classify_memory_write_failure(exc)
            return WriteFailure(
                str(exc),
                retryable=failure.retryable,
                reason_code=failure.reason_code,
            )

    def reorganize(self, **kwargs: Any) -> dict[str, Any]:
        """Rewrite topic files. Called by the nightly scheduler."""
        from . import writing

        try:
            return writing.reorganize(model=kwargs.get("model"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory reorganize failed: %s", exc)
            return {"status": "failed", "error": str(exc)}

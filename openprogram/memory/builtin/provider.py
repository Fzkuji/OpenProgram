"""Builtin memory provider implementation.

Wires the storage layer (short-term + wiki + core) to the agent
runtime via ``MemoryProvider`` lifecycle hooks. No external services,
no embeddings — just file-system and FTS5.
"""
from __future__ import annotations

import logging
from typing import Any

from .. import core, short_term
from ..provider import MemoryProvider, fence_memory
from . import recall, summarizer

logger = logging.getLogger(__name__)


class BuiltinMemoryProvider(MemoryProvider):
    """File-based memory: short-term + wiki + core, FTS5 for recall."""

    @property
    def name(self) -> str:
        return "builtin"

    # -- Lifecycle ------------------------------------------------------------

    def initialize(self, *, session_id: str = "", **kwargs: Any) -> None:
        self._session_id = session_id
        self._summarize_model = kwargs.get("summarize_model")  # callable or None

    # -- System prompt --------------------------------------------------------

    def system_prompt_block(self) -> str:
        return core.system_prompt_block()

    # -- Per-turn -------------------------------------------------------------

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not query or not query.strip():
            return ""
        try:
            raw = recall.recall_for_prompt(query)
        except Exception as e:  # noqa: BLE001
            logger.debug("recall failed: %s", e)
            return ""
        if not raw:
            return ""
        return fence_memory(raw)

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
    ) -> None:
        # Pattern-match obvious facts. Anything richer goes through
        # on_session_end where we run the LLM summarizer.
        if not user_content:
            return
        sid = session_id or getattr(self, "_session_id", "")
        try:
            for phrase in recall.cheap_extract(user_content, assistant_content):
                short_term.append_text(
                    phrase,
                    type="observation",
                    tags=["sync"],
                    session_id=sid,
                    confidence=0.4,  # low — pattern match, not validated
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("sync_turn extract failed: %s", e)

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        """Run the LLM summarizer over the finished conversation.

        ``self._summarize_model`` should be a callable
        ``(system_prompt: str, user_text: str) -> str`` — the agent
        runtime injects one when the provider is created so we don't
        couple this module to a specific provider SDK.
        """
        if not messages:
            return
        fn = getattr(self, "_summarize_model", None)
        if fn is None:
            logger.debug("no summarize_model configured; skipping session-end extraction")
            return
        try:
            user_text = summarizer.build_input_text(messages)
            raw = fn(summarizer.system_prompt(), user_text)
            entries = summarizer.parse_extraction(raw)
        except Exception as e:  # noqa: BLE001
            logger.warning("session-end summary failed: %s", e)
            return
        sid = getattr(self, "_session_id", "")
        for e in entries:
            try:
                short_term.append_text(
                    e["text"],
                    type=e.get("type", "fact"),
                    tags=e.get("tags") or [],
                    session_id=sid,
                    confidence=float(e.get("confidence", 0.5)),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("session-end append failed: %s", exc)

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
        """Reuse the session-end summarizer on the messages about to be dropped.

        Returns text to fold into the compression summary so insights
        survive compression. Empty string if no model is configured.
        """
        fn = getattr(self, "_summarize_model", None)
        if fn is None or not messages:
            return ""
        try:
            user_text = summarizer.build_input_text(messages, max_chars=8000)
            raw = fn(summarizer.system_prompt(), user_text)
            entries = summarizer.parse_extraction(raw)
        except Exception:  # noqa: BLE001
            return ""
        if not entries:
            return ""
        return "Memory-extracted facts to preserve:\n" + "\n".join(
            f"- ({e.get('type', 'fact')}) {e['text']}" for e in entries
        )

    # -- Tool surface ---------------------------------------------------------

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        # Tool schemas are owned by openprogram.tools; this provider
        # doesn't expose its own. Memory tools route through the regular
        # tool registry and call back into the storage layer directly.
        return []

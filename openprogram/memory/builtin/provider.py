"""Builtin memory provider implementation.

Thin adapter wiring the storage layer (journal + wiki + core)
to the agent runtime via :class:`MemoryProvider` lifecycle hooks.

Under the hybrid wiki schema the heavy lifting moved out of this
file:

* Ingest now goes through :func:`openprogram.memory.ingest.ingest_session`,
  driven by ``session_watcher`` — not by ``on_session_end`` here.
* Per-turn pattern-matched extraction is gone — the LLM writes
  journal explicitly via ``memory_note``.

Only the read path + system-prompt block remain wired here.
"""
from __future__ import annotations

import logging
from typing import Any

from .. import core
from ..provider import MemoryProvider, fence_memory
from . import recall

logger = logging.getLogger(__name__)


class BuiltinMemoryProvider(MemoryProvider):
    """File-based memory: journal + wiki + core."""

    @property
    def name(self) -> str:
        return "builtin"

    def initialize(self, *, session_id: str = "", **kwargs: Any) -> None:
        self._session_id = session_id

    def system_prompt_block(self) -> str:
        return core.system_prompt_block()

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not query or not query.strip():
            return ""
        try:
            raw = recall.recall_for_prompt(query)
        except Exception as e:  # noqa: BLE001
            logger.debug("recall failed: %s", e)
            return ""
        return fence_memory(raw) if raw else ""

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
    ) -> None:
        return

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        """Deprecated — session_watcher calls agentic ingest directly."""
        return

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
        return ""

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return []

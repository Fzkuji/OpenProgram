"""Context management — what the LLM actually sees each turn.

Design lifted from the best parts of three reference systems:

* **Claude Code** — three-tier compaction:
    1. tool-result aging (microcompact) — preserves turn structure, only
       redacts stale tool outputs. No LLM call. Runs every turn.
    2. auto-compact — when token budget crosses a threshold, summarise
       the prefix INLINE so the agent loop doesn't crash on the next
       request. LLM call, but transparent to the user.
    3. manual ``/compact`` — user-initiated full summary.
* **OpenClaw** — plugin interface (``ContextEngine`` ABC) so per-agent
  policies are pluggable; default impl can be swapped without touching
  the dispatcher.
* **Hermes** — separate tool-result compressor from history
  summariser; the two concerns are different sizes and run at different
  cadences.

Public API:

    from openprogram.context import default_engine, ContextEngine
    prep = engine.prepare(agent=..., session=..., history=..., model=...)
    # prep.system_prompt + prep.messages → ready for AgentContext

    if engine.should_auto_compact(prep):
        await engine.compact(agent=..., session=..., model=..., on_event=...)
        # re-prepare with the now-summarised history

    # User-initiated:
    await engine.compact(agent=..., session=..., model=..., user_initiated=True)
"""
from __future__ import annotations

from openprogram.context.engine import (
    ContextEngine,
    CompactResult,
    DefaultContextEngine,
    TurnPrep,
    default_engine,
)
from openprogram.context.tokens import (
    estimate_message_tokens,
    estimate_history_tokens,
    real_context_window,
)


__all__ = [
    "ContextEngine",
    "CompactResult",
    "DefaultContextEngine",
    "TurnPrep",
    "default_engine",
    "estimate_message_tokens",
    "estimate_history_tokens",
    "real_context_window",
]

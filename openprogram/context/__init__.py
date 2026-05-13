"""Context management — what the LLM actually sees each turn.

Architecture: component-based, single-responsibility files composed by
``DefaultContextEngine``. Lifted from the best of Claude Code (three-tier
compaction + wall-clock aging), OpenClaw (lifecycle ABC + plugin registry),
and Hermes (reference tracking + protect-first-N + incremental summary
chain), plus our own DAG re-parent persistence.

Public API:

    from openprogram.context import default_engine, resolve_engine_for
    engine = resolve_engine_for(agent)
    prep = engine.prepare(agent=..., session=..., history=..., model=..., tools=...)
    if engine.should_auto_compact(prep):
        await engine.compact(agent=..., session_id=..., model=..., on_event=...)
    # ... run LLM ...
    engine.after_turn(session_id, usage=usage, prep=prep, on_event=on_event)
"""
from __future__ import annotations

from openprogram.context.engine import (
    CONTEXT_ENGINE_REGISTRY,
    ContextEngine,
    DefaultContextEngine,
    default_engine,
    get_engine,
    register_engine,
    resolve_engine_for,
)
from openprogram.context.tokens import (
    estimate_history_tokens,
    estimate_message_tokens,
    real_context_window,
)
from openprogram.context.types import (
    BudgetAllocation,
    CompactResult,
    ReferenceMap,
    TurnPrep,
    UsageSnapshot,
)
from openprogram.context.usage import default_tracker as usage_tracker


__all__ = [
    "BudgetAllocation",
    "CompactResult",
    "CONTEXT_ENGINE_REGISTRY",
    "ContextEngine",
    "DefaultContextEngine",
    "ReferenceMap",
    "TurnPrep",
    "UsageSnapshot",
    "default_engine",
    "estimate_history_tokens",
    "estimate_message_tokens",
    "get_engine",
    "real_context_window",
    "register_engine",
    "resolve_engine_for",
    "usage_tracker",
]

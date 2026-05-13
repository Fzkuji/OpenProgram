"""Shared dataclasses for the context-management pipeline.

Kept in a separate module so every component (UsageTracker, BudgetAllocator,
TurnAger, Summarizer, ContextEngine) can import them without sucking in
the whole package. Pure data containers — no behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Usage tracking — the real numbers, fed back from provider responses
# ---------------------------------------------------------------------------

@dataclass
class UsageSnapshot:
    """Latest known token usage for one session.

    Source priority for any field:

      1. ``provider``  — the provider's API response told us this number
                         on the most recent turn. Authoritative.
      2. ``estimate``  — we computed via tiktoken / char heuristic. Off
                         by 10-30% typically; biases high.
      3. ``cached``    — read from session.extra_meta (last persisted).

    ``source`` records which of these populated the most recent fields
    so the engine can decide how much to trust them when planning a
    compaction (a stale ``cached`` snapshot is worse than a fresh
    ``estimate`` after 5 idle minutes).
    """
    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    last_cache_read_tokens: int = 0
    last_cache_write_tokens: int = 0

    cumulative_prompt_tokens: int = 0
    cumulative_completion_tokens: int = 0
    cumulative_cache_read_tokens: int = 0

    turn_count: int = 0
    compaction_count: int = 0
    last_updated_at: float = 0.0
    last_compacted_at: float = 0.0

    source: str = "estimate"  # estimate | provider | cached

    def cache_hit_rate(self) -> float:
        denom = self.last_prompt_tokens + self.last_cache_read_tokens
        if denom <= 0:
            return 0.0
        return self.last_cache_read_tokens / denom


# ---------------------------------------------------------------------------
# Budget allocation — how the window is sliced for one turn
# ---------------------------------------------------------------------------

@dataclass
class BudgetAllocation:
    """Per-turn budget breakdown.

    Total input is whatever the LLM will accept; ``output_reserve`` is
    held aside for the response itself so a 195K/200K context doesn't
    silently cap completions at 5K tokens. Tool schemas are usually a
    couple hundred tokens but worth tracking separately because they
    invalidate prompt cache when the tool list changes mid-session.
    """
    context_window: int = 0

    system_prompt: int = 0
    history: int = 0
    tools_schema: int = 0
    output_reserve: int = 0

    @property
    def input_used(self) -> int:
        return self.system_prompt + self.history + self.tools_schema

    @property
    def input_budget(self) -> int:
        return max(0, self.context_window - self.output_reserve)

    @property
    def input_used_pct(self) -> float:
        if self.context_window <= 0:
            return 0.0
        return (self.input_used + self.output_reserve) / self.context_window

    @property
    def headroom(self) -> int:
        return max(0, self.input_budget - self.input_used)


# ---------------------------------------------------------------------------
# Per-turn prep — what assemble() produces
# ---------------------------------------------------------------------------

@dataclass
class TurnPrep:
    """The complete artefact handed to the dispatcher before each LLM call."""
    system_prompt: str
    agent_messages: list = field(default_factory=list)
    history_dicts: list[dict] = field(default_factory=list)

    budget: BudgetAllocation = field(default_factory=BudgetAllocation)
    usage: UsageSnapshot = field(default_factory=UsageSnapshot)

    # Aging telemetry
    tool_results_redacted: int = 0
    tokens_freed_by_aging: int = 0
    references_protected: int = 0
    summary_id: Optional[str] = None

    # Trace breadcrumbs for the compaction_recommended event payload
    decision_path: list[str] = field(default_factory=list)

    @property
    def estimated_tokens(self) -> int:
        return self.budget.input_used

    @property
    def context_window(self) -> int:
        return self.budget.context_window

    @property
    def budget_pct(self) -> float:
        return self.budget.input_used_pct


# ---------------------------------------------------------------------------
# Compaction outcome
# ---------------------------------------------------------------------------

@dataclass
class CompactResult:
    """Everything a compact() call wants to tell its caller."""
    ok: bool
    summary_text: str = ""
    summary_id: Optional[str] = None

    summarised_count: int = 0
    summarised_tokens: int = 0

    tokens_before: int = 0
    tokens_after: int = 0

    duration_ms: int = 0
    used_previous_summary: bool = False

    reason: str = ""  # auto | manual | overflow | forced | recovered
    error: Optional[str] = None
    fell_back_to_structural: bool = False  # LLM failed → kept tail only


# ---------------------------------------------------------------------------
# Reference tracking — which tool outputs are still cited downstream
# ---------------------------------------------------------------------------

@dataclass
class ReferenceMap:
    """Inverse index: tool_use_id → did any later assistant message
    *quote* / *cite* / *re-reference* its result?

    Built by ``ReferenceTracker.build()`` and consumed by ``TurnAger``
    to skip aging on results the model is still working with.
    """
    cited_tool_use_ids: set[str] = field(default_factory=set)
    quoted_snippets_by_msg: dict[str, set[str]] = field(default_factory=dict)
    last_built_at: float = 0.0

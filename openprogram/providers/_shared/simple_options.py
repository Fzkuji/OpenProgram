"""
Simple stream options builder utilities.

Builds base StreamOptions from SimpleStreamOptions, clamps reasoning levels,
and adjusts max tokens for thinking budgets.

Mirrors simple-options.ts
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.providers.types import Model, SimpleStreamOptions, StreamOptions, ThinkingBudgets, ThinkingLevel

DEFAULT_THINKING_BUDGETS: "ThinkingBudgets" = {
    "minimal": 1024,
    "low": 2048,
    "medium": 8192,
    "high": 16384,
}

MIN_OUTPUT_TOKENS = 1024


def build_base_options(
    model: "Model",
    options: "SimpleStreamOptions | None" = None,
    api_key: str | None = None,
) -> "StreamOptions":
    """Build a StreamOptions dict from a Model and optional SimpleStreamOptions."""
    from openprogram.providers.types import StreamOptions
    return StreamOptions(
        temperature=options.temperature if options else None,
        max_tokens=(options.max_tokens if options and options.max_tokens else None) or min(model.max_tokens, 32000),
        signal=options.signal if options else None,
        api_key=api_key or (options.api_key if options else None),
        cache_retention=options.cache_retention if options else None,
        session_id=options.session_id if options else None,
        headers=options.headers if options else None,
        on_payload=options.on_payload if options else None,
        max_retry_delay_ms=options.max_retry_delay_ms if options else None,
        metadata=options.metadata if options else None,
    )


def clamp_reasoning(effort: "ThinkingLevel | None") -> "ThinkingLevel | None":
    """Clamp 'xhigh' down to 'high' (not all providers support xhigh)."""
    return "high" if effort == "xhigh" else effort


def adjust_max_tokens_for_thinking(
    base_max_tokens: int,
    model_max_tokens: int,
    reasoning_level: "ThinkingLevel",
    custom_budgets: "ThinkingBudgets | None" = None,
) -> tuple[int, int]:
    """Return (max_tokens, thinking_budget) adjusted for a thinking level.

    Returns:
        Tuple of (max_tokens, thinking_budget).
    """
    budgets = {**DEFAULT_THINKING_BUDGETS, **(custom_budgets or {})}
    level = clamp_reasoning(reasoning_level) or "low"
    thinking_budget: int = budgets.get(level, budgets["low"])  # type: ignore[assignment]

    max_tokens = min(base_max_tokens + thinking_budget, model_max_tokens)
    if max_tokens <= thinking_budget:
        thinking_budget = max(0, max_tokens - MIN_OUTPUT_TOKENS)

    return max_tokens, thinking_budget

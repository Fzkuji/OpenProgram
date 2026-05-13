"""BudgetAllocator — slice the context window into purpose-named slots.

The naive view treats the window as one number ("I have 200K tokens").
The honest view: every turn spends tokens on **four** distinct things
that have different growth rates and different cache properties:

    system_prompt   — system + agent persona + skills index + memory
                      Slow growth, high cache value.
    tools_schema    — JSON schemas of every tool you handed the model.
                      Constant within a session; invalidates if tools
                      change.
    history         — past user / assistant / tool_result messages.
                      Grows ~linearly with turns; the only one we
                      compact.
    output_reserve  — held back so the model can actually respond.
                      Without this, a 195K/200K context silently caps
                      completions at 5K tokens.

Reserving output explicitly lets the engine make smarter decisions
("history can grow to 180K because tools+system+output = 20K") rather
than the dispatcher hard-coding ``reserveTokens=16384`` everywhere.
"""
from __future__ import annotations

from typing import Any

from openprogram.context.types import BudgetAllocation
from openprogram.context.tokens import (
    estimate_history_tokens,
    estimate_message_tokens,
)


# Typical max-output a chat turn would want — bigger than most
# real responses, smaller than the absolute max (32-128K) so we
# don't strand half the window for nothing.
DEFAULT_OUTPUT_RESERVE = 16_384


class BudgetAllocator:
    """Compute a per-turn BudgetAllocation from the live state.

    Stateless — every call is independent. The engine holds one
    instance just so per-model output reserves can be configured per
    session (e.g. claude-haiku gets a smaller reserve than opus).
    """

    def __init__(self, default_output_reserve: int = DEFAULT_OUTPUT_RESERVE):
        self._default_output_reserve = default_output_reserve

    def allocate(self, *,
                 context_window: int,
                 system_prompt: str,
                 history: list[dict],
                 tools: list[Any] | None = None,
                 output_reserve: int | None = None,
                 ) -> BudgetAllocation:
        """Return a populated BudgetAllocation. All numbers in tokens.

        Tools list is passed as the runtime AgentTool objects (which
        carry ``schema`` attribute); the schema JSON gets dumped + counted
        through the same tokenizer as messages so the breakdown is
        consistent.
        """
        sys_tokens = self._estimate_text(system_prompt)
        hist_tokens = estimate_history_tokens(history)
        tools_tokens = self._estimate_tools(tools or [])
        out_reserve = (output_reserve if output_reserve is not None
                       else self._default_output_reserve)
        # Clamp the reserve: never give back more than 25% of the
        # window. Some agents legitimately want a 64K output cap, but
        # taking it from a 100K window leaves no room for history.
        out_reserve = min(out_reserve, max(4096, context_window // 4))

        return BudgetAllocation(
            context_window=context_window,
            system_prompt=sys_tokens,
            history=hist_tokens,
            tools_schema=tools_tokens,
            output_reserve=out_reserve,
        )

    # ---- Helpers -------------------------------------------------------

    @staticmethod
    def _estimate_text(text: str) -> int:
        if not text:
            return 0
        return estimate_message_tokens({"role": "system", "content": text})

    @staticmethod
    def _estimate_tools(tools: list[Any]) -> int:
        """Tool schemas are JSON dicts. We dump them and estimate text
        weight. Adds the per-tool overhead the provider charges
        (~5 tokens/tool for the metadata wrapper)."""
        import json as _json
        total = 0
        for t in tools:
            schema = getattr(t, "schema", None) or getattr(t, "spec", None)
            if schema is None:
                total += 20  # unknown tool — guess
                continue
            try:
                text = _json.dumps(schema, default=str, ensure_ascii=False)
            except Exception:
                text = str(schema)
            total += estimate_message_tokens(
                {"role": "tool", "content": text}
            ) + 5  # per-tool overhead
        return total


default_allocator = BudgetAllocator()

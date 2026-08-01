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
    _text_tokens,
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
        """Sum of the single-tool estimates — same pricing as the per-tool
        breakdown (:func:`estimate_tools_breakdown`), by construction."""
        return sum(_estimate_one_tool(t)[0] for t in tools)


def _estimate_one_tool(t: Any) -> tuple[int, bool]:
    """(tokens, deferred) for one tool — THE single pricing rule.

    Price what actually goes on the wire, per tool:

    * A **deferred** tool contributes one bare NAME to the deferred
      catalog block in the system prompt (see
      ``functions._runtime.deferred_catalog_text``, which joins names
      only). Pricing it by its full description over-counted the catalog
      by an order of magnitude — 4665 estimated against 183 real.
    * A **resident** tool ships as a provider tools-array entry:
      ``description`` AND ``parameters`` schema, plus a few tokens of
      name/wrapper. Counting only ``parameters`` under-counted the
      resident toolset the other way — 588 estimated against 4567 real —
      and the two errors cancelled just enough to make the total look
      plausible.
    """
    import json as _json

    deferred = bool(getattr(t, "_defer", False))
    if deferred:
        # One name per line in the catalog block — no description, and no
        # per-message overhead: the whole catalog is a few lines inside ONE
        # system-prompt block, not a message each. ``+1`` is the newline
        # that separates this name from the next.
        name = getattr(t, "name", "") or ""
        return _text_tokens(name) + 1, True
    # Tool objects spell the JSON schema differently depending on where
    # they came from: ``parameters`` (the OpenAI-shaped one, which every
    # registered AgentTool actually uses) / ``schema`` / ``spec``.
    #
    # ``parameters`` is consulted FIRST and the alternates only when it is
    # absent: on a pydantic AgentTool ``t.schema`` resolves to
    # ``BaseModel.schema`` — the deprecated bound METHOD, not data. Trying
    # it first meant every tool priced a 78-char repr of a method object
    # instead of its real schema.
    schema = next(
        (s for s in (getattr(t, "parameters", None),
                     getattr(t, "spec", None),
                     getattr(t, "schema", None))
         if isinstance(s, dict)),
        None,
    )
    desc = getattr(t, "description", "") or ""
    name = getattr(t, "name", "") or ""
    if schema is None and not desc:
        return 20, False  # unknown tool — guess
    try:
        schema_text = _json.dumps(schema, default=str, ensure_ascii=False)
    except Exception:
        schema_text = str(schema)
    entry = _json.dumps(
        {"name": name, "description": desc}, ensure_ascii=False,
    ) + schema_text
    return estimate_message_tokens({"role": "tool", "content": entry}) + 5, False


def estimate_tools_breakdown(tools: list[Any]) -> list[dict]:
    """Per-tool token 明细。口径 = _estimate_one_tool（与
    BudgetAllocator._estimate_tools 共用同一条计价规则）。
    返回 [{"name","tokens","deferred"}, ...]，顺序同入参。"""
    out: list[dict] = []
    for t in tools:
        tokens, deferred = _estimate_one_tool(t)
        out.append({
            "name": getattr(t, "name", "") or "",
            "tokens": tokens,
            "deferred": deferred,
        })
    return out


default_allocator = BudgetAllocator()

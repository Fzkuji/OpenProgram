"""rule_microcompact — idle-gap tool-result clearing for snapshot chain.

Ported from openprogram/context/microcompact.py (Claude Code's microcompact
design) to the Snapshot Chain rule pipeline.

WHY the 60-minute gap:
  Provider prompt caches have a 1-hour TTL. Once we cross that gap, the
  whole prefix is rewritten on the next call at full price anyway — so
  clearing old tool results at that moment costs nothing extra. While a
  task is actively running (turns seconds/minutes apart) the gap is
  never hit, so live tasks are never trimmed.

WHY keep_recent=5:
  The model usually only needs the most recent few tool results to reason
  about the current step; older ones can be re-fetched if needed.

WHY large_result_floor=200:
  CLEARED_PLACEHOLDER itself costs ~8 tokens. Clearing a 50-token result
  saves ~42 tokens — not worth the churn (and not worth losing the
  content). Only clear results large enough that the trade pays.
"""
from __future__ import annotations

from openprogram.context.snapshot.types import ContextItem, CLEARED_PLACEHOLDER
from openprogram.context.rules._base import RuleContext


GAP_THRESHOLD_SECONDS = 3600.0   # 60 min — see module docstring.
KEEP_RECENT = 5
LARGE_RESULT_TOKEN_FLOOR = 200

# Cost of CLEARED_PLACEHOLDER itself, hard-coded to avoid pulling in a
# tokenizer dependency for one constant string.
_PLACEHOLDER_TOKENS = 8


def _last_assistant_created_at(
    items: list[ContextItem],
    ctx: RuleContext,
) -> float | None:
    """Return the DAG ``created_at`` of the most recent assistant item.

    ContextItem itself doesn't carry timestamps — they live on the DAG
    node. We resolve them lazily via ``ctx.fetch_node``. If anything's
    missing (no fetcher, node gone, no timestamp) we return None and the
    caller skips the rule.
    """
    if ctx.fetch_node is None:
        return None
    for item in reversed(items):
        if item.role != "assistant":
            continue
        node = ctx.fetch_node(item.source_node_id)
        if not node:
            return None
        ts = node.get("created_at")
        try:
            return float(ts) if ts is not None else None
        except (TypeError, ValueError):
            return None
    return None


def rule_microcompact(items: list[ContextItem], ctx: RuleContext) -> None:
    """Clear old tool results once the conversation has been idle >60min.

    No-op while a task is actively running. When triggered, the most
    recent ``KEEP_RECENT`` unlocked full tool items are kept verbatim;
    older ones (above the size floor) are replaced with a fixed
    placeholder and locked.
    """
    last_ts = _last_assistant_created_at(items, ctx)
    if last_ts is None:
        return
    if (ctx.now - last_ts) < GAP_THRESHOLD_SECONDS:
        return

    # Eligible tool items, in document order. Already-aged / locked items
    # are skipped — the aging rule has its own policy and we respect its
    # decision.
    eligible_idx = [
        i for i, it in enumerate(items)
        if it.role == "tool" and it.state == "full" and not it.locked
    ]
    if len(eligible_idx) <= KEEP_RECENT:
        return

    to_clear = eligible_idx[:-KEEP_RECENT]
    for i in to_clear:
        it = items[i]
        if it.tokens < LARGE_RESULT_TOKEN_FLOOR:
            # Clearing this won't free meaningful tokens — leave it as-is
            # so the model still sees the (small) original content.
            continue
        it.state = "cleared"
        it.locked = True
        it.rendered = CLEARED_PLACEHOLDER
        it.tokens = _PLACEHOLDER_TOKENS
        it.state_set_at = ctx.snap_id
        it.reason = "idle_60min"

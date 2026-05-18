"""Microcompactor — idle-gap tool-result clearing (microcompact).

Stale tool outputs are the single biggest waste in long agent loops:
the agent reads a 50K-line file once, digests it, then later turns drag
that wall of text through the prompt for no reason. Microcompact swaps
the bulky content of an old tool_result for a short placeholder.

Trigger — idle gap (ported from Claude Code's microcompact):
microcompact fires *only* when the gap between now and the last
assistant message exceeds ``gap_threshold_seconds`` (default 3600 — one
hour). The rationale: a provider's prompt cache has expired by then, so
the whole prompt prefix gets rewritten on the next call anyway —
clearing old tool results at that point costs nothing extra. While a
task is actively running (turns seconds/minutes apart) the gap is never
hit, so microcompact never touches a live task's context.

Selection: when triggered, the most-recent ``keep_recent`` tool results
are kept verbatim; every older tool_result block (string content, at
least ``large_result_tokens`` big) is cleared to a placeholder.

Non-destructive: ``microcompact()`` returns a transformed *copy* of the
turn's message list. It does NOT touch the stored DAG nodes — the
original tool_result stays in the database intact. The effect is purely
"the model does not see this content in this turn's prompt"; the data
is still recoverable, and if the model needs it again it can re-run the
tool.

Attribution: the idle-gap trigger and keep-last-N selection are Claude
Code's microcompact design, reimplemented here for OpenProgram's
message/DAG model. An earlier revision used a different every-turn
three-gate scheme; it was replaced by this idle-gap version so that an
actively running task is never trimmed.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any

from openprogram.context.tokens import _text_tokens


# Defaults — overridable via constructor for testing / per-engine tuning.
GAP_THRESHOLD_SECONDS = 3600.0   # 60 min — only fire after this much idle.
KEEP_RECENT_RESULTS = 5          # most-recent tool results always kept.
LARGE_RESULT_TOKENS = 800        # below this, clearing nets ~nothing.

_REDACTED_TEMPLATE = "[Old tool result content cleared (was {n} tokens)]"


def _parse_extra(extra_raw: Any) -> dict | None:
    """Parse a message's ``extra`` field into a dict, or None."""
    if extra_raw is None:
        return None
    try:
        return (json.loads(extra_raw)
                if isinstance(extra_raw, str) else dict(extra_raw))
    except Exception:
        return None


class Microcompactor:
    """Idle-gap-triggered tool-result clearing."""

    def __init__(self, *,
                 gap_threshold_seconds: float = GAP_THRESHOLD_SECONDS,
                 keep_recent: int = KEEP_RECENT_RESULTS,
                 large_result_tokens: int = LARGE_RESULT_TOKENS):
        self.gap_threshold_seconds = gap_threshold_seconds
        # Floor at 1: clearing every result leaves the model with zero
        # working context, which is never sensible.
        self.keep_recent = max(1, keep_recent)
        self.large_result_tokens = large_result_tokens

    def microcompact(self, history: list[dict],
                     *,
                     now: float | None = None,
                     ) -> tuple[list[dict], int, int]:
        """Return ``(new_history, n_redacted, tokens_freed)``.

        A no-op unless the gap since the last assistant message exceeds
        ``gap_threshold_seconds``. Input is never mutated — callers get
        a fresh list with cleared messages replaced by new dicts.
        """
        if not history:
            return history, 0, 0
        now = now if now is not None else time.time()

        # --- Trigger gate: only fire after a long idle gap. ---
        last_assistant_ts = 0.0
        for m in reversed(history):
            if m.get("role") == "assistant":
                last_assistant_ts = float(m.get("timestamp") or 0.0)
                break
        if (last_assistant_ts <= 0
                or (now - last_assistant_ts) < self.gap_threshold_seconds):
            return history, 0, 0

        # --- Locate every tool_result block; keep the last keep_recent. ---
        positions: list[tuple[int, int]] = []   # (msg_index, block_index)
        for mi, m in enumerate(history):
            extra = _parse_extra(m.get("extra"))
            if not extra:
                continue
            for bi, blk in enumerate(extra.get("blocks") or []):
                if (blk.get("type") or "") == "tool_result":
                    positions.append((mi, bi))
        if len(positions) <= self.keep_recent:
            return history, 0, 0

        to_clear: dict[int, list[int]] = defaultdict(list)
        for mi, bi in positions[:-self.keep_recent]:
            to_clear[mi].append(bi)

        # --- Redact the to-clear blocks. ---
        out = list(history)
        total_redacted = 0
        total_freed = 0
        for mi, block_indices in to_clear.items():
            orig = history[mi]
            extra = _parse_extra(orig.get("extra"))
            if not extra:
                continue
            blocks = list(extra.get("blocks") or [])
            changed = False
            for bi in block_indices:
                blk = blocks[bi]
                if blk.get("_redacted"):
                    continue
                content = blk.get("content") or ""
                # Structured (non-string) content is left alone — redacting
                # it risks dropping inline image references.
                if not isinstance(content, str):
                    continue
                est = _text_tokens(content)
                if est < self.large_result_tokens:
                    continue
                blocks[bi] = {
                    **blk,
                    "content": _REDACTED_TEMPLATE.format(n=est),
                    "_redacted": True,
                    "_orig_tokens": est,
                }
                total_redacted += 1
                total_freed += est
                changed = True
            if not changed:
                continue
            new_extra = {**extra, "blocks": blocks}
            raw = orig.get("extra")
            out[mi] = {
                **orig,
                "extra": (json.dumps(new_extra, default=str)
                          if isinstance(raw, str) else new_extra),
            }

        return out, total_redacted, total_freed


# Module-level default — engines compose this rather than instantiating
# their own unless they want different thresholds.
default_microcompactor = Microcompactor()

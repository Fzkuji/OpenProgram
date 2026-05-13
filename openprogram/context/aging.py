"""TurnAger — tool-result aging (microcompact).

Stale tool outputs are the single biggest waste in long agent loops:
the agent reads a 50K-line file once, digests it, then the next 20
turns drag that wall of text through prompt cache for no reason.

Three independent gates decide whether a tool_result is "stale":

1. **Turn distance**: at least ``keep_recent_turns`` (default 4)
   assistant turns must have passed since the message was created.
   Newer than that and the agent might still be acting on it.

2. **Wall-clock age** (Claude Code's trick): in addition to turn
   distance, the result must be older than ``keep_recent_seconds``.
   Useful when the agent is in tight tool-call loops — 4 turns in 10
   seconds isn't really "stale".

3. **Not referenced**: if the ``ReferenceTracker`` thinks a later
   message is still citing the result (file path, hash, snippet), we
   don't age it even if it's old. The model is clearly still working
   with it.

A result that fails any one of these gates is preserved. Only when ALL
three say "stale AND large enough to matter" do we redact.
"""
from __future__ import annotations

import json
import time
from typing import Any

from openprogram.context.tokens import _text_tokens
from openprogram.context.references import ReferenceTracker
from openprogram.context.types import ReferenceMap


# Defaults — overridable via constructor for testing / per-engine tuning.
KEEP_RECENT_TURNS = 4
KEEP_RECENT_SECONDS = 60.0          # 1 minute — don't age in tight loops
LARGE_RESULT_TOKENS = 800           # below this, redaction nets negative

_REDACTED_TEMPLATE = "[Old tool result content cleared (was {n} tokens)]"


class TurnAger:
    """Apply the three-gate aging rules over a session branch."""

    def __init__(self, *,
                 keep_recent_turns: int = KEEP_RECENT_TURNS,
                 keep_recent_seconds: float = KEEP_RECENT_SECONDS,
                 large_result_tokens: int = LARGE_RESULT_TOKENS,
                 protect_first_n: int = 2,
                 references: ReferenceTracker | None = None):
        self.keep_recent_turns = keep_recent_turns
        self.keep_recent_seconds = keep_recent_seconds
        self.large_result_tokens = large_result_tokens
        # Protect the first N messages so the original task description
        # never gets redacted away — even on hour-long sessions the
        # model should still see "build a CLI to do X" at the top.
        self.protect_first_n = protect_first_n
        self.references = references or ReferenceTracker()

    # ---- Main entry point ---------------------------------------------

    def age(self, history: list[dict],
            *,
            now: float | None = None,
            ref_map: ReferenceMap | None = None,
            ) -> tuple[list[dict], int, int]:
        """Return ``(new_history, n_redacted, tokens_freed)``.

        Input is NOT mutated — callers get a fresh list with aged
        messages replaced by new dicts.
        """
        if not history:
            return history, 0, 0

        now = now if now is not None else time.time()

        # Build reference map lazily — caller can pass one in to share
        # the result with other components on the same prepare() call.
        if ref_map is None:
            ref_map = self.references.build(history)

        # Find cut-off: messages BEFORE this index are eligible for aging.
        cut_idx = self._cut_index(history)
        if cut_idx <= self.protect_first_n:
            return history, 0, 0

        out: list[dict] = list(history)
        total_redacted = 0
        total_freed = 0

        for i in range(self.protect_first_n, cut_idx):
            m = history[i]
            # Don't age messages cited downstream.
            if self.references.is_referenced(ref_map, m.get("id")):
                continue
            extra = m.get("extra")
            if not extra:
                continue
            # Wall-clock age gate.
            ts = float(m.get("timestamp") or 0.0)
            if ts > 0 and (now - ts) < self.keep_recent_seconds:
                continue

            new_extra, n, freed = self._redact_extra(extra)
            if n == 0:
                continue
            out[i] = {**m, "extra": new_extra}
            total_redacted += n
            total_freed += freed

        return out, total_redacted, total_freed

    # ---- Internals -----------------------------------------------------

    def _cut_index(self, history: list[dict]) -> int:
        """The smallest index past which messages are 'fresh enough'
        to not age. Cut at the start of the (N+1)-th most-recent
        assistant turn, where N = keep_recent_turns.
        """
        assistant_count = sum(1 for m in history if m.get("role") == "assistant")
        to_skip = max(0, assistant_count - self.keep_recent_turns)
        if to_skip <= 0:
            return 0
        seen = 0
        for i, m in enumerate(history):
            if m.get("role") == "assistant":
                seen += 1
                if seen > to_skip:
                    return i
        return len(history)

    def _redact_extra(self, extra_raw: Any) -> tuple[Any, int, int]:
        """Redact large tool_result blocks inside one message's extra."""
        if extra_raw is None:
            return extra_raw, 0, 0
        try:
            extra = (json.loads(extra_raw)
                     if isinstance(extra_raw, str) else dict(extra_raw))
        except Exception:
            return extra_raw, 0, 0

        n_redacted = 0
        tokens_freed = 0
        new_blocks = []
        for blk in (extra.get("blocks") or []):
            if (blk.get("type") or "") != "tool_result":
                new_blocks.append(blk)
                continue
            if blk.get("_redacted"):
                # Already redacted on an earlier pass — leave alone.
                new_blocks.append(blk)
                continue
            content = blk.get("content") or ""
            if not isinstance(content, str):
                # Anthropic-style structured content — leave alone for now,
                # redacting structured blocks risks losing inline image refs.
                new_blocks.append(blk)
                continue
            est = _text_tokens(content)
            if est < self.large_result_tokens:
                new_blocks.append(blk)
                continue
            tokens_freed += est
            n_redacted += 1
            new_blocks.append({
                **blk,
                "content": _REDACTED_TEMPLATE.format(n=est),
                "_redacted": True,
                "_orig_tokens": est,
            })
        extra["blocks"] = new_blocks

        if isinstance(extra_raw, str):
            return json.dumps(extra, default=str), n_redacted, tokens_freed
        return extra, n_redacted, tokens_freed


# Module-level default — engines compose this rather than instantiating
# their own unless they want different thresholds.
default_ager = TurnAger()

"""Tool-result aging — Claude Code's "microcompact" trick.

Old tool results (big file reads, dumped HTML, bash outputs) keep
contributing to context budget long after the agent has moved past
them. The agent rarely re-reads the raw text once it has digested it
once. Replacing those bodies with a short stub ``[Old tool result
content cleared (was N tokens)]`` cuts context spend without changing
turn structure.

Rules:

* Only ``tool_result`` blocks get aged. User text and assistant
  prose are NEVER touched — the model needs that intact for
  coherence.
* The most recent ``KEEP_RECENT_TURNS`` turns are spared entirely
  (the agent is probably still acting on them).
* When a result exceeds ``LARGE_RESULT_TOKENS`` and is older than
  the keep window, we replace it. Smaller results pass through (the
  cost of redacting them outweighs the savings).
* The function is pure — it builds a new message list and never
  mutates the input. DB stays the source of truth.
"""
from __future__ import annotations

import json
from typing import Any

from openprogram.context.tokens import estimate_message_tokens, _text_tokens  # noqa: F401


# A "turn" is one user → assistant pair. We keep the freshest 4 turns
# fully intact (no aging) so recent tool outputs aren't suddenly empty
# while the agent is still reasoning over them.
KEEP_RECENT_TURNS = 4

# Tool results smaller than this aren't worth aging. The marker itself
# eats ~20 tokens, so anything under 200 tokens nets negative savings.
LARGE_RESULT_TOKENS = 800

# Tools whose outputs almost always carry stale bulk data and can be
# aggressively aged. Other tools' results age normally too — this list
# just signals "definitely safe to drop fully".
_BULKY_TOOLS = frozenset({
    "read", "read_file", "browser", "playwright_browser",
    "agent_browser", "web_fetch", "fetch", "bash", "execute_code",
    "grep", "glob", "list", "html", "extract",
})

_REDACTED_STUB_TEMPLATE = "[Old tool result content cleared (was {n} tokens)]"


def _count_assistant_turns(history: list[dict]) -> int:
    return sum(1 for m in history if m.get("role") == "assistant")


def _redact_extra_blocks(extra_raw: Any, age_threshold: int) -> tuple[Any, int, int]:
    """Walk a message's ``extra`` JSON, redact large tool_result bodies.

    Returns ``(new_extra, n_redacted, tokens_freed)``. ``new_extra`` is
    a JSON string when input was a string, dict otherwise (matching
    SessionDB's serialization).
    """
    if extra_raw is None:
        return extra_raw, 0, 0
    try:
        extra = (json.loads(extra_raw)
                 if isinstance(extra_raw, str) else dict(extra_raw))
    except Exception:
        return extra_raw, 0, 0

    n_redacted = 0
    tokens_freed = 0

    blocks = extra.get("blocks") or []
    new_blocks = []
    for blk in blocks:
        if (blk.get("type") or "") == "tool_result":
            content = blk.get("content") or ""
            est = _text_tokens(str(content)) if isinstance(content, str) else 0
            if est >= age_threshold:
                tokens_freed += est
                n_redacted += 1
                new_blocks.append({
                    **blk,
                    "content": _REDACTED_STUB_TEMPLATE.format(n=est),
                    "_redacted": True,
                    "_orig_tokens": est,
                })
                continue
        new_blocks.append(blk)
    extra["blocks"] = new_blocks

    if isinstance(extra_raw, str):
        return json.dumps(extra, default=str), n_redacted, tokens_freed
    return extra, n_redacted, tokens_freed


def age_tool_results(
    history: list[dict],
    *,
    keep_recent_turns: int = KEEP_RECENT_TURNS,
    threshold_tokens: int = LARGE_RESULT_TOKENS,
) -> tuple[list[dict], int, int]:
    """Apply tool-result aging to a message list.

    Returns ``(new_history, n_redacted, tokens_freed)``. The input
    list is NOT mutated — callers get back a shallow-copied list with
    aged entries swapped for new dicts. Messages that didn't need
    aging are shared (no copy).
    """
    if not history:
        return history, 0, 0

    # Find the cut-off index: messages BEFORE this index get aged,
    # messages AT/AFTER stay intact.
    assistant_count_to_skip = max(0, _count_assistant_turns(history)
                                  - keep_recent_turns)
    if assistant_count_to_skip <= 0:
        # Whole history fits in the keep window. Nothing to age.
        return history, 0, 0

    cut_idx = 0
    seen = 0
    for i, m in enumerate(history):
        if m.get("role") == "assistant":
            seen += 1
            if seen > assistant_count_to_skip:
                cut_idx = i
                break

    out: list[dict] = list(history[:cut_idx])
    total_redacted = 0
    total_freed = 0
    for m in history[:cut_idx]:
        idx = len(out) - len(history[:cut_idx]) + history[:cut_idx].index(m)
        extra = m.get("extra")
        if not extra:
            continue
        new_extra, n, freed = _redact_extra_blocks(extra, threshold_tokens)
        if n == 0:
            continue
        # Replace the message in out[] (we appended originals above; now
        # swap the specific index).
        actual_idx = history.index(m)
        out[actual_idx] = {**m, "extra": new_extra}
        total_redacted += n
        total_freed += freed

    out.extend(history[cut_idx:])  # keep recent turns unchanged
    return out, total_redacted, total_freed

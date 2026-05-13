"""LLM-driven history summarisation.

This is the heavyweight compaction step: when even tool-result aging
can't fit history under budget, we ask the model to summarise the
older prefix into a few hundred tokens and replace it with a synthetic
"[Previous conversation summary]" message.

Differences from the legacy ``agent/compaction/compaction.py``:

* Incremental summaries: subsequent compactions pass the previous
  summary as ``previous_summary`` so the second pass refines rather
  than re-summarising the entire branch from scratch. (The old
  ``trigger_compaction`` hard-coded ``previous_summary=None``.)
* Picks the cut point by **walking from the newest end** with a token
  budget rather than from the oldest — matches Claude Code's
  ``find_cut_point`` semantics (keep the last N tokens of work
  verbatim, summarise everything before).
* Doesn't mutate SessionDB itself; returns a ``Summary`` record and
  lets the caller (engine.compact) decide whether to persist.

The actual SUMMARIZATION_PROMPT is shared with the legacy module to
keep behavior identical.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openprogram.context.tokens import (
    estimate_message_tokens, estimate_history_tokens,
)


@dataclass
class Summary:
    """One LLM-generated summary.

    ``summary_text`` is the model's output; ``cut_idx`` is the index
    in the original history where the summary's coverage ends — every
    message at or after that index is kept verbatim.
    """
    summary_text: str
    cut_idx: int
    summarised_count: int          # messages folded into the summary
    summarised_tokens: int         # token weight of the folded prefix
    previous_summary_used: bool    # whether we built on a prior summary


# Default tail-preservation budget (tokens). We aim to keep the last
# ~20K tokens of conversation verbatim so the model still has fresh
# context to act on after the summary takes over the prefix.
DEFAULT_KEEP_RECENT_TOKENS = 20_000


def _find_cut_index(messages: list[dict], keep_recent_tokens: int) -> int:
    """Return the smallest index such that messages[idx:] fits under
    ``keep_recent_tokens``. Walks backward summing token estimates.

    Always returns at least 1 (don't summarise the only message); also
    snaps to a user-turn boundary so the summary doesn't cut mid-tool-
    call.
    """
    if len(messages) < 4:
        return 0
    running = 0
    cut = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        running += estimate_message_tokens(messages[i])
        if running > keep_recent_tokens:
            cut = i + 1
            break
        cut = i
    # Snap forward to the next user-turn boundary if we landed
    # mid-tool. Avoids leaving a dangling assistant turn at the head
    # of the kept tail, which confuses the model.
    while cut < len(messages) and messages[cut].get("role") != "user":
        cut += 1
    # Don't summarise the entire history away — keep at least one turn
    # verbatim.
    if cut >= len(messages):
        cut = max(1, len(messages) - 2)
    return cut


_SYSTEM_PROMPT = (
    "You are a summariser preserving conversation context across a "
    "model context-window boundary. Output a faithful summary of the "
    "user's intent, every decision the assistant made, and the final "
    "state of work in progress. Be specific: include file paths, ids, "
    "command names, error messages. Do NOT moralise or add commentary; "
    "do NOT include preamble like 'Here is a summary'. Just the summary."
)

_FRESH_PROMPT = (
    "Summarise the conversation above. The user is about to send the "
    "next message and you will continue the work — the summary REPLACES "
    "the older transcript in your context window. Cover, in this order: "
    "(1) the user's overall goal, (2) every concrete decision / "
    "instruction / preference the user has expressed, (3) what the "
    "assistant has done so far (files written / commands run / data "
    "found), (4) any outstanding tasks or follow-ups."
)

_UPDATE_PROMPT = (
    "Incorporate the NEW messages above into the EXISTING "
    "summary inside <previous-summary> below. Output a single revised "
    "summary that supersedes the previous one. Treat the new messages "
    "as authoritative — if they contradict the prior summary, the new "
    "info wins. Same structure as the prior summary."
)


async def generate_summary(
    *,
    messages: list[Any],          # messages to summarise (the prefix)
    model: Any,
    previous_summary: str | None = None,
    max_tokens: int = 4000,
) -> str:
    """Ask ``model`` to summarise ``messages``.

    Falls back to a deterministic stub if the LLM call fails, so the
    caller's compaction pass never crashes the turn.
    """
    from openprogram.providers import complete_simple
    from openprogram.providers.types import (
        Context, SimpleStreamOptions, TextContent, UserMessage,
    )

    conv = _serialize(messages)
    prompt = f"<conversation>\n{conv}\n</conversation>\n\n"
    if previous_summary:
        prompt += f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
        prompt += _UPDATE_PROMPT
    else:
        prompt += _FRESH_PROMPT

    opts_kwargs: dict[str, Any] = {"max_tokens": max_tokens}
    if getattr(model, "reasoning", False):
        opts_kwargs["reasoning"] = "high"
    opts = SimpleStreamOptions(**opts_kwargs)

    try:
        ctx = Context(
            system_prompt=_SYSTEM_PROMPT,
            messages=[UserMessage(
                role="user",
                content=[TextContent(type="text", text=prompt)],
                timestamp=0,
            )],
        )
        response = await complete_simple(model, ctx, opts)
        if getattr(response, "stop_reason", None) == "error":
            return _fallback_stub(messages)
        return " ".join(
            b.text for b in response.content if isinstance(b, TextContent)
        )
    except Exception:
        return _fallback_stub(messages)


def _serialize(messages: list[Any]) -> str:
    """Render messages as plain "Role: content" so the summariser sees
    a transcript it can reason over. Order preserved."""
    out: list[str] = []
    for m in messages:
        if isinstance(m, dict):
            role = (m.get("role") or "user").capitalize()
            text = (m.get("content") or "").strip()
        else:
            role = (getattr(m, "role", "user") or "user").capitalize()
            content = getattr(m, "content", None)
            if isinstance(content, list):
                parts = []
                for blk in content:
                    t = getattr(blk, "text", None) or getattr(blk, "content", None)
                    if t:
                        parts.append(str(t))
                text = "\n".join(parts).strip()
            else:
                text = str(content or "").strip()
        if text:
            out.append(f"{role}: {text}")
    return "\n\n".join(out)


def _fallback_stub(messages: list[Any]) -> str:
    """Deterministic stub used when the LLM summariser fails."""
    n = len(messages)
    tokens = estimate_history_tokens(messages)
    return (
        f"[Context summary unavailable — {n} earlier message(s), "
        f"≈{tokens} tokens, elided. The model could not generate a "
        f"summary on this attempt.]"
    )


async def summarise_prefix(
    *,
    messages: list[dict],
    model: Any,
    keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS,
    previous_summary: str | None = None,
) -> Summary:
    """End-to-end: pick a cut point, summarise the prefix, return result.

    ``messages`` should be the full active branch (dicts). The caller
    decides what to do with the result (typically: persist the
    summary as a new message in SessionDB and re-parent the kept tail).
    """
    cut = _find_cut_index(messages, keep_recent_tokens)
    if cut <= 0:
        return Summary(
            summary_text="",
            cut_idx=0,
            summarised_count=0,
            summarised_tokens=0,
            previous_summary_used=False,
        )
    prefix = messages[:cut]
    text = await generate_summary(
        messages=prefix,
        model=model,
        previous_summary=previous_summary,
    )
    return Summary(
        summary_text=text,
        cut_idx=cut,
        summarised_count=cut,
        summarised_tokens=estimate_history_tokens(prefix),
        previous_summary_used=bool(previous_summary),
    )

"""Cross-turn tool memory: attach tool rows, age old ones, hard-cap
single-result size.

Public entry point: ``prepare_history(history, session_id)`` returns
the same history list, in place, with each assistant carrying its
own ``tool_calls`` (full for the tail window, aged stubs for older
turns; single oversize results middle-truncated regardless).

See ``docs/design/cross-turn-tool-context.md`` for the design.
"""
from __future__ import annotations

from .attach import enrich_with_tools
from .policy import (
    TAIL_TURNS,
    MAX_TOOL_RESULT_CHARS,
    PRUNE_PROTECTED_TOOLS,
)
from .summarize import summarize_tool_call
from .truncate import middle_truncate


def _age_tool_calls(calls: list[dict], *, full_fidelity: bool) -> list[dict]:
    """Walk one assistant's tool_calls, returning a new list.

    ``full_fidelity=True`` (tail window): keep everything, only the
    single-result hard cap fires.
    ``full_fidelity=False`` (older turn): every non-protected tool
    collapses into a one-line stub.
    """
    out: list[dict] = []
    for c in calls:
        name = c.get("tool") or ""
        result = c.get("result") or ""
        is_error = bool(c.get("is_error"))
        if not full_fidelity and name not in PRUNE_PROTECTED_TOOLS:
            stub = summarize_tool_call(name, c.get("input"), result, is_error)
            out.append({**c, "result": stub, "_aged": True})
            continue
        # Tail window OR protected tool: keep, but hard-cap result.
        if isinstance(result, str) and len(result) > MAX_TOOL_RESULT_CHARS:
            result = middle_truncate(result, MAX_TOOL_RESULT_CHARS)
        out.append({**c, "result": result})
    return out


def prepare_history(history: list[dict], session_id: str) -> list[dict]:
    """Attach + age in one pass. Mutates entries in place.

    The aging boundary is the LAST ``TAIL_TURNS`` assistants:
    everything from that index onward keeps full fidelity; older
    assistants get aged stubs.
    """
    enrich_with_tools(history, session_id)
    # Index of every assistant in the history list.
    asst_indices = [
        i for i, m in enumerate(history)
        if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    if not asst_indices:
        return history
    tail_cutoff = (
        asst_indices[-TAIL_TURNS] if len(asst_indices) > TAIL_TURNS
        else asst_indices[0]
    )
    for i in asst_indices:
        full = i >= tail_cutoff
        history[i]["tool_calls"] = _age_tool_calls(
            history[i]["tool_calls"], full_fidelity=full,
        )
    return history


__all__ = [
    "prepare_history",
    "enrich_with_tools",
    "TAIL_TURNS",
    "MAX_TOOL_RESULT_CHARS",
    "PRUNE_PROTECTED_TOOLS",
]

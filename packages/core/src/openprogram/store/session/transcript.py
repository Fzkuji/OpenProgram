"""Render a session branch as plain text an LLM can read.

``get_branch`` returns the conversational chain (user / assistant nodes
linked by ``predecessor``). Tool and function-call nodes are *not* on
that chain — they hang off the assistant turn that issued them via
``caller`` (dag/overview.md; the same edge ``graph_builder`` follows).
This module joins the two: walk the branch, and under each turn print
the calls whose ``caller`` points at it.

Written for reading, not debugging — ``scripts/dag_dump.py`` covers the
coordinate/lane view. The output feeds the ``distill`` skill, which
turns a past session into a reusable SKILL.md or agentic function.
"""
from __future__ import annotations

import json
from typing import Any, Optional

# Per-field caps. A transcript is prompt material: one runaway tool
# result (a 2 MB file read) must not evict the reasoning around it.
MAX_TEXT_CHARS = 2_000
MAX_ARGS_CHARS = 400
MAX_RESULT_CHARS = 600
MAX_TOTAL_CHARS = 60_000


def _clip(text: Any, limit: int) -> str:
    s = "" if text is None else str(text)
    s = s.strip()
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n… [+{len(s) - limit} chars truncated]"


def _tool_use(msg: dict[str, Any]) -> dict[str, Any]:
    """The ``tool_use`` blob a code node stores in ``extra``.

    ``_node_to_msg`` writes ``extra`` as a JSON string, but callers that
    build msg dicts by hand leave it a dict — accept both.
    """
    extra = msg.get("extra")
    if isinstance(extra, str) and extra:
        try:
            extra = json.loads(extra)
        except (ValueError, TypeError):
            return {}
    if not isinstance(extra, dict):
        return {}
    tu = extra.get("tool_use")
    return tu if isinstance(tu, dict) else {}


def _format_args(msg: dict[str, Any]) -> str:
    args = _tool_use(msg).get("arguments")
    if args in (None, "", {}):
        return ""
    if not isinstance(args, str):
        try:
            args = json.dumps(args, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            args = str(args)
    return _clip(args, MAX_ARGS_CHARS)


def _call_line(msg: dict[str, Any]) -> list[str]:
    name = msg.get("function") or _tool_use(msg).get("name") or "(unnamed call)"
    status = "FAILED" if msg.get("is_error") else "ok"
    lines = [f"  [call] {name} -> {status}"]
    args = _format_args(msg)
    if args:
        lines.append(f"    args: {args}")
    result = _clip(msg.get("content"), MAX_RESULT_CHARS)
    if result:
        indented = "\n".join(f"      {ln}" for ln in result.splitlines())
        lines.append("    result:")
        lines.append(indented)
    return lines


def _turn_header(msg: dict[str, Any], index: int) -> str:
    role = msg.get("role") or "unknown"
    # A summary node stands in for a compacted range (dag/overview.md
    # §8) — say so, so the reader knows detail was dropped there rather
    # than never existing.
    if (msg.get("function") or "") == "context/summary":
        return f"--- [{index}] compaction summary (stands in for earlier turns) ---"
    if msg.get("source") == "agent_spawn" or msg.get("spawn_branch_root"):
        return f"--- [{index}] {role} (spawned sub-branch root) ---"
    return f"--- [{index}] {role} ---"


def render_session_transcript(
    session_id: str,
    head_id: Optional[str] = None,
    start_turn: int = 0,
    end_turn: int = 0,
    include_function_calls: bool = True,
    max_chars: int = MAX_TOTAL_CHARS,
    store: Any = None,
) -> str:
    """Serialize one branch of a session into LLM-readable plain text.

    Args:
        session_id: session to read.
        head_id: branch tip to walk back from. Defaults to the session's
            active head; pass a tip from ``list_branches`` to read a
            different branch.
        start_turn: first turn to include, as the 1-based ``[N]`` number
            shown in transcript headers (inclusive). 0 means from the
            first turn; negative counts from the end (``-10`` = last 10
            turns).
        end_turn: last turn to include (inclusive). 0 means through the
            last turn; negative counts from the end (``-1`` = last turn).
        include_function_calls: print the tool / function calls made
            during each turn. Turn this off for a conversation-only view.
        max_chars: overall budget. The transcript is cut at the last
            whole turn that fits, with a note naming what was dropped.
        store: SessionStore override, for tests. Defaults to the
            process-wide store.

    Returns:
        The transcript, or a one-line ``[transcript] …`` notice when the
        session is missing or empty.
    """
    if store is None:
        from openprogram.agent.session_db import default_db
        store = default_db()

    branch = store.get_branch(session_id, head_id)
    if not branch:
        return f"[transcript] session {session_id} has no messages on this branch."

    # Resolve the 1-based closed turn range; negatives count from the end.
    total = len(branch)
    start = int(start_turn) or 1
    if start < 0:
        start = total + start + 1
    start = max(1, start)
    end = int(end_turn) or total
    if end < 0:
        end = total + end + 1
    end = min(total, end)
    if start > end:
        return f"[transcript] range selects no turns (session has {total} turns)"
    turns = list(enumerate(branch, 1))[start - 1:end]

    # Calls hang off their caller turn, not off the branch chain, so
    # they need the raw node list. One pass, grouped by caller.
    calls_by_caller: dict[str, list[dict[str, Any]]] = {}
    if include_function_calls:
        for msg in store.get_messages(session_id):
            if msg.get("role") != "tool":
                continue
            caller = msg.get("caller") or ""
            if caller:
                calls_by_caller.setdefault(caller, []).append(msg)

    head = head_id or (branch[-1].get("id") or "")
    if (start, end) != (1, total):
        range_note = f"branch head: {head} · turns {start}-{end} of {total}"
    else:
        range_note = f"branch head: {head} · {total} turns"
    lines = [
        f"# Session transcript: {session_id}",
        range_note,
        "",
    ]
    body: list[str] = []
    used = sum(len(ln) + 1 for ln in lines)
    dropped_at = 0

    for i, msg in turns:
        turn = [_turn_header(msg, i)]
        text = _clip(msg.get("content"), MAX_TEXT_CHARS)
        if text:
            turn.append(text)
        for call in calls_by_caller.get(msg.get("id") or "", []):
            turn.extend(_call_line(call))
        turn.append("")

        size = sum(len(ln) + 1 for ln in turn)
        if used + size > max_chars and body:
            dropped_at = i
            break
        body.extend(turn)
        used += size

    lines.extend(body)
    if dropped_at:
        lines.append(
            f"[transcript truncated: turns {dropped_at}-{end} omitted to stay "
            f"under {max_chars} chars — re-read with start_turn={dropped_at} "
            "to continue]"
        )
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["render_session_transcript"]

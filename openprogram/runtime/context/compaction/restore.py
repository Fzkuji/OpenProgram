"""The working state a compaction writes after its summary.

A summary retells the conversation; what the model works from next is
stated exactly, once, when the summary is written, and stays on the
summary node so every later request sends the same bytes:

- the plan the user approved last on this chain (Claude Code's plan
  attachment), cut to ``PLAN_TOKENS`` with a note saying so;
- the todo board, whole (Kimi Code, Hermes);
- the background jobs still running, one line each (Claude Code);
- the paths of files read and changed, never their contents, added to the
  previous summary's lists (Pi's cumulative file tracking).

Skill bodies and file contents are not restored: they are long and the
model can read them again.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Optional

_log = logging.getLogger(__name__)

#: The whole block's budget, in tokens.
RESTORED_TOKENS = 10_000
#: The plan's share of it.
PLAN_TOKENS = 6_000
#: Paths kept per list.
MAX_PATHS = 30

READ_TOOLS = frozenset({"read"})
WRITE_TOOLS = frozenset({"write", "edit"})
#: Metadata key of the path lists on a summary node.
RESTORED_FILES = "restored_files"


def _tokens(text: str) -> int:
    from openprogram.runtime.context.tokens import _text_tokens
    return _text_tokens(text)


def _cut(text: str, budget: int) -> tuple[str, int]:
    """``text`` within ``budget`` tokens, and how many characters were left out."""
    if _tokens(text) <= budget:
        return text, 0
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _tokens(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo], len(text) - lo


def _chain_calls(graph: Any, head_id: Optional[str]) -> list:
    """The tool calls on the chain ending at ``head_id``, oldest first,
    without the ones a rewind retracted."""
    if not head_id or head_id not in graph.nodes:
        return []
    from openprogram.runtime.context.nodes import render_path
    gone = set(getattr(graph, "retracted", ()) or ())
    return [graph.nodes[nid] for nid in render_path(graph, head_id)
            if nid not in gone and graph.nodes[nid].is_code()]


def _args(call) -> dict:
    """A tool call's arguments. A stored call keeps them as the JSON text
    the model sent; one built in memory may hold the dict itself."""
    raw = call.input
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def approved_plan(calls: Iterable) -> str:
    """The plan of the last approved ``exit_plan_mode`` call; empty when
    plan mode was entered again after it, or nothing was approved."""
    plan = ""
    for call in calls:
        if call.name == "enter_plan_mode":
            plan = ""
        elif call.name == "exit_plan_mode" and str(call.output or "").startswith("Plan approved"):
            plan = str(_args(call).get("plan") or "")
    return plan


def _path_of(call) -> str:
    args = _args(call)
    return str(args.get("file_path") or args.get("path") or "").strip()


def file_lists(calls: Iterable, covers: Iterable[str], previous: Optional[dict]) -> dict:
    """Paths read and changed in the covered turns, after the previous
    summary's own lists; each list keeps its ``MAX_PATHS`` latest."""
    covered = set(covers)
    read = list((previous or {}).get("read") or [])
    modified = list((previous or {}).get("modified") or [])
    for call in calls:
        owner = call.caller or call.id
        if owner not in covered and call.id not in covered:
            continue
        path = _path_of(call)
        if not path:
            continue
        target = modified if call.name in WRITE_TOOLS else read if call.name in READ_TOOLS else None
        if target is None:
            continue
        if path in target:
            target.remove(path)
        target.append(path)
    return {"read": read[-MAX_PATHS:], "modified": modified[-MAX_PATHS:]}


def _previous_files(graph: Any, covers: Iterable[str]) -> Optional[dict]:
    """The path lists of the summary this one absorbs: the newest summary
    whose turns this one covers too."""
    from openprogram.runtime.context.nodes import summary_covers_ids
    covered = set(covers)
    best = None
    for node in graph.nodes.values():
        ids = summary_covers_ids(node)
        if ids and set(ids) <= covered and isinstance((node.metadata or {}).get(RESTORED_FILES), dict):
            if best is None or node.seq > best.seq:
                best = node
    return (best.metadata or {}).get(RESTORED_FILES) if best is not None else None


def _todo_lines(session_id: str) -> list[str]:
    from openprogram.programs.tools.planning.todo import shared as todo
    lines = []
    for item in todo.load(session_id):
        if not isinstance(item, dict):
            continue
        text = str(item.get("subject") or item.get("content") or "").strip()
        if text:
            lines.append(f"- [{item.get('status') or 'pending'}] {item.get('id') or ''} {text}".replace("  ", " "))
    return lines


def _job_lines(session_id: str) -> list[str]:
    from openprogram.state import execution_facts
    return [f"- {job_id} {name}: {status}".replace("  ", " ")
            for job_id, name, status in execution_facts.unfinished_jobs(session_id)]


def _safely(read, session_id: str) -> list[str]:
    try:
        return read(session_id)
    except Exception:
        _log.debug("restored state part unavailable for %s", session_id, exc_info=True)
        return []


def restored(session_id: str, graph: Any, head_id: Optional[str],
             covers: Iterable[str]) -> tuple[str, dict]:
    """The block written after the summary, and the path lists stored with it."""
    covers = list(covers)
    calls = _chain_calls(graph, head_id)
    files = file_lists(calls, covers, _previous_files(graph, covers))
    parts: list[str] = []
    plan = approved_plan(calls)
    if plan.strip():
        kept, left = _cut(plan.strip(), PLAN_TOKENS)
        if left:
            kept += f"\n[Plan cut here: {left} more characters are in the approved plan.]"
        parts.append("## Approved plan\n" + kept)
    todos = _safely(_todo_lines, session_id)
    if todos:
        parts.append("## Todo list\n" + "\n".join(todos))
    jobs = _safely(_job_lines, session_id)
    if jobs:
        parts.append("## Background jobs still running\n" + "\n".join(jobs))
    if files["read"]:
        parts.append("## Files read\n" + "\n".join(f"- {p}" for p in files["read"]))
    if files["modified"]:
        parts.append("## Files changed\n" + "\n".join(f"- {p}" for p in files["modified"]))
    if not parts:
        return "", files
    text = ("<restored-state>\nThe state below is current as of this summary.\n\n"
            + "\n\n".join(parts) + "\n</restored-state>")
    text, left = _cut(text, RESTORED_TOKENS)
    if left:
        text += f"\n[Restored state cut here: {left} more characters.]\n</restored-state>"
    return text, files


def restored_state(session_id: str, graph: Any, head_id: Optional[str],
                   covers: Iterable[str]) -> str:
    """The block alone (``restored``)."""
    return restored(session_id, graph, head_id, covers)[0]


__all__ = ["restored", "restored_state", "approved_plan", "file_lists",
           "RESTORED_TOKENS", "PLAN_TOKENS", "MAX_PATHS", "RESTORED_FILES"]

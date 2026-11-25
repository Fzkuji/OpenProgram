"""Which harness thread a turn continues, and the turns that thread has not seen.

A harness thread holds only the turns it ran. When a conversation switches
engine (OpenProgram → Codex, Codex → OpenProgram → Codex) or branches, the
thread is told what it missed: a budgeted transcript of those turns, newest
kept (T3 Code's "portable handoff"). Each branch keeps its own thread and
the id of the last message that thread saw, in the conversation's
``harness_threads``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

#: The handoff transcript's size.
HANDOFF_BUDGET_CHARS = 24_000
#: One tool call's line in the transcript.
_TOOL_LINE_CHARS = 160
_SESSION_FIELD = "harness_threads"

# The turns running per agent session, which the app sync waits for (kept in state, which it reads).
from openprogram.state.store.session.external_import.engine_turns import (  # noqa: E402,F401
    enter_running, leave_running, running,
)


@dataclass
class ThreadPlan:
    """``resume`` the branch's thread, ``fork`` another branch's, or ``start`` a new one."""

    action: str
    thread_id: Optional[str] = None
    missed: list[dict] = field(default_factory=list)


def branch_key(req: Any) -> str:
    return str(getattr(req, "branch_id", None) or "main")


def _ids(history: list[dict]) -> list[str]:
    return [str(message.get("id") or "") for message in history]


def _after(history: list[dict], seen: str) -> list[dict]:
    """The messages after ``seen``, without ``seen``'s own tool nodes."""
    ids = _ids(history)
    index = ids.index(seen)
    return [message for message in history[index + 1:]
            if not str(message.get("id") or "").startswith(seen)]


def plan_thread(records: dict, key: str, history: list[dict]) -> ThreadPlan:
    """How a turn on branch ``key`` with ``history`` (before its message) reaches its thread."""
    ids = set(_ids(history))
    own = records.get(key) if isinstance(records, dict) else None
    taken = _taken(records)
    if isinstance(own, dict) and own.get("thread_id") and not (own.get("shared") and own["thread_id"] in taken):
        seen = str(own.get("seen") or "")
        if seen and seen in ids:
            return ThreadPlan("resume", own["thread_id"], _after(history, seen))
        if not seen and not history:
            return ThreadPlan("resume", own["thread_id"])
        # The branch went back before what the thread saw (an edit or rewind).
        return ThreadPlan("start", missed=list(history))
    # A conversation synced from an app: its thread, when nothing was said
    # here since. Once an engine went on in that thread on some branch, the
    # record is no longer shared (``write_record``) and another branch forks it.
    for record in (records or {}).values():
        if isinstance(record, dict) and record.get("thread_id") in taken:
            continue
        if isinstance(record, dict) and record.get("shared") and record.get("thread_id"):
            seen = str(record.get("seen") or "")
            if seen and seen in ids:
                return ThreadPlan("resume", record["thread_id"], _after(history, seen))
    # A new branch: the thread of the branch it came from, when it forks at
    # that thread's latest turn.
    order = _ids(history)
    best: Optional[tuple[int, dict]] = None
    for record in (records or {}).values():
        if not isinstance(record, dict) or not record.get("thread_id"):
            continue
        if record.get("shared") and record["thread_id"] in taken:
            continue  # it holds another branch's turns now: starting over keeps them out
        seen = str(record.get("seen") or "")
        if seen in ids:
            position = order.index(seen)
            if best is None or position > best[0]:
                best = (position, record)
    if best is not None and not _after(history, str(best[1]["seen"])):
        return ThreadPlan("fork", best[1]["thread_id"])
    return ThreadPlan("start", missed=list(history))


def _taken(records: dict) -> set:
    """Threads an engine went on in on some branch: an app's shared record of one no longer
    describes it, since the thread holds that branch's turns too."""
    return {r.get("thread_id") for r in (records or {}).values()
            if isinstance(r, dict) and not r.get("shared") and r.get("own_turns")}


def _text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        content = "\n".join(parts)
    return str(content or "").strip()


def _line(message: dict) -> str:
    role = str(message.get("role") or "")
    text = _text(message)
    if role == "user":
        return f"User: {text}" if text else ""
    if role == "assistant":
        return f"Assistant: {text}" if text else ""
    name = message.get("name") or message.get("tool_name") or message.get("tool")
    if name or role in ("tool", "code", "toolResult"):
        first = text.splitlines()[0] if text else ""
        if len(first) > _TOOL_LINE_CHARS:
            first = first[:_TOOL_LINE_CHARS] + "…"
        return f"Tool {name or 'call'}: {first}".rstrip()
    if role in ("system", "summary") and text:
        return f"Summary of earlier conversation: {text}"
    return ""


def transcript(messages: list[dict], budget: int = HANDOFF_BUDGET_CHARS) -> str:
    """The missed turns as text within ``budget`` characters, the newest kept."""
    lines = [line for line in (_line(message) for message in messages) if line]
    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        if used + len(line) + 1 > budget:
            if not kept:
                kept.append(line[-budget:])
            break
        kept.append(line)
        used += len(line) + 1
    kept.reverse()
    if len(kept) < len(lines):
        kept.insert(0, "[Earlier turns left out]")
    return "\n".join(kept)


def handoff_text(messages: list[dict]) -> str:
    """The note a thread gets about turns run without it, or ''."""
    body = transcript(messages)
    if not body:
        return ""
    return ("This conversation continues in OpenProgram. These turns happened without you; "
            "read them as context, then answer the new message.\n\n"
            "<conversation_so_far>\n" + body + "\n</conversation_so_far>")


def read_records(db: Any, session_id: str, provider: str) -> dict:
    row = db.get_session(session_id) or {}
    threads = row.get(_SESSION_FIELD) or {}
    records = threads.get(provider) if isinstance(threads, dict) else None
    return dict(records) if isinstance(records, dict) else {}


def write_record(db: Any, session_id: str, provider: str, key: str, record: dict) -> None:
    """Keep one branch's thread record (thread id, last message seen)."""
    def update(current: dict) -> dict:
        threads = dict(current or {})
        records = dict(threads.get(provider) or {})
        old = dict(records.get(key) or {})
        # The engine went on here: the thread now holds this branch's turns, so
        # another branch forks it instead of taking it as the app's shared one.
        records[key] = {**old, **record, **({"shared": False} if old.get("shared") else {})}
        threads[provider] = records
        return threads

    if hasattr(db, "update_session_dict"):
        db.update_session_dict(session_id, _SESSION_FIELD, update)
    else:
        row = db.get_session(session_id) or {}
        db.update_session(session_id, **{_SESSION_FIELD: update(row.get(_SESSION_FIELD) or {})})

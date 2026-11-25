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


def held_record(history: list[dict], held: str, text: Optional[str]) -> dict:
    """The record of a turn whose thread took its message, then failed: ``seen`` is the last
    message the thread took; ``prior`` what it held before this turn (handoff included), for a
    retry of the message, which comes back with a new id; ``held_text`` tells a retry from an edit."""
    ids = _ids(history)
    return {"seen": held, "prior": ids[-1] if ids else "", "held_text": _digest(text)}


def _digest(text: Optional[str]) -> str:
    import hashlib
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()[:16]


def resumed_position(history: list[dict], plan: "ThreadPlan") -> Optional[str]:
    """What a resumed thread holds of ``history`` before this turn: the last message before
    the handoff it has yet to read. A branch taking up another record's thread records this,
    not that record's position, which may lie on the other branch."""
    missed = set(_ids(plan.missed))
    return next((i for i in reversed(_ids(history)) if i and i not in missed), None)


def plan_thread(records: dict, key: str, history: list[dict], text: Optional[str] = None) -> ThreadPlan:
    """How a turn on branch ``key`` with ``history`` (before its message ``text``) reaches its thread."""
    ids = set(_ids(history))
    own = records.get(key) if isinstance(records, dict) else None
    taken = _taken(records)
    if isinstance(own, dict) and own.get("handed_over"):
        # Its thread went on in a retry on another branch: this branch starts over.
        return ThreadPlan("start", missed=list(history))
    if isinstance(own, dict) and own.get("thread_id") and not (own.get("shared") and own["thread_id"] in taken):
        seen = str(own.get("seen") or "")
        if seen and seen in ids:
            return ThreadPlan("resume", own["thread_id"], _after(history, seen))
        prior = own.get("prior")
        if text is not None and own.get("held_text") and own["held_text"] != _digest(text):
            prior = None  # the failed message was edited: the thread holding it is left
        if prior == "" and not history:
            return ThreadPlan("resume", own["thread_id"])  # a retry of the thread's first message
        if prior and prior in ids:
            # The message the thread took before its turn failed was sent again (a retry):
            # the thread goes on from where it was.
            return ThreadPlan("resume", own["thread_id"], _after(history, prior))
        if not seen and not history:
            return ThreadPlan("resume", own["thread_id"])
        # The branch went back before what the thread saw (an edit or rewind).
        return ThreadPlan("start", missed=list(history))
    # A retry of a failed message comes on a branch of its own: the thread that took the
    # message goes on, when it held exactly what comes before it here.
    if text is not None:
        digest = _digest(text)
        for held_key, record in (records or {}).items():
            if not (isinstance(record, dict) and record.get("thread_id") and record.get("held_text") == digest):
                continue
            if any(k != held_key and isinstance(r, dict) and r.get("thread_id") == record["thread_id"]
                   and not r.get("handed_over") and not r.get("shared") for k, r in records.items()):
                continue  # another branch already went on in it
            prior = record.get("prior")
            if (prior == "" and not history) or (prior and prior in ids and not _after(history, prior)):
                return ThreadPlan("resume", record["thread_id"])
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
        if record.get("handed_over"):
            continue  # its thread went on in a retry elsewhere: it is not this branch's end
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


#: How many of a thread's turn ids a record keeps as written by an engine.
_OWN_TURNS_KEPT = 1000


def write_record(db: Any, session_id: str, provider: str, key: str, record: dict) -> None:
    """Keep one branch's thread record (thread id, last message seen)."""
    def update(current: dict) -> dict:
        threads = dict(current or {})
        records = dict(threads.get(provider) or {})
        old = dict(records.get(key) or {})
        if "thread_id" in record and old.get("thread_id") not in (None, record["thread_id"]):
            old = {}  # another thread now: nothing of the last one's record is this one's
        # The engine went on here: the thread now holds this branch's turns, so
        # another branch forks it instead of taking it as the app's shared one.
        merged = {**old, **record, **({"shared": False} if old.get("shared") else {})}
        if "seen" in record and "prior" not in record:
            # Only a failed turn's record keeps where it was before.
            merged.pop("prior", None)
            merged.pop("held_text", None)
        if isinstance(record.get("own_turns"), list):
            # Two turns may keep ids at once: each adds its own to what is there now.
            kept = list(old.get("own_turns") or [])
            merged["own_turns"] = (kept + [t for t in record["own_turns"] if t not in kept])[-_OWN_TURNS_KEPT:]
        records[key] = merged
        if merged.get("thread_id"):
            # A failed turn's thread taken up by a retry on this branch is no longer its branch's.
            for other, held in list(records.items()):
                if (other != key and isinstance(held, dict) and held.get("held_text")
                        and held.get("thread_id") == merged["thread_id"]):
                    records[other] = {**{k: v for k, v in held.items() if k not in ("prior", "held_text")},
                                      "handed_over": True}
        threads[provider] = records
        return threads

    if hasattr(db, "update_session_dict"):
        db.update_session_dict(session_id, _SESSION_FIELD, update)
    else:
        row = db.get_session(session_id) or {}
        db.update_session(session_id, **{_SESSION_FIELD: update(row.get(_SESSION_FIELD) or {})})

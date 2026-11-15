"""Open work: what a conversation's agent still owes the user.

The todo board plans the task in front of the agent and ends with it. Open
work outlives the task: a reply the agent waits on, a check it promised, a
draft the user has not answered. It belongs to the conversation, like
everything else a long-running conversation keeps; the agent sees the open
items in its system prompt on every turn there, and the Activity panel
lists them. What should carry over to other conversations belongs in memory.

Stored in ``<session dir>/open_work.json``, written atomically under a
per-conversation lock. Each item:

    id          short incrementing string ("1", "2", …)
    text        what is owed, one line
    note        optional details or the latest state
    status      open | done
    created_at / updated_at / done_at  unix timestamps
"""
from __future__ import annotations

import logging
import json
import threading
import time
from typing import Any

_log = logging.getLogger(__name__)

STATUSES = ("open", "done")
TEXT_MAX = 300
NOTE_MAX = 2000
# Done items kept for the list's history; the oldest are dropped first.
DONE_KEPT = 50
# Open items shown in the system prompt; the tool lists all of them.
PROMPT_ITEMS = 20

_locks: dict[str, threading.RLock] = {}
_locks_master = threading.Lock()


def lock(session_id: str) -> threading.RLock:
    with _locks_master:
        return _locks.setdefault(session_id, threading.RLock())


def path(session_id: str):
    """The conversation's open_work.json; None when the conversation is gone."""
    from openprogram.runtime.context.session_db import default_db
    sessions = default_db()
    if not sessions._session_dir(session_id).exists():  # noqa: SLF001 — same as todos.json
        return None
    return sessions.state_file(session_id, "open_work.json")


def load(session_id: str) -> list[dict[str, Any]]:
    target = path(session_id)
    if target is None:
        return []
    try:
        blob = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = blob.get("items") if isinstance(blob, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _save(session_id: str, items: list[dict[str, Any]]) -> None:
    done = [item for item in items if item.get("status") == "done"]
    if len(done) > DONE_KEPT:
        dropped = {id(item) for item in sorted(done, key=lambda i: i.get("done_at") or 0)[:-DONE_KEPT]}
        items = [item for item in items if id(item) not in dropped]
    target = path(session_id)
    if target is None:
        raise ValueError(f"conversation {session_id!r} not found")
    target.write_text(json.dumps({"version": 1, "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    _publish(session_id, items)


def _publish(session_id: str, items: list[dict[str, Any]]) -> None:
    try:
        from openprogram.runtime import interface_link
        interface_link.broadcast(
            {"type": "open_work_update", "data": {"session_id": session_id, "items": items}},
        )
    except Exception:
        # Live UI refresh only; the items are already persisted.
        _log.debug("open_work broadcast failed for session %s", session_id, exc_info=True)


def _next_id(items: list[dict[str, Any]]) -> str:
    highest = 0
    for item in items:
        try:
            highest = max(highest, int(item.get("id", 0)))
        except (TypeError, ValueError):
            continue
    return str(highest + 1)


def _clean(value: Any, limit: int) -> str:
    """Text is one line; a note keeps its line breaks."""
    value = str(value or "")
    return (" ".join(value.split()) if limit == TEXT_MAX else value.strip())[:limit]


def add(session_id: str, text: str, *, note: str = "") -> dict[str, Any]:
    text = _clean(text, TEXT_MAX)
    if not text:
        raise ValueError("text is required")
    with lock(session_id):
        items = load(session_id)
        now = time.time()
        item = {
            "id": _next_id(items), "text": text, "note": _clean(note, NOTE_MAX),
            "status": "open",
            "created_at": now, "updated_at": now, "done_at": None,
        }
        items.append(item)
        _save(session_id, items)
    return item


def update(session_id: str, item_id: str, *, text: str | None = None,
           note: str | None = None, status: str | None = None) -> dict[str, Any]:
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    with lock(session_id):
        items = load(session_id)
        item = next((i for i in items if str(i.get("id")) == str(item_id)), None)
        if item is None:
            raise KeyError(f"no open work #{item_id}")
        if text is not None:
            text = _clean(text, TEXT_MAX)
            if not text:
                raise ValueError("text cannot be empty")
            item["text"] = text
        if note is not None:
            item["note"] = _clean(note, NOTE_MAX)
        now = time.time()
        if status is not None and status != item.get("status"):
            item["status"] = status
            item["done_at"] = now if status == "done" else None
        item["updated_at"] = now
        _save(session_id, items)
    return item


def remove(session_id: str, item_id: str) -> None:
    with lock(session_id):
        items = load(session_id)
        kept = [i for i in items if str(i.get("id")) != str(item_id)]
        if len(kept) == len(items):
            raise KeyError(f"no open work #{item_id}")
        _save(session_id, kept)


def open_items(session_id: str) -> list[dict[str, Any]]:
    return [item for item in load(session_id) if item.get("status") == "open"]


def prompt_block(session_id: str | None) -> str:
    """The open items for the system prompt; empty when there are none."""
    items = open_items(session_id) if session_id else []
    if not items:
        return ""
    lines = [
        "<open_work>",
        "What you still owe the user in this conversation, kept across its turns. "
        "Mark an item done with open_work when it is finished, and add one "
        "whenever you promise or wait on something that outlives the current task.",
    ]
    for item in items[:PROMPT_ITEMS]:
        note = f" — {item['note']}" if item.get("note") else ""
        lines.append(f"- #{item['id']} {item['text']}{note}")
    if len(items) > PROMPT_ITEMS:
        lines.append(f"- … and {len(items) - PROMPT_ITEMS} more; open_work(action=\"list\") shows all.")
    lines.append("</open_work>")
    return "\n".join(lines)

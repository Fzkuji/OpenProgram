"""todo_read / todo_write tools — session-scoped task list for the LLM.

Modelled after Claude Code's TodoWrite: the agent keeps a small list of
in-flight tasks visible to itself between turns. Backed by in-memory state
shared across tool calls in the same process.
"""

from __future__ import annotations

import threading
from typing import Any


_LOCK = threading.Lock()
_TODOS: list[dict[str, Any]] = []


# ─── todo_read ────────────────────────────────────────────────────────────────

READ_NAME = "todo_read"

READ_DESCRIPTION = (
    "Return the current session todo list. Use this at the start of a session "
    "or whenever you're unsure what's outstanding. Returns a JSON array of "
    "{id, subject, status} items (status: pending|in_progress|completed)."
)

READ_SPEC: dict[str, Any] = {
    "name": READ_NAME,
    "description": READ_DESCRIPTION,
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def read_execute(**_: Any) -> str:
    with _LOCK:
        if not _TODOS:
            return "(no todos)"
        return "\n".join(
            f"[{t['status']:<12}] #{t['id']} {t['subject']}" for t in _TODOS
        )


# ─── todo_write ───────────────────────────────────────────────────────────────

WRITE_NAME = "todo_write"

WRITE_DESCRIPTION = (
    "Replace the entire todo list with the provided items. Pass the full new "
    "list every time (this is a set operation, not an append). Use to plan "
    "multi-step work at the start of a task and to mark items completed as "
    "you progress. Keep the list focused on non-trivial work — skip anything "
    "that can be done in one tool call.\n"
    "\n"
    "Each item: {id: string, subject: string, status: 'pending'|'in_progress'|'completed'}"
)

WRITE_SPEC: dict[str, Any] = {
    "name": WRITE_NAME,
    "description": WRITE_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "description": "Full new todo list. Replaces any previous list.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "subject": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                    },
                    "required": ["id", "subject", "status"],
                },
            },
        },
        "required": ["items"],
    },
}


def write_execute(items: list[dict[str, Any]] | None = None, **_: Any) -> str:
    if items is None or not isinstance(items, list):
        return "Error: items must be an array"

    cleaned: list[dict[str, Any]] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            return f"Error: item #{i} is not an object"
        for key in ("id", "subject", "status"):
            if key not in it:
                return f"Error: item #{i} missing required field {key!r}"
        if it["status"] not in ("pending", "in_progress", "completed"):
            return f"Error: item #{i} has invalid status {it['status']!r}"
        cleaned.append({"id": str(it["id"]), "subject": str(it["subject"]), "status": it["status"]})

    with _LOCK:
        _TODOS[:] = cleaned

    counts = {"pending": 0, "in_progress": 0, "completed": 0}
    for it in cleaned:
        counts[it["status"]] += 1
    return (
        f"Stored {len(cleaned)} todo{'s' if len(cleaned) != 1 else ''} "
        f"(pending={counts['pending']}, in_progress={counts['in_progress']}, "
        f"completed={counts['completed']})"
    )

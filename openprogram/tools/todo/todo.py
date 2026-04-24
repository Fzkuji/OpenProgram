from __future__ import annotations

from typing import Any

_TODOS: list[dict[str, str]] = []
_VALID_STATUSES = ("pending", "in_progress", "completed")


def read_execute() -> str:
    if not _TODOS:
        return "(no todos)"
    return "\n".join(
        f"[{item['status']:<12}] #{item['id']} {item['subject']}"
        for item in _TODOS
    )


def write_execute(*, items: Any = None) -> str:
    if not isinstance(items, list):
        return "Error: items must be an array"

    normalized: list[dict[str, str]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            return f"Error: item #{idx} must be an object"
        for required in ("id", "subject", "status"):
            if required not in item:
                return f"Error: item #{idx} missing required field {required!r}"
        status = item["status"]
        if status not in _VALID_STATUSES:
            return f"Error: item #{idx} has invalid status {status!r}"
        normalized.append({
            "id": str(item["id"]),
            "subject": str(item["subject"]),
            "status": status,
        })

    _TODOS[:] = normalized
    counts = {status: 0 for status in _VALID_STATUSES}
    for item in _TODOS:
        counts[item["status"]] += 1
    noun = "todo" if len(_TODOS) == 1 else "todos"
    return (
        f"Stored {len(_TODOS)} {noun} "
        f"(pending={counts['pending']}, in_progress={counts['in_progress']}, completed={counts['completed']})"
    )


READ_TOOL = {
    "spec": {
        "name": "todo_read",
        "description": "Read the current todo list.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "execute": read_execute,
}

WRITE_TOOL = {
    "spec": {
        "name": "todo_write",
        "description": "Replace the current todo list.",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": ["string", "integer"]},
                            "subject": {"type": "string"},
                            "status": {"type": "string", "enum": list(_VALID_STATUSES)},
                        },
                        "required": ["id", "subject", "status"],
                        "additionalProperties": True,
                    },
                },
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    },
    "execute": write_execute,
}

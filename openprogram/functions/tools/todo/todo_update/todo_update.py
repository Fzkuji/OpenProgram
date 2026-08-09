"""todo_update — edit an entry on the session's planning board."""
from __future__ import annotations

import time

from openprogram.functions._runtime import function
from .. import shared


@function(
    name="todo_update",
    description=(
        "Update an entry on this session's todo planning board. Only the "
        "fields you pass are changed. Mark items in_progress when you "
        "start them and completed when done — the board is the plan, not "
        "the execution (dispatch work with agent(), track it with "
        "list_tasks).\n"
        "\n"
        "Args:\n"
        "  todo_id: id of the todo to update.\n"
        "  status: pending | in_progress | completed (optional).\n"
        "  subject: new title (optional).\n"
        "  owner: who is claiming this todo, free text (optional).\n"
        "  description: new details (optional)."
    ),
    toolset=["core"],
    unsafe_in=["wechat", "telegram"],
)
def todo_update(
    todo_id: str,
    status: str = "",
    subject: str = "",
    owner: str = "",
    description: str = "",
) -> str:
    """Update fields of a todo entry."""
    return _todo_update_impl(todo_id, status, subject, owner, description)


def _todo_update_impl(
    todo_id: str,
    status: str = "",
    subject: str = "",
    owner: str = "",
    description: str = "",
) -> str:
    """Implementation body — kept apart from the @function binding so
    tests can call it directly (the binding object is not callable)."""
    sid = shared.current_session_id()
    if not sid:
        return "[todo_update error] no active session context"
    todo_id = (todo_id or "").strip()
    if not todo_id:
        return "[todo_update error] todo_id required"
    if status and status not in shared.STATUSES:
        return (
            f"[todo_update error] invalid status {status!r} "
            f"(expected one of: {', '.join(shared.STATUSES)})"
        )
    with shared.lock():
        todos = shared.load(sid)
        entry = next((t for t in todos if t.get("id") == todo_id), None)
        if entry is None:
            return f"[todo_update error] unknown todo_id={todo_id!r}"
        updated: list[str] = []
        for field, value in (
            ("status", status),
            ("subject", subject),
            ("owner", owner),
            ("description", description),
        ):
            if value and entry.get(field) != value:
                entry[field] = value
                updated.append(field)
        if not updated:
            return f"Todo #{todo_id} unchanged (no new field values)"
        entry["updated_at"] = time.time()
        shared.save(sid, todos)
    return f"Todo #{todo_id} updated ({', '.join(updated)})"

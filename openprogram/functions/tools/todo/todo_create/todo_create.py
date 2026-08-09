"""todo_create — add an entry to the session's planning board."""
from __future__ import annotations

import time

from openprogram.functions._runtime import function
from .. import shared


@function(
    name="todo_create",
    description=(
        "Add an entry to this session's todo planning board — a written "
        "checklist of intended work, not execution. Returns the new todo's "
        "id. To actually dispatch work, spawn an agent(); to track "
        "dispatched work, use list_tasks/task_output.\n"
        "\n"
        "Args:\n"
        "  subject: brief title for the todo.\n"
        "  description: what needs to be done (optional).\n"
        "  blocked_by: comma-separated ids of todos this one waits on "
        "(optional)."
    ),
    toolset=["core"],
    unsafe_in=["wechat", "telegram"],
)
def todo_create(subject: str, description: str = "", blocked_by: str = "") -> str:
    """Create a todo entry on the session planning board."""
    return _todo_create_impl(subject, description, blocked_by)


def _todo_create_impl(subject: str, description: str = "", blocked_by: str = "") -> str:
    """Implementation body — kept apart from the @function binding so
    tests can call it directly (the binding object is not callable)."""
    sid = shared.current_session_id()
    if not sid:
        return "[todo_create error] no active session context"
    subject = (subject or "").strip()
    if not subject:
        return "[todo_create error] subject required"
    deps = [d.strip() for d in (blocked_by or "").split(",") if d.strip()]
    with shared.lock():
        todos = shared.load(sid)
        known = {t.get("id") for t in todos}
        unknown = [d for d in deps if d not in known]
        if unknown:
            return f"[todo_create error] blocked_by refers to unknown todo id(s): {', '.join(unknown)}"
        now = time.time()
        todo_id = shared.next_id(todos)
        todos.append({
            "id": todo_id,
            "subject": subject,
            "description": description or "",
            "status": "pending",
            "owner": "",
            "blocked_by": deps,
            "created_at": now,
            "updated_at": now,
        })
        shared.save(sid, todos)
    return f"Todo #{todo_id} created: {subject}"

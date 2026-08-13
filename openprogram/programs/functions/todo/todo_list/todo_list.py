"""todo_list — show the session's planning board, grouped by status."""
from __future__ import annotations

from openprogram.programs._runtime import function
from .. import shared

_GROUP_ORDER = ("in_progress", "pending", "completed")


@function(
    name="todo_list",
    description=(
        "List every entry on this session's todo planning board, grouped "
        "by status (in_progress, then pending, then completed). The board "
        "is the written plan — for the agents actually running, use "
        "list_jobs instead."
    ),
    toolset=["core"],
)
def todo_list() -> str:
    """Render the session planning board as text."""
    return _todo_list_impl()


def _todo_list_impl() -> str:
    """Implementation body — kept apart from the @function binding so
    tests can call it directly (the binding object is not callable)."""
    sid = shared.current_session_id()
    if not sid:
        return "[todo_list error] no active session context"
    todos = shared.load(sid)
    if not todos:
        return "(no todos in this session)"
    lines: list[str] = []
    for status in _GROUP_ORDER:
        group = [t for t in todos if t.get("status") == status]
        if not group:
            continue
        lines.append(f"{status}:")
        for t in group:
            line = f"- #{t.get('id')} {t.get('subject')}"
            if t.get("owner"):
                line += f"  (owner: {t['owner']})"
            if t.get("blocked_by"):
                line += "  (blocked by " + ", ".join(f"#{d}" for d in t["blocked_by"]) + ")"
            lines.append(line)
    return "\n".join(lines)

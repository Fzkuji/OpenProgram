"""list_tasks — list this session's background agent tasks.

Named verb-object to pair with ``list_agents``; ``task_list`` would
collide with Claude Code's TaskList (a todo planning board — our todo
board uses the ``todo_*`` prefix instead).
"""
from __future__ import annotations

from openprogram.functions._runtime import function

_ROW_LIMIT = 50
_SUBJECT_CLIP = 80


@function(
    name="list_tasks",
    description=(
        "List the background tasks of the current session — everything "
        "spawned here with agent(run_in_background=true), newest first: "
        "task id, status (queued/running/completed/cancelled/errored) "
        "and the task's subject. Use it to check on parallel work you "
        "dispatched: fetch a result with task_output(task_id), stop one "
        "with task_stop(task_id)."
    ),
    toolset=["core"],
)
def list_tasks() -> str:
    """Render the current session's task table as text."""
    return _list_tasks_impl()


def _list_tasks_impl() -> str:
    """Implementation body — kept apart from the @function binding so
    tests can call it directly (the binding object is not callable)."""
    from openprogram.agent.run_control import _current_session_id
    sid = _current_session_id.get(None)
    if not sid:
        return "[list_tasks error] no active session context"
    from openprogram.agent.task import get_runner
    tasks = get_runner().list_tasks(sid, limit=_ROW_LIMIT)
    if not tasks:
        return "(no background tasks in this session)"
    lines = []
    for t in tasks:
        subject = (t.subject or t.prompt or "").strip().replace("\n", " ")
        if len(subject) > _SUBJECT_CLIP:
            subject = subject[:_SUBJECT_CLIP] + "…"
        lines.append(f"- {t.id}  [{t.status.value}]  {subject}")
    return "\n".join(lines)

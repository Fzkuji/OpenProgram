"""task_list — show the session's tasks with their steps."""
from __future__ import annotations

from openprogram.programs._host.runtime import function
from .. import shared

#: Closed tasks listed, newest last; older ones are counted.
_CLOSED_SHOWN = 10


@function(
    name="task_list",
    description=(
        "List this conversation's tasks: every open task with its numbered "
        "steps, their status and result, then the tasks already closed, one "
        "line each. Read-only. The task tools' own results already say "
        "where the open task stands, so this is for looking back. For the "
        "agents actually running, use list_agents instead."
    ),
    toolset=["core"],
)
def task_list() -> str:
    """Render the session's tasks as text."""
    return _task_list_impl()


def _count(steps: list[dict]) -> str:
    kept = shared.counted(steps)
    return f"{sum(1 for s in kept if s.get('status') == 'completed')}/{len(kept)}"


def _task_list_impl() -> str:
    """Implementation body — kept apart from the @function binding so
    tests can call it directly (the binding object is not callable)."""
    sid = shared.current_session_id()
    if not sid:
        return "[task_list error] no active session context"
    todos = shared.load(sid)
    tasks = shared.load_tasks(sid)
    lines: list[str] = []
    for task in tasks:
        if task.get("status") != "open":
            continue
        steps = shared.steps_of(todos, str(task.get("id")))
        owner = f" ({task['scope']})" if task.get("scope") else ""
        lines.append(f"{task.get('id')} {task.get('title')} [open]{owner}" + (f" {_count(steps)}" if steps else ""))
        if task.get("purpose"):
            lines.append(f"  {task['purpose']}")
        for number, step in enumerate(steps, 1):
            line = f"  {number}. [{step.get('status')}] {step.get('subject')}"
            if step.get("result"):
                line += f" — {step['result']}"
            lines.append(line)
    closed = [t for t in tasks if t.get("status") != "open"]
    if len(closed) > _CLOSED_SHOWN:
        lines.append(f"({len(closed) - _CLOSED_SHOWN} earlier closed tasks not listed)")
    for task in closed[-_CLOSED_SHOWN:]:
        steps = shared.steps_of(todos, str(task.get("id")))
        line = f"{task.get('id')} {task.get('title')} [{task.get('status')}]" + (f" {_count(steps)}" if steps else "")
        if task.get("outcome"):
            line += f" — {task['outcome']}"
        lines.append(line)
    known = {str(t.get("id")) for t in tasks}
    loose = [t for t in todos if str(t.get("plan") or "") not in known]
    if loose:
        # Entries written before tasks existed.
        lines.append("Earlier todos:")
        lines.extend(f"  [{t.get('status')}] {t.get('subject')}" for t in loose)
    return "\n".join(lines) if lines else "(no tasks in this conversation)"

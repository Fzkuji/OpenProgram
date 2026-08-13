"""list_jobs — list this session's background agent tasks.

Named verb-object to pair with ``list_agents``; ``agentic_workflow`` would
collide with Claude Code's TaskList (a todo planning board — our todo
board uses the ``todo_*`` prefix instead).
"""
from __future__ import annotations

from openprogram.agent.types import AgentToolResult
from openprogram.programs._runtime import function
from openprogram.providers.types import TextContent

_ROW_LIMIT = 50
_SUBJECT_CLIP = 80


@function(
    name="list_jobs",
    description=(
        "List the background tasks of the current session — everything "
        "spawned here with agent(run_in_background=true), newest first: "
        "task id, status (queued/running/completed/cancelled/errored) "
        "and the task's subject. Use it to check on parallel work you "
        "dispatched: fetch a result with job_output(job_id), stop one "
        "with job_stop(job_id)."
    ),
    toolset=["core"],
)
def list_jobs() -> str | AgentToolResult:
    """Render the current session's task table as text."""
    return _list_jobs_impl()


def _list_jobs_impl() -> str | AgentToolResult:
    """Implementation body — kept apart from the @function binding so
    tests can call it directly (the binding object is not callable)."""
    from openprogram.agent.run_control import _current_session_id
    sid = _current_session_id.get(None)
    if not sid:
        return "[list_jobs error] no active session context"
    from openprogram.agent.job import get_runner
    runner = get_runner()
    tasks = runner.list_jobs(sid, limit=_ROW_LIMIT)
    if not tasks:
        return AgentToolResult(
            content=[TextContent(text="(no background tasks in this session)")],
            details={"jobs": []},
        )
    lines = []
    details = []
    for t in tasks:
        subject = (t.subject or t.prompt or "").strip().replace("\n", " ")
        if len(subject) > _SUBJECT_CLIP:
            subject = subject[:_SUBJECT_CLIP] + "…"
        lines.append(f"- {t.id}  [{t.status.value}]  {subject}")
        view = runner.get_job_resource_view(t.id)
        details.append({
            "job_id": t.id,
            "status": t.status.value,
            "resource": view.to_dict() if view is not None else None,
        })
    return AgentToolResult(
        content=[TextContent(text="\n".join(lines))],
        details={"jobs": details},
    )

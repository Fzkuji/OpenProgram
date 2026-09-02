"""list_jobs — list this session's background agent tasks."""
from __future__ import annotations

from openprogram.agent.types import AgentToolResult
from openprogram.programs._runtime import function
from openprogram.providers.types import TextContent

_ROW_LIMIT = 50
_SUBJECT_CLIP = 80


@function(
    name="list_jobs",
    description=(
        "List the background tasks of the current session, newest first. "
        "Each row includes its execution id, status, subject, and the "
        "canonical nested resource view."
    ),
    toolset=["core"],
)
def list_jobs() -> str | AgentToolResult:
    """Render the current session's task table as text."""
    return _list_jobs_impl()


def _list_jobs_impl() -> str | AgentToolResult:
    """Implementation body kept separate for direct tests."""
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
    for task in tasks:
        subject = (task.subject or task.prompt or "").strip().replace("\n", " ")
        if len(subject) > _SUBJECT_CLIP:
            subject = subject[:_SUBJECT_CLIP] + "…"
        lines.append(f"- {task.id}  [{task.status.value}]  {subject}")
        view = runner.get_job_resource_view(task.id)
        details.append({
            "job_id": task.id,
            "status": task.status.value,
            "resource": view.to_dict() if view is not None else None,
        })
    return AgentToolResult(
        content=[TextContent(text="\n".join(lines))],
        details={"jobs": details},
    )

"""job_output — get the output of a background agent execution."""
from __future__ import annotations

from openprogram.agent.types import AgentToolResult
from openprogram.programs._runtime import function
from openprogram.programs.tools.agents.send_message.send_message.depth import (
    delegation_budget_left,
)
from openprogram.providers.types import TextContent

_MAX_TIMEOUT_MS = 600_000
_DEFAULT_TIMEOUT_MS = 30_000


@function(
    name="job_output",
    description=(
        "Get the output of a background task spawned with "
        "agent(run_in_background=true). Blocks until terminal or the "
        "timeout expires, and returns the task reply plus its canonical "
        "nested resource view.\n\n"
        "Args:\n"
        "  job_id: execution id returned by the background Agent.\n"
        "  block: whether to wait for completion (default true).\n"
        "  timeout: max wait time in ms (default 30000, max 600000)."
    ),
    toolset=["core"],
    can_use=delegation_budget_left,
)
def job_output(
    job_id: str,
    block: bool = True,
    timeout: float = _DEFAULT_TIMEOUT_MS,
) -> str | AgentToolResult:
    """Wait for (or peek at) an async execution and return its reply."""
    return _job_output_impl(job_id, block=block, timeout=timeout)


def _job_output_impl(
    job_id: str,
    block: bool = True,
    timeout: float = _DEFAULT_TIMEOUT_MS,
) -> str | AgentToolResult:
    if not job_id or not isinstance(job_id, str):
        return "[job_output error] execution id required"
    from openprogram.programs.tools.agents.agent._ownership import check_job_ownership
    denied = check_job_ownership(job_id.strip(), "job_output")
    if denied:
        return denied
    from openprogram.agent.job import get_runner
    runner = get_runner()
    wait_s = 0.001 if not block else max(
        0.001, min(_DEFAULT_TIMEOUT_MS if timeout is None else float(timeout), _MAX_TIMEOUT_MS) / 1000.0,
    )
    task = runner.await_job(job_id.strip(), timeout=wait_s)
    if task is None:
        return f"[job_output error] unknown execution_id={job_id!r}"
    status = task.status.value
    if status == "completed":
        out = task.result_text or "(spawned agent returned no text)"
        text = f"{out}\n\n[task {job_id} status={status}]"
    elif status == "cancelled":
        text = f"[task {job_id} cancelled] {task.error or ''}".rstrip()
    elif status == "errored":
        text = f"[task {job_id} errored] {task.error or 'unknown error'}"
    elif not block:
        text = f"[task {job_id} still {status}]"
    else:
        text = f"[task {job_id} still {status}] timed out; call job_output again."
    view = runner.get_job_resource_view(task.id)
    return AgentToolResult(
        content=[TextContent(text=text)],
        details={
            "job_id": task.id,
            "status": status,
            "resource": view.to_dict() if view is not None else None,
        },
    )

"""job_output — get the output of a background agent() spawn.

Parameter shape mirrors Claude Code's TaskOutput: ``block`` (default
true) and ``timeout`` in milliseconds (default 30s, capped at 10min).
"""
from __future__ import annotations

from openprogram.agent.types import AgentToolResult
from openprogram.programs._runtime import function
from openprogram.programs.tools.agents.send_message.send_message.depth import (
    delegation_budget_left,
)
from openprogram.providers.types import TextContent

# Claude Code's TaskOutput cap: 600000 ms.
_MAX_TIMEOUT_MS = 600_000
_DEFAULT_TIMEOUT_MS = 30_000


@function(
    name="job_output",
    description=(
        "Get the output of a background task spawned with "
        "agent(run_in_background=true). By default blocks until the task "
        "reaches a terminal state (completed/cancelled/errored) or the "
        "timeout expires; on timeout it returns with the task still "
        "running — call again to keep waiting. Returns the task's final "
        "reply text plus its terminal status.\n"
        "\n"
        "Args:\n"
        "  job_id: id returned by agent(run_in_background=true).\n"
        "  block: whether to wait for completion (default true). "
        "false = return the task's current status immediately.\n"
        "  timeout: max wait time in ms (default 30000, max 600000)."
    ),
    toolset=["core"],
    # Same as agent: present while the chain has collaboration budget
    # left, gone once it is spent.
    can_use=delegation_budget_left,
)
def job_output(
    job_id: str,
    block: bool = True,
    timeout: float = _DEFAULT_TIMEOUT_MS,
) -> str | AgentToolResult:
    """Wait for (or peek at) an async task and return its reply."""
    return _job_output_impl(job_id, block=block, timeout=timeout)


def _job_output_impl(
    job_id: str,
    block: bool = True,
    timeout: float = _DEFAULT_TIMEOUT_MS,
) -> str | AgentToolResult:
    if not job_id or not isinstance(job_id, str):
        return "[job_output error] job_id required"
    from openprogram.programs.tools.agents.agent._ownership import check_job_ownership
    denied = check_job_ownership(job_id.strip(), "job_output")
    if denied:
        return denied
    from openprogram.agent.job import get_runner
    runner = get_runner()
    # Never pass 0 to the runner: its restart-recovery path treats a
    # falsy timeout as "poll for 60s", the opposite of a peek.
    if not block:
        wait_s = 0.001
    else:
        ms = _DEFAULT_TIMEOUT_MS if timeout is None else float(timeout)
        wait_s = max(0.001, min(ms, _MAX_TIMEOUT_MS) / 1000.0)
    t = runner.await_job(job_id.strip(), timeout=wait_s)
    if t is None:
        return f"[job_output error] unknown job_id={job_id!r}"
    status = t.status.value
    if status == "completed":
        out = t.result_text or "(spawned agent returned no text)"
        text = f"{out}\n\n[task {job_id} status={status}]"
    elif status == "cancelled":
        text = f"[task {job_id} cancelled] {t.error or ''}".rstrip()
    elif status == "errored":
        text = f"[task {job_id} errored] {t.error or 'unknown error'}"
    elif not block:
        text = f"[task {job_id} still {status}]"
    else:
        text = (
            f"[task {job_id} still {status}] "
            "timed out; call job_output again to keep waiting."
        )
    view = runner.get_job_resource_view(t.id)
    return AgentToolResult(
        content=[TextContent(text=text)],
        details={
            "job_id": t.id,
            "status": status,
            "resource": view.to_dict() if view is not None else None,
        },
    )

"""job_stop — signal cancel for an in-flight async agent() spawn."""
from __future__ import annotations

from openprogram.agent.types import AgentToolResult
from openprogram.programs._runtime import function
from openprogram.programs.functions.vanilla.send_message.send_message.depth import (
    delegation_budget_left,
)
from openprogram.providers.types import TextContent


@function(
    name="job_stop",
    description=(
        "Cancel an in-flight async task. Idempotent — calling on an "
        "already-terminal task is a no-op. The runner sets the "
        "session's cancel event, which propagates into the LLM "
        "stream + tool pre-invocation hook so the spawned agent "
        "stops at its next cooperative checkpoint. A 30s watchdog "
        "force-flips the entity if the worker won't drop. Only the "
        "session that dispatched the task (or an ancestor on its task "
        "chain) may stop it. A job dispatched with agent(to=…) that is "
        "still queued in the target's inbox is withdrawn without "
        "touching the target's running turn.\n"
        "\n"
        "Args:\n"
        "  job_id: id of the task to cancel.\n"
        "  reason: optional human-readable reason recorded on the "
        "task entity."
    ),
    toolset=["core"],
    # Same as agent: present while the chain has collaboration budget
    # left, gone once it is spent.
    can_use=delegation_budget_left,
)
def job_stop(job_id: str, reason: str = "") -> str | AgentToolResult:
    """Signal cancel for an async task."""
    if not job_id or not isinstance(job_id, str):
        return "[job_stop error] job_id required"
    from openprogram.programs.functions.vanilla.agent._ownership import check_job_ownership
    denied = check_job_ownership(job_id.strip(), "job_stop")
    if denied:
        return denied
    from openprogram.agent.job import get_runner
    runner = get_runner()
    t = runner.cancel_job(job_id.strip(), reason=reason or None)
    if t is None:
        return f"[job_stop error] unknown job_id={job_id!r}"
    view = runner.get_job_resource_view(t.id)
    return AgentToolResult(
        content=[TextContent(
            text=f"[job_stop] job_id={job_id} status={t.status.value}",
        )],
        details={
            "job_id": t.id,
            "status": t.status.value,
            "resource": view.to_dict() if view is not None else None,
        },
    )

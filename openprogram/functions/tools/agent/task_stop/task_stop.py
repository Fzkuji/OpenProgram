"""task_stop — signal cancel for an in-flight async agent() spawn."""
from __future__ import annotations

from openprogram.functions._runtime import function
from openprogram.functions.tools.send_message.send_message.depth import (
    delegation_budget_left,
)


@function(
    name="task_stop",
    description=(
        "Cancel an in-flight async task. Idempotent — calling on an "
        "already-terminal task is a no-op. The runner sets the "
        "session's cancel event, which propagates into the LLM "
        "stream + tool pre-invocation hook so the spawned agent "
        "stops at its next cooperative checkpoint. A 30s watchdog "
        "force-flips the entity if the worker won't drop. Only the "
        "session that dispatched the task (or an ancestor on its task "
        "chain) may stop it. A task dispatched with agent(to=…) that is "
        "still queued in the target's inbox is withdrawn without "
        "touching the target's running turn.\n"
        "\n"
        "Args:\n"
        "  task_id: id of the task to cancel.\n"
        "  reason: optional human-readable reason recorded on the "
        "task entity."
    ),
    toolset=["core"],
    # Same as agent: present while the chain has collaboration budget
    # left, gone once it is spent.
    can_use=delegation_budget_left,
)
def task_stop(task_id: str, reason: str = "") -> str:
    """Signal cancel for an async task."""
    if not task_id or not isinstance(task_id, str):
        return "[task_stop error] task_id required"
    from openprogram.functions.tools.agent._ownership import check_task_ownership
    denied = check_task_ownership(task_id.strip(), "task_stop")
    if denied:
        return denied
    from openprogram.agent.task import get_runner
    runner = get_runner()
    t = runner.cancel_task(task_id.strip(), reason=reason or None)
    if t is None:
        return f"[task_stop error] unknown task_id={task_id!r}"
    return f"[task_stop] task_id={task_id} status={t.status.value}"

"""task_stop — signal cancel for an in-flight async agent() spawn."""
from __future__ import annotations

from openprogram.functions._runtime import function


@function(
    name="task_stop",
    description=(
        "Cancel an in-flight async task. Idempotent — calling on an "
        "already-terminal task is a no-op. The runner sets the "
        "session's cancel event, which propagates into the LLM "
        "stream + tool pre-invocation hook so the spawned agent "
        "stops at its next cooperative checkpoint. A 30s watchdog "
        "force-flips the entity if the worker won't drop.\n"
        "\n"
        "Args:\n"
        "  task_id: id of the task to cancel.\n"
        "  reason: optional human-readable reason recorded on the "
        "task entity."
    ),
    toolset=["core"],
    # Same as agent: a spawned agent has no delegated work to cancel.
    unsafe_in=["agent_spawn"],
)
def task_stop(task_id: str, reason: str = "") -> str:
    """Signal cancel for an async task."""
    if not task_id or not isinstance(task_id, str):
        return "[task_stop error] task_id required"
    from openprogram.agent.task import get_runner
    runner = get_runner()
    t = runner.cancel_task(task_id.strip(), reason=reason or None)
    if t is None:
        return f"[task_stop error] unknown task_id={task_id!r}"
    return f"[task_stop] task_id={task_id} status={t.status.value}"

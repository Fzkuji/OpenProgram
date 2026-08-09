"""await_task — block until an async task() spawn reaches a terminal
state and return its final reply."""
from __future__ import annotations

from openprogram.functions._runtime import function


@function(
    name="await_task",
    description=(
        "Block until an async task spawned with task(wait=False) "
        "reaches a terminal state (completed/cancelled/errored). "
        "Returns the task's final reply text plus its terminal "
        "status. Pair with task(wait=False) for parallel agent "
        "execution.\n"
        "\n"
        "Args:\n"
        "  task_id: id returned by task(wait=False).\n"
        "  timeout: max seconds to block. None = wait forever. "
        "On timeout the call returns with the task still running."
    ),
    toolset=["core"],
    # Same as task: a spawned agent neither delegates nor waits on
    # delegated work.
    unsafe_in=["agent_spawn"],
)
def await_task(task_id: str, timeout: float = 0) -> str:
    """Wait for an async task and return its final reply."""
    if not task_id or not isinstance(task_id, str):
        return "[await_task error] task_id required"
    from openprogram.agent.task import get_runner
    runner = get_runner()
    eff_timeout = None if (timeout is None or timeout <= 0) else float(timeout)
    t = runner.await_task(task_id.strip(), timeout=eff_timeout)
    if t is None:
        return f"[await_task error] unknown task_id={task_id!r}"
    status = t.status.value
    if status == "completed":
        out = t.result_text or "(spawned agent returned no text)"
        return f"{out}\n\n[task {task_id} status={status}]"
    if status == "cancelled":
        return f"[task {task_id} cancelled] {t.error or ''}".rstrip()
    if status == "errored":
        return f"[task {task_id} errored] {t.error or 'unknown error'}"
    # still running / queued
    return (
        f"[task {task_id} still {status}] "
        f"timed out after {timeout}s; call await_task again to keep waiting."
    )

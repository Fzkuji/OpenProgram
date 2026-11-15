"""job_output — get the output of a background agent execution."""
from __future__ import annotations

from openprogram.models.providers.types import AgentToolResult
from openprogram.programs._host.runtime import function
from openprogram.models.providers.types import TextContent
import logging

_log = logging.getLogger(__name__)

_MAX_TIMEOUT_MS = 600_000
_DEFAULT_TIMEOUT_MS = 30_000


@function(
    name="job_output",
    description=(
        "Get the output of a background task spawned with "
        "agent(run_in_background=true), or the reply to a send_message "
        "delivery. Blocks until terminal or the "
        "timeout expires, and returns the task reply plus its canonical "
        "nested resource view.\n\n"
        "Args:\n"
        "  job_id: execution id returned by the background Agent, or a "
        "send_message delivery_id.\n"
        "  block: whether to wait for completion (default true).\n"
        "  timeout: max wait time in ms (default 30000, max 600000)."
    ),
    toolset=["core"],
    # read_conversation(execution id) took its place for the model;
    # kept registered for programs and old transcripts.
    expose=False,
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
    from openprogram.runtime.jobs import get_runner
    runner = get_runner()
    wait_s = 0.001 if not block else max(
        0.001, min(_DEFAULT_TIMEOUT_MS if timeout is None else float(timeout), _MAX_TIMEOUT_MS) / 1000.0,
    )
    task = runner.await_job(job_id.strip(), timeout=wait_s)
    if task is None:
        return f"[job_output error] unknown execution_id={job_id!r}"
    status = task.status.value
    if status in ("completed", "cancelled", "errored"):
        # The caller has the result now; the automated "it finished"
        # notice (agent/job/notices.py) would only repeat it.
        try:
            from openprogram.runtime.jobs import notices
            notices.consume(task)
        except Exception:
            # Best-effort: at worst the notice repeats the result.
            _log.debug("job notice not consumed for %s", job_id, exc_info=True)
    # A task whose execution stopped short of finishing (an unknown outcome, or a pause) waits for a
    # person, not for time: report it instead of asking the caller to wait again.
    stopped = None if status in ("completed", "cancelled", "errored") else runner.stopped_execution(task.id)
    if stopped is not None:
        why = ("an operation's outcome is unknown and needs checking"
               if stopped.status.value == "reconciliation_required" else "it was paused")
        status = stopped.status.value
        text = (f"[task {job_id} stopped] the agent stopped before finishing: {why} "
                f"({stopped.reason_code or status}). Check the page or app before repeating its work.")
    elif status == "completed":
        from openprogram.state.memory.policy import consume_node
        consume_node(task.parent_session_id, task.head_id)
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

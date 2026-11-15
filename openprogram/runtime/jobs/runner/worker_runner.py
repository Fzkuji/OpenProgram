"""The JobRunner as a function subprocess sees it."""
from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from openprogram.runtime.execution.worker_calls import SubprocessWorkerCalls

_CALL_RUNNER = "openprogram.runtime.jobs.runner.worker_runner:call_runner"

_WORKER_METHODS = frozenset({
    "admit_job_entity", "await_job", "cancel_execution", "get_job", "get_job_resource_view",
    "list_jobs", "spawn_job", "stopped_execution",
})


def call_runner(method: str, *args: Any, callback=None, **kwargs: Any) -> Any:
    """Runs in the parent: one JobRunner call made by its function subprocess."""
    from openprogram.runtime.jobs.runner.shared import get_runner

    if method not in _WORKER_METHODS:
        raise PermissionError(f"JobRunner.{method} cannot be called from a function subprocess")
    if callback is not None:
        kwargs["on_accepted"] = callback
    return getattr(get_runner(), method)(*args, **kwargs)


class WorkerRunner:
    """Sends every JobRunner call of a function subprocess to the Worker.

    The sub-agent runs in the Worker under the Worker's own claim, so a caller
    here has no claim to take and no Worker lock to contend for.
    """

    def __init__(self, calls: SubprocessWorkerCalls):
        self._calls = calls

    def can_borrow_current_claim(self, session_id: str) -> bool:
        """True sends ``run_agent_turn`` straight to submit-and-wait: the Worker holds what it needs."""
        return True

    def claim_only(self, job_id: str):
        return nullcontext()

    def spawn_job(self, *args: Any, on_accepted=None, borrow_current_claim: bool = False, **kwargs: Any) -> str:
        return self._calls.call(_CALL_RUNNER, "spawn_job", *args, callback=on_accepted, **kwargs)

    def __getattr__(self, method: str):
        if method not in _WORKER_METHODS:
            raise AttributeError(method)

        def forwarded(*args: Any, **kwargs: Any) -> Any:
            return self._calls.call(_CALL_RUNNER, method, *args, **kwargs)

        return forwarded

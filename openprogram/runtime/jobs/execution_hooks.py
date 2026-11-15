"""The Jobs engine's registrations with the execution control plane.

Imported by ``openprogram.runtime.jobs`` itself. The control plane reads Job
records during cancellation, routes a Job's commands to its live runner and
validates a stored Job turn request against the turn engine's fields.
"""
from __future__ import annotations

from typing import Any

from openprogram.runtime.execution import hooks
from openprogram.runtime.jobs.input import turn_request_fields


class _JobRecords:
    """``execution.hooks.JobRecords`` over ``jobs.store`` and the runner."""

    def load(self, session_id: str, job_id: str) -> Any | None:
        from openprogram.runtime.jobs.store import load_job

        return load_job(session_id, job_id)

    def list(self, session_id: str) -> list[Any]:
        from openprogram.runtime.jobs.store import list_jobs

        return list_jobs(session_id)

    def is_terminal(self, job: Any) -> bool:
        from openprogram.runtime.jobs.types import is_terminal

        return is_terminal(job.status)

    def is_waiting(self, job: Any) -> bool:
        from openprogram.runtime.jobs.types import JobStatus

        return job.status in (JobStatus.PENDING, JobStatus.QUEUED)

    def update_status(self, session_id: str, job_id: str, status: Any, **fields: Any) -> Any:
        from openprogram.runtime.jobs.store import update_job_status
        from openprogram.runtime.jobs.types import JobStatus

        return update_job_status(session_id, job_id, JobStatus(status), **fields)

    def _runner(self) -> Any | None:
        # Read the singleton when called: the runner starts after this
        # module is imported, and a lookup must not start one.
        from openprogram.runtime.jobs.runner import current_runner

        return current_runner()

    def live_job(self, job_id: str) -> Any | None:
        runner = self._runner()
        return None if runner is None else runner.get_job(job_id)

    def cancel_live(self, job_id: str, *, reason: str) -> Any | None:
        runner = self._runner()
        return None if runner is None else runner.cancel_execution(job_id, reason=reason)


def _job_runner(store: Any) -> Any | None:
    from openprogram.runtime.jobs.runner import runner_for_execution_store

    return runner_for_execution_store(store)


def _worktree_owner_running(worktree: Any) -> str | None:
    """The helper Job a worktree belongs to while it has not ended."""
    from openprogram.runtime.jobs.store import load_job
    from openprogram.runtime.jobs.types import is_terminal

    if not worktree.parent_job or not worktree.parent_session:
        return None
    job = load_job(worktree.parent_session, worktree.parent_job)
    if job is None:
        job = _JobRecords().live_job(worktree.parent_job)
    return None if job is None or is_terminal(job.status) else job.id


def _register_worktree_hold() -> None:
    from openprogram.state.worktree import manager as worktree_manager

    worktree_manager.set_owner_check(_worktree_owner_running)


hooks.register("job_records", _JobRecords())
hooks.register("job_runner", _job_runner)
hooks.register("turn_request_fields", turn_request_fields)
hooks.register("agent_roster_facts", __import__(
    "openprogram.runtime.jobs.agent_status", fromlist=["RosterFacts"]).RosterFacts())
# A worktree's merge and discard wait for the helper working in it.
_register_worktree_hold()

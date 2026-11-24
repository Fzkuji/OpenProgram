"""A Job that ended and was closed is not closed again on every pass."""
from __future__ import annotations

from types import SimpleNamespace

from openprogram.runtime.jobs.runner import projection, shared
from openprogram.runtime.jobs.types import Job, JobStatus


class _Runner(projection.ProjectionOperations):
    def __init__(self, jobs, executions):
        self.jobs, self.closed = jobs, []
        self._governor = object()
        self._execution_store = SimpleNamespace(get_execution=executions.get)
        self._execution_control = SimpleNamespace(reconcile_terminal_cancel=lambda execution: None)

    def list_jobs(self):
        return list(self.jobs)

    def _project_canonical_terminal(self, execution, **_fence):
        self.closed.append(execution.execution_id)


def _execution(job_id, version=1):
    return SimpleNamespace(execution_id=job_id, session_id="s", status_version=version,
                           status=SimpleNamespace(value="completed"))


def _job(job_id, status):
    return Job(id=job_id, parent_session_id="s", prompt="work", agent_id="helper", status=status)


def test_an_ended_job_is_closed_once_per_state(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(projection, "_clock", lambda: clock[0])
    monkeypatch.setattr(shared, "_store_load", lambda session_id, job_id: None)
    executions = {"j_done": _execution("j_done"), "j_open": _execution("j_open")}
    runner = _Runner([_job("j_done", JobStatus.COMPLETED), _job("j_open", JobStatus.RUNNING)], executions)

    for _ in range(3):
        runner._project_existing_canonical_terminals()
    assert runner.closed.count("j_done") == 1, "an ended Job is closed on the first pass only"
    assert runner.closed.count("j_open") == 3, "a Job whose own row has not ended is projected every pass"

    executions["j_done"] = _execution("j_done", version=2)
    runner._project_existing_canonical_terminals()
    assert runner.closed.count("j_done") == 2, "a run that changed is closed again"

    clock[0] += projection.RECLOSE_SECONDS
    runner._project_existing_canonical_terminals()
    assert runner.closed.count("j_done") == 3, "and every ended Job is closed again after five minutes"

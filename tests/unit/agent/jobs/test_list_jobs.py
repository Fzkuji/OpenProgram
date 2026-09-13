"""Background execution resource tools use the canonical nested DTO."""
from __future__ import annotations

from openprogram.agent.run_control import _current_session_id
from openprogram.agent.job.types import Job, JobStatus
from openprogram.agent.types import AgentToolResult
from openprogram.programs.tools.agents.agent.job_output.job_output import _job_output_impl
from openprogram.programs.tools.agents.agent.list_jobs.list_jobs import _list_jobs_impl


def _task(tid: str, status: JobStatus, prompt: str = "do a thing") -> Job:
    return Job(id=tid, parent_session_id="s1", prompt=prompt,
               agent_id="a1", status=status)


class _FakeRunner:
    def __init__(self, tasks):
        self._tasks = tasks
        self.await_calls = []

    def list_jobs(self, session_id, limit=None):
        return [t for t in self._tasks if t.parent_session_id == session_id]

    def await_job(self, job_id, timeout=None):
        self.await_calls.append(timeout)
        return next((t for t in self._tasks if t.id == job_id), None)

    def get_job(self, job_id):
        return next((t for t in self._tasks if t.id == job_id), None)

    def get_job_resource_view(self, job_id):
        for task in self._tasks:
            if task.id == job_id:
                return _View(task)
        return None


class _View:
    def __init__(self, task):
        self.task = task

    def to_dict(self):
        return {
            "job_id": self.task.id,
            "execution_id": self.task.id,
            "status": self.task.status.value,
            "resource": {
                "resource_state": "untracked",
                "queue_wait": None,
                "limits": {},
                "usage": {},
            },
            "event_cursor": {
                "execution_id": self.task.id,
                "next_sequence": 1,
                "snapshot_status_version": 1,
            },
            "execution": {"execution_id": self.task.id},
        }


def _text(result: AgentToolResult) -> str:
    return result.content[0].text


def _with_runner(monkeypatch, runner):
    from openprogram.agent import job as job_mod
    monkeypatch.setattr(job_mod, "get_runner", lambda: runner)


def test_list_jobs_renders_rows(monkeypatch) -> None:
    runner = _FakeRunner([
        _task("t1", JobStatus.RUNNING, "survey plan A " + "x" * 100),
        _task("t2", JobStatus.COMPLETED),
    ])
    _with_runner(monkeypatch, runner)
    tok = _current_session_id.set("s1")
    try:
        result = _list_jobs_impl()
    finally:
        _current_session_id.reset(tok)
    out = _text(result)
    assert "t1" in out and "[running]" in out
    assert "t2" in out and "[completed]" in out
    assert "…" in out
    assert result.details["jobs"][0]["resource"]["resource"]["resource_state"] == "untracked"


def test_list_jobs_needs_session_context() -> None:
    assert "no active session" in _list_jobs_impl()


def test_job_output_nonblocking_peek_uses_tiny_wait(monkeypatch) -> None:
    runner = _FakeRunner([_task("t1", JobStatus.RUNNING)])
    _with_runner(monkeypatch, runner)
    result = _job_output_impl("t1", block=False)
    assert "still running" in _text(result)
    assert result.details["resource"]["resource"]["resource_state"] == "untracked"
    assert runner.await_calls == [0.001]


def test_job_output_timeout_is_milliseconds_and_capped(monkeypatch) -> None:
    runner = _FakeRunner([_task("t1", JobStatus.COMPLETED)])
    runner._tasks[0].result_text = "done!"
    _with_runner(monkeypatch, runner)
    result = _job_output_impl("t1", timeout=5_000_000)
    assert "done!" in _text(result)
    assert runner.await_calls == [600.0]

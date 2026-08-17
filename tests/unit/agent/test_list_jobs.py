"""list_jobs renders the current session's job table; job_output's
``block``/``timeout`` params (Claude Code TaskOutput shape) resolve to
sane runner waits."""
from __future__ import annotations

import asyncio

from openprogram.agent.run_control import _current_session_id
from openprogram.agent.job.types import Job, JobStatus
from openprogram.agent.types import AgentToolResult
from openprogram.programs.functions.vanilla.agent.job_output.job_output import _job_output_impl
from openprogram.programs.functions.vanilla.agent.job_stop.job_stop import job_stop
from openprogram.programs.functions.vanilla.agent.list_jobs.list_jobs import _list_jobs_impl


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
        for t in self._tasks:
            if t.id == job_id:
                return t
        return None

    def cancel_job(self, job_id, reason=None):
        for task in self._tasks:
            if task.id == job_id:
                task.status = JobStatus.CANCELLED
                task.reason_code = "cancel.user"
                return task
        return None

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
            "status": self.task.status.value,
            "resource_state": "legacy/unmetered",
            "reason_code": self.task.reason_code,
            "retryable": False,
            "capacity": {"queue_position": None},
            "budget": {"shared_remaining": {"tokens": None, "cost_usd": None}},
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
    assert "…" in out  # long prompt clipped
    assert result.details == {
        "jobs": [
            {"job_id": "t1", "status": "running", "resource": _View(runner._tasks[0]).to_dict()},
            {"job_id": "t2", "status": "completed", "resource": _View(runner._tasks[1]).to_dict()},
        ],
    }


def test_list_jobs_needs_session_context() -> None:
    assert "no active session" in _list_jobs_impl()


def test_job_output_nonblocking_peek_uses_tiny_wait(monkeypatch) -> None:
    runner = _FakeRunner([_task("t1", JobStatus.RUNNING)])
    _with_runner(monkeypatch, runner)
    result = _job_output_impl("t1", block=False)
    assert "still running" in _text(result)
    assert result.details["resource"]["resource_state"] == "legacy/unmetered"
    # Peek must not fall into the runner's falsy-timeout 60s poll path.
    assert runner.await_calls == [0.001]


def test_job_output_timeout_is_milliseconds_and_capped(monkeypatch) -> None:
    runner = _FakeRunner([_task("t1", JobStatus.COMPLETED)])
    runner._tasks[0].result_text = "done!"
    _with_runner(monkeypatch, runner)
    result = _job_output_impl("t1", timeout=5_000_000)
    assert "done!" in _text(result)
    assert runner.await_calls == [600.0]  # capped at 600000 ms → 600 s


def test_job_stop_returns_post_cancel_resource_view(monkeypatch) -> None:
    runner = _FakeRunner([_task("t1", JobStatus.RUNNING)])
    _with_runner(monkeypatch, runner)
    monkeypatch.setattr(
        "openprogram.programs.functions.vanilla.agent._ownership.check_job_ownership",
        lambda *_args: None,
    )

    result = asyncio.run(job_stop.execute(
        "call-1", {"job_id": "t1", "reason": "stop"}, None, None,
    ))

    assert result.content[0].text == "[job_stop] job_id=t1 status=cancelled"
    assert result.details["resource"]["status"] == "cancelled"
    assert result.details["resource"]["reason_code"] == "cancel.user"

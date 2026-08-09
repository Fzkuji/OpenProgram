"""list_tasks renders the current session's task table; task_output's
``block``/``timeout`` params (Claude Code TaskOutput shape) resolve to
sane runner waits."""
from __future__ import annotations

from openprogram.agent.run_control import _current_session_id
from openprogram.agent.task.types import Task, TaskStatus
from openprogram.functions.tools.agent.list_tasks.list_tasks import _list_tasks_impl
from openprogram.functions.tools.agent.task_output.task_output import _task_output_impl


def _task(tid: str, status: TaskStatus, prompt: str = "do a thing") -> Task:
    return Task(id=tid, parent_session_id="s1", prompt=prompt,
                agent_id="a1", status=status)


class _FakeRunner:
    def __init__(self, tasks):
        self._tasks = tasks
        self.await_calls = []

    def list_tasks(self, session_id, limit=None):
        return [t for t in self._tasks if t.parent_session_id == session_id]

    def await_task(self, task_id, timeout=None):
        self.await_calls.append(timeout)
        for t in self._tasks:
            if t.id == task_id:
                return t
        return None


def _with_runner(monkeypatch, runner):
    from openprogram.agent import task as task_mod
    monkeypatch.setattr(task_mod, "get_runner", lambda: runner)


def test_list_tasks_renders_rows(monkeypatch) -> None:
    runner = _FakeRunner([
        _task("t1", TaskStatus.RUNNING, "survey plan A " + "x" * 100),
        _task("t2", TaskStatus.COMPLETED),
    ])
    _with_runner(monkeypatch, runner)
    tok = _current_session_id.set("s1")
    try:
        out = _list_tasks_impl()
    finally:
        _current_session_id.reset(tok)
    assert "t1" in out and "[running]" in out
    assert "t2" in out and "[completed]" in out
    assert "…" in out  # long prompt clipped


def test_list_tasks_needs_session_context() -> None:
    assert "no active session" in _list_tasks_impl()


def test_task_output_nonblocking_peek_uses_tiny_wait(monkeypatch) -> None:
    runner = _FakeRunner([_task("t1", TaskStatus.RUNNING)])
    _with_runner(monkeypatch, runner)
    out = _task_output_impl("t1", block=False)
    assert "still running" in out
    # Peek must not fall into the runner's falsy-timeout 60s poll path.
    assert runner.await_calls == [0.001]


def test_task_output_timeout_is_milliseconds_and_capped(monkeypatch) -> None:
    runner = _FakeRunner([_task("t1", TaskStatus.COMPLETED)])
    runner._tasks[0].result_text = "done!"
    _with_runner(monkeypatch, runner)
    out = _task_output_impl("t1", timeout=5_000_000)
    assert "done!" in out
    assert runner.await_calls == [600.0]  # capped at 600000 ms → 600 s

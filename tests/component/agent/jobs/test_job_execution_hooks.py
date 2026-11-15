"""The Job records hook reaches the runner that is live when it is called."""
from __future__ import annotations

import openprogram.runtime.jobs  # noqa: F401  (registers the Job hooks)
from openprogram.runtime.execution import hooks
from openprogram.runtime.jobs.runner import shared


class _LiveRunner:
    def __init__(self) -> None:
        self.cancelled: list[tuple[str, str]] = []

    def get_job(self, job_id: str):
        return {"id": job_id, "live": True}

    def cancel_execution(self, job_id: str, *, reason: str):
        self.cancelled.append((job_id, reason))
        return {"id": job_id, "cancelled": True}


def test_records_hook_finds_the_runner_started_after_import(monkeypatch):
    records = hooks.job_records()
    assert records.live_job("job-1") is None

    runner = _LiveRunner()
    monkeypatch.setattr(shared, "_runner", runner)

    assert records.live_job("job-1") == {"id": "job-1", "live": True}
    assert records.cancel_live("job-1", reason="execution cancelled") == {
        "id": "job-1", "cancelled": True,
    }
    assert runner.cancelled == [("job-1", "execution cancelled")]


def test_records_hook_never_starts_a_runner(monkeypatch):
    monkeypatch.setattr(shared, "_runner", None)
    records = hooks.job_records()

    assert records.live_job("job-1") is None
    assert records.cancel_live("job-1", reason="execution cancelled") is None
    assert shared._runner is None

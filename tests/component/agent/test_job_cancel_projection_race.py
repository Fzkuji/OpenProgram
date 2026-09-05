from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from openprogram.agent.job import runner as runner_module
from openprogram.agent.job.store import load_job, save_job, update_job_status
from openprogram.agent.job.types import Job, JobStatus
from tests.component.agent.async_job_support import store_fixture  # noqa: F401


@pytest.mark.parametrize("outcome", [JobStatus.CANCELLED, JobStatus.COMPLETED, JobStatus.ERRORED])
def test_cancel_progress_stamp_cannot_resurrect_a_terminal_projection(
    store_fixture, monkeypatch, outcome,
) -> None:
    job = Job(
        id="cancel-race", parent_session_id="p1", prompt="work", agent_id="main",
        status=JobStatus.RUNNING,
    )
    save_job("p1", job)
    runner = object.__new__(runner_module.JobRunner)
    runner._lock = threading.RLock()
    runner._jobs = {job.id: {"session_id": "p1", "event": threading.Event()}}
    runner._execution_store = SimpleNamespace(
        get_execution=lambda _id: SimpleNamespace(status=SimpleNamespace(value="running")),
    )
    runner._request_canonical_cancel = lambda *_args: None
    runner._canonical_cancel_reason = lambda _id: "budget.runtime_exhausted"
    runner._broadcast_job_status = lambda _job: None
    runner._governor = SimpleNamespace(request_stop=lambda *_args: None)
    terminal = []

    def finish_before_stamp(session_id, job_id, status, **fields):
        # Place the finalization exactly after _cancel_single's last read and
        # before its write. No scheduler timing/retry is needed to hit the race.
        terminal.append(update_job_status(
            session_id, job_id, outcome,
            reason_code="first.outcome", result_text="immutable result",
        ))
        return update_job_status(session_id, job_id, status, **fields)

    monkeypatch.setattr(runner_module, "_store_update_status", finish_before_stamp)
    result = runner._cancel_single(job.id)

    assert len(terminal) == 1
    assert result.to_dict() == terminal[0].to_dict()
    assert load_job("p1", job.id).to_dict() == terminal[0].to_dict()
    # Unconditional illegal transitions must still fail loudly.
    with pytest.raises(ValueError, match="illegal job transition"):
        update_job_status("p1", job.id, JobStatus.RUNNING)

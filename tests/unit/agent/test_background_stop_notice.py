"""A background agent the person stopped on its own is reported to its sender.

Claude Code tells the model a background agent "was stopped by the user";
without it the sender waits for a result that never comes. A job stopped
with the turn that started it (``cancel.parent``) is not reported: nobody
is left waiting on it.
"""
from __future__ import annotations

import json

from openprogram.runtime.jobs import notices
from openprogram.runtime.jobs.types import Job, JobStatus


def _job(status: JobStatus, reason: str | None, *, wait: bool = False) -> Job:
    return Job(id="job-1", parent_session_id="s1", prompt="survey the logs", agent_id="main",
               label="log survey", status=status, reason_code=reason, wait=wait)


def _queued(tmp_path, monkeypatch, job: Job) -> list[dict]:
    path = tmp_path / "notices.json"
    monkeypatch.setattr(notices, "_path", lambda _session: path)
    notices.queue(job)
    return json.loads(path.read_text())["entries"] if path.exists() else []


def test_a_background_agent_the_person_stopped_is_reported(tmp_path, monkeypatch) -> None:
    entries = _queued(tmp_path, monkeypatch, _job(JobStatus.CANCELLED, "cancel.user"))

    assert [(e["job_id"], e.get("stopped_by_user")) for e in entries] == [("job-1", True)]
    text = notices.notice_text([_job(JobStatus.CANCELLED, "cancel.user")])
    assert text.startswith("A background agent you started was stopped.")
    assert 'Background agent "log survey" was stopped by the user (job job-1).' in text
    assert "Task: survey the logs" in text
    assert "Reply from branch" not in text and "report is attached" not in text


def test_a_job_stopped_with_its_caller_is_not_reported(tmp_path, monkeypatch) -> None:
    assert _queued(tmp_path, monkeypatch, _job(JobStatus.CANCELLED, "cancel.parent")) == []


def test_a_waited_on_job_is_not_reported(tmp_path, monkeypatch) -> None:
    assert _queued(tmp_path, monkeypatch, _job(JobStatus.CANCELLED, "cancel.user", wait=True)) == []


def test_finished_jobs_are_reported_as_before(tmp_path, monkeypatch) -> None:
    entries = _queued(tmp_path, monkeypatch, _job(JobStatus.COMPLETED, None))
    assert [e.get("stopped_by_user") for e in entries] == [None]
    assert notices.notice_text([_job(JobStatus.COMPLETED, None)]).startswith(
        "A background agent you started has finished.")

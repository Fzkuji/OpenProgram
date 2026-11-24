"""The runner's walk over every conversation reads only those with Jobs."""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.runtime.jobs.runner import shared
from openprogram.runtime.jobs.runner.service import JobRunner
from openprogram.runtime.jobs.store import save_job
from openprogram.runtime.jobs.types import Job
from openprogram.state.store.session.session_store import SessionStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch, request):
    value = SessionStore(tmp_path / "sessions")
    request.addfinalizer(value.close)
    monkeypatch.setattr(
        "openprogram.state.store.session.session_store.shared._default_store", value,
        raising=False,
    )
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: value)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    for name in ("quiet-1", "busy", "quiet-2"):
        value.create_session(name, "main", title=name)
    return value


def test_conversations_without_jobs_are_not_locked_or_read(store, monkeypatch):
    save_job("busy", Job(id="j_one", parent_session_id="busy", prompt="work", agent_id="helper"))
    read = []
    list_one = shared._store_list

    def counted(session_id, **kwargs):
        read.append(session_id)
        return list_one(session_id, **kwargs)

    monkeypatch.setattr(shared, "_store_list", counted)
    jobs = JobRunner.list_jobs(object())
    assert [job.id for job in jobs] == ["j_one"]
    assert read == ["busy"]


def test_the_notice_pass_reads_only_conversations_the_walk_found_work_in(store, monkeypatch):
    from openprogram.runtime.jobs.runner.recovery import RecoveryOperations
    from openprogram.runtime.jobs.types import JobStatus

    store.create_session("done", "main", title="done")
    save_job("busy", Job(id="j_open", parent_session_id="busy", prompt="work", agent_id="helper"))
    save_job("done", Job(id="j_done", parent_session_id="done", prompt="work", agent_id="helper",
                         status=JobStatus.COMPLETED))
    runner = type("Runner", (RecoveryOperations,), {"list_jobs": JobRunner.list_jobs})()
    runner._pass_jobs = runner.list_jobs()
    read = []
    list_one = shared._store_list
    monkeypatch.setattr(shared, "_store_list",
                        lambda session_id, **kwargs: read.append(session_id) or list_one(session_id, **kwargs))

    runner._recover_pending_notices()
    assert read == ["busy"], "a conversation whose Jobs all ended with no notice waiting is not read again"
    read.clear()
    runner._recover_pending_notices()
    assert sorted(read) == ["busy", "done"], "without a walk made in this pass every conversation is listed"

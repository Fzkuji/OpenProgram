"""Async Job store component tests."""
from __future__ import annotations

import json
import threading

import pytest

from tests.component.agent.async_job_support import (
    store_fixture,
)


def test_store_migrates_legacy_tasks_file_and_deletes_it(store_fixture):
    from openprogram.agent.job.store import load_job

    session_dir = store_fixture._session_dir("p1")
    legacy_path = session_dir / "tasks.json"
    legacy_path.write_text(json.dumps({
        "version": 1,
        "tasks": {
            "t_child": {
                "id": "t_child",
                "parent_session_id": "p1",
                "prompt": "legacy",
                "agent_id": "main",
                "parent_task_id": "t_parent",
                "status": "pending",
            },
        },
    }), encoding="utf-8")

    job = load_job("p1", "t_child")

    assert job is not None
    assert job.parent_job_id == "t_parent"
    assert not legacy_path.exists()
    migrated = json.loads((session_dir / "jobs.json").read_text(encoding="utf-8"))
    assert "tasks" not in migrated
    assert migrated["jobs"]["t_child"]["parent_job_id"] == "t_parent"
    assert "parent_task_id" not in migrated["jobs"]["t_child"]

def test_store_removes_legacy_file_after_prior_migration(store_fixture):
    from openprogram.agent.job.store import load_job

    session_dir = store_fixture._session_dir("p1")
    (session_dir / "jobs.json").write_text(json.dumps({
        "version": 1,
        "jobs": {
            "j_child": {
                "id": "j_child",
                "parent_session_id": "p1",
                "prompt": "migrated",
                "agent_id": "main",
                "status": "pending",
            },
        },
    }), encoding="utf-8")
    legacy_path = session_dir / "tasks.json"
    legacy_path.write_text('{"version": 1, "tasks": {}}', encoding="utf-8")

    assert load_job("p1", "j_child") is not None
    assert not legacy_path.exists()

def test_store_keeps_legacy_file_when_canonical_file_is_invalid(store_fixture):
    from openprogram.agent.job.store import load_job

    session_dir = store_fixture._session_dir("p1")
    (session_dir / "jobs.json").write_text("not json", encoding="utf-8")
    legacy_path = session_dir / "tasks.json"
    legacy_path.write_text('{"version": 1, "tasks": {}}', encoding="utf-8")

    assert load_job("p1", "missing") is None
    assert legacy_path.exists()

def test_state_machine_legal_edges():
    from openprogram.agent.job.types import JobStatus, can_transition
    assert can_transition(JobStatus.PENDING, JobStatus.QUEUED)
    assert can_transition(JobStatus.QUEUED, JobStatus.RUNNING)
    assert can_transition(JobStatus.RUNNING, JobStatus.COMPLETED)
    assert can_transition(JobStatus.RUNNING, JobStatus.CANCELLED)
    assert can_transition(JobStatus.RUNNING, JobStatus.ERRORED)
    assert can_transition(JobStatus.PENDING, JobStatus.CANCELLED)

def test_state_machine_illegal_edges():
    from openprogram.agent.job.types import JobStatus, can_transition
    # Terminal states are absorbing.
    assert not can_transition(JobStatus.COMPLETED, JobStatus.RUNNING)
    assert not can_transition(JobStatus.CANCELLED, JobStatus.RUNNING)
    assert not can_transition(JobStatus.ERRORED, JobStatus.COMPLETED)
    # Can't skip back to earlier non-terminal.
    assert not can_transition(JobStatus.RUNNING, JobStatus.PENDING)
    assert not can_transition(JobStatus.QUEUED, JobStatus.PENDING)

def test_job_to_dict_roundtrip():
    from openprogram.agent.job.types import Job, JobStatus
    t = Job(
        id="t_abc", parent_session_id="s1", prompt="hi", agent_id="main",
        label="alpha", subject="alpha",
    )
    d = t.to_dict()
    assert d["status"] == "pending"
    t2 = Job.from_dict(d)
    assert t2.id == "t_abc"
    assert t2.status == JobStatus.PENDING
    assert t2.label == "alpha"

def test_store_save_load(store_fixture):
    from openprogram.agent.job.types import Job, JobStatus
    from openprogram.agent.job.store import save_job, load_job, list_jobs

    t = Job(
        id="t_one", parent_session_id="p1", prompt="x", agent_id="main",
        subject="one",
    )
    save_job("p1", t)
    loaded = load_job("p1", "t_one")
    assert loaded is not None
    assert loaded.id == "t_one"
    assert loaded.status == JobStatus.PENDING

    rows = list_jobs("p1")
    assert len(rows) == 1
    assert rows[0].id == "t_one"

def test_store_update_status_legal_transition(store_fixture):
    from openprogram.agent.job.types import Job, JobStatus
    from openprogram.agent.job.store import save_job, update_job_status
    t = Job(id="t_a", parent_session_id="p1", prompt="x", agent_id="main")
    save_job("p1", t)
    updated = update_job_status("p1", "t_a", JobStatus.QUEUED)
    assert updated.status == JobStatus.QUEUED
    assert updated.queued_at is not None


def test_reciprocal_linked_idempotent_updates_do_not_deadlock(
    store_fixture, monkeypatch,
):
    from openprogram.agent.job import store as job_store
    from openprogram.agent.job.store import save_job, update_job_status
    from openprogram.agent.job.types import Job, JobStatus

    store_fixture.create_session("p2", "main", title="peer")
    save_job("p1", Job(
        id="j-ab", parent_session_id="p1", prompt="ab", agent_id="main",
        relation="linked", creates_agent=False, caller_session_id="p2",
        origin_turn_id="p2-origin", status=JobStatus.RUNNING,
    ))
    save_job("p2", Job(
        id="j-ba", parent_session_id="p2", prompt="ba", agent_id="main",
        relation="linked", creates_agent=False, caller_session_id="p1",
        origin_turn_id="p1-origin", status=JobStatus.RUNNING,
    ))
    original_write = job_store._write_raw
    barrier = threading.Barrier(2)
    local = threading.local()

    def synchronized_write(path, jobs):
        original_write(path, jobs)
        if not getattr(local, "waited", False):
            local.waited = True
            barrier.wait(timeout=2)

    monkeypatch.setattr(job_store, "_write_raw", synchronized_write)
    errors = []

    def update(session_id, job_id):
        try:
            update_job_status(session_id, job_id, JobStatus.RUNNING)
        except Exception as exc:  # pragma: no cover - assertion reports it
            errors.append(exc)

    threads = [
        threading.Thread(target=update, args=("p1", "j-ab")),
        threading.Thread(target=update, args=("p2", "j-ba")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert not errors
    assert not any(thread.is_alive() for thread in threads)

def test_store_update_status_illegal_transition_raises(store_fixture):
    from openprogram.agent.job.types import Job, JobStatus
    from openprogram.agent.job.store import save_job, update_job_status
    t = Job(
        id="t_b", parent_session_id="p1", prompt="x", agent_id="main",
        status=JobStatus.COMPLETED,
    )
    save_job("p1", t)
    with pytest.raises(ValueError):
        update_job_status("p1", "t_b", JobStatus.RUNNING)

def test_store_reconcile_orphans_flips_running_to_errored(store_fixture):
    from openprogram.agent.job.types import Job, JobStatus
    from openprogram.agent.job.store import save_job, load_job, reconcile_orphans
    t = Job(
        id="t_orphan", parent_session_id="p1", prompt="x", agent_id="main",
        status=JobStatus.RUNNING,
    )
    save_job("p1", t)
    n = reconcile_orphans()
    assert n == 1
    cur = load_job("p1", "t_orphan")
    assert cur.status == JobStatus.ERRORED
    assert "died" in (cur.error or "")

def test_store_reconcile_orphans_preserves_terminal(store_fixture):
    from openprogram.agent.job.types import Job, JobStatus
    from openprogram.agent.job.store import save_job, load_job, reconcile_orphans
    t = Job(
        id="t_done", parent_session_id="p1", prompt="x", agent_id="main",
        status=JobStatus.COMPLETED,
    )
    save_job("p1", t)
    reconcile_orphans()
    cur = load_job("p1", "t_done")
    assert cur.status == JobStatus.COMPLETED

def test_store_reconcile_orphans_legacy_only_preserves_governed_jobs(store_fixture):
    from openprogram.agent.job.types import Job, JobStatus
    from openprogram.agent.job.store import save_job, load_job, reconcile_orphans
    save_job(
        "p1",
        Job(
            id="legacy", parent_session_id="p1", prompt="x", agent_id="main",
            status=JobStatus.RUNNING,
        ),
    )
    save_job(
        "p1",
        Job(
            id="governed", parent_session_id="p1", prompt="x", agent_id="main",
            status=JobStatus.QUEUED, admission_id="adm_governed",
        ),
    )

    assert reconcile_orphans(legacy_only=True) == 1
    assert load_job("p1", "legacy").status == JobStatus.ERRORED
    assert load_job("p1", "governed").status == JobStatus.QUEUED

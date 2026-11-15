"""governance finalization tests."""
from __future__ import annotations
import json
import os
import sqlite3
from openprogram.runtime.jobs.types import JobStatus
from types import SimpleNamespace
from ._support import (
    Job,
    ResourceGovernor,
    ResourceLimits,
    UsageLedger,
    pytest,
    resolve_resource_limits,
)


def _columns(row, *names):
    return tuple(row[name] for name in names)


def test_pending_terminal_projection_blocks_dispatch_claim(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="terminal-pending", parent_session_id="s1", prompt="p", agent_id="a")
    assert governor.admit_job(job, persist=lambda _job: None).accepted
    assert governor.enqueue_terminal_projection(
        job.id,
        {
            "status": JobStatus.CANCELLED.value,
            "head_id": None,
            "result_text": None,
            "error": "cancelled before pickup",
            "reason_code": "cancel.user",
        },
    )

    assert governor.claim_next(owner_instance_id="worker") is None
    row = ledger.run_tables().handle.first("job_admissions", job_id=job.id)
    assert (row["state"], row["dispatch_ready"]) == ("queued", 0)



def test_reconcile_finalizes_or_rolls_back_preparing_and_releases_missing_queue(
    tmp_path,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=2)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    jobs = {}
    for job_id in ("preparing_present", "preparing_missing", "queued_missing"):
        job = Job(
            id=job_id, parent_session_id="s1", prompt=job_id, agent_id="a",
        )
        governor.admit_job(job, persist=lambda accepted: jobs.setdefault(
            accepted.id, accepted,
        ))
    jobs.pop("preparing_missing")
    jobs.pop("queued_missing")
    with ledger.run_tables().transaction() as tables:
        tables.update(
            "job_admissions",
            {"state": "preparing"},
            job_id=("preparing_present", "preparing_missing"),
        )

    result = governor.reconcile(
        job_lookup=lambda _sid, job_id: jobs.get(job_id),
        mark_worker_lost=lambda _sid, _job_id: None,
        owner_is_alive=lambda _owner: False,
    )

    rows = {
        row["job_id"]: (row["state"], row["reason_code"])
        for row in ledger.run_tables().handle.rows("job_admissions")
    }
    assert result.finalized_preparing == 1
    assert result.rolled_back_preparing == 1
    assert result.released_missing == 1
    assert "preparing_missing" not in rows
    assert rows == {
        "preparing_present": ("queued", None),
        "queued_missing": ("released", "error.job_missing"),
    }



def test_worker_lost_revokes_generation_before_terminal_store_mutation(
    tmp_path,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="live", parent_session_id="s1", prompt="live", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    stale_owner = f"worker_{os.getpid()}_stale"
    claim = governor.claim_next(owner_instance_id=stale_owner)
    mutations: list[str] = []

    result = governor.reconcile(
        job_lookup=lambda _sid, _job_id: job,
        mark_worker_lost=lambda _sid, _job_id: mutations.append("worker_lost"),
        owner_is_alive=lambda _owner: False,
        now=21,
    )
    stale_finalized = governor.finalize_job(
        "live", "completed",
        owner_instance_id=stale_owner,
        lease_generation=claim.lease_generation,
        terminal_fields={
            "status": "completed", "head_id": None, "result_text": None,
            "error": None, "reason_code": "completed",
        },
        mutate=lambda _fields: mutations.append("completed"),
    )

    row = ledger.run_tables().handle.first("job_admissions", job_id="live")
    assert result.released_worker_lost == 1
    assert stale_finalized is False
    assert mutations == ["worker_lost"]
    assert (row["state"], row["owner_instance_id"], row["lease_generation"]) == ("released", None, claim.lease_generation + 1)



def test_finalize_job_writes_job_store_outside_sqlite_transaction(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="live", parent_session_id="s1", prompt="live", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    claim = governor.claim_next(owner_instance_id="worker")

    assert governor.finalize_job(
        job.id,
        "completed",
        owner_instance_id="worker",
        lease_generation=claim.lease_generation,
        terminal_fields={
            "status": "completed",
            "head_id": None,
            "result_text": None,
            "error": None,
            "reason_code": "completed",
        },
        mutate=lambda _fields: (
            ledger.connection().in_transaction is False
            or pytest.fail("JobStore write ran inside SQLite transaction")
        ),
    )



def test_release_job_keeps_admission_while_finalization_is_pending(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="live", parent_session_id="s1", prompt="live", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    claim = governor.claim_next(owner_instance_id="worker")
    fields = {
        "status": "completed", "head_id": "head_1", "result_text": "done",
        "error": None, "reason_code": "completed",
    }

    with pytest.raises(RuntimeError, match="job store unavailable"):
        governor.finalize_job(
            job.id,
            "completed",
            owner_instance_id="worker",
            lease_generation=claim.lease_generation,
            terminal_fields=fields,
            mutate=lambda _fields: (_ for _ in ()).throw(
                RuntimeError("job store unavailable")
            ),
        )

    assert governor.release_job(
        job.id,
        "error.execution",
        owner_instance_id="worker",
        lease_generation=claim.lease_generation,
    ) is False
    assert _columns(
        ledger.run_tables().handle.first("job_admissions", job_id=job.id),
        "state", "owner_instance_id",
    ) == ("live", "worker")
    assert ledger.run_tables().handle.first("job_finalizations", job_id=job.id)["state"] == "pending"

    job.status = JobStatus.RUNNING
    failures = []
    for _ in range(2):
        result = governor.reconcile(
            job_lookup=lambda _sid, _job_id: job,
            write_terminal=lambda _sid, _job_id, _fields: failures.append(1)
            or (_ for _ in ()).throw(RuntimeError("job store unavailable")),
            mark_worker_lost=lambda _sid, _job_id: pytest.fail(
                "pending finalization must retain its admission"
            ),
            owner_is_alive=lambda _owner: False,
            now=1,
        )
        assert result.finalization_conflicts == 0
        assert ledger.run_tables().handle.first("job_admissions", job_id=job.id)["state"] == "live"
        assert ledger.run_tables().handle.first("job_finalizations", job_id=job.id)["state"] == "pending"

    def apply_terminal(_session_id: str, _job_id: str, staged: dict) -> None:
        job.status = JobStatus(staged["status"])
        for name, value in staged.items():
            if name != "status":
                setattr(job, name, value)

    governor.reconcile(
        job_lookup=lambda _sid, _job_id: job,
        write_terminal=apply_terminal,
        mark_worker_lost=lambda _sid, _job_id: pytest.fail(
            "recovered finalization must not use worker-lost handling"
        ),
        owner_is_alive=lambda _owner: False,
        now=2,
    )
    assert len(failures) == 2
    assert ledger.run_tables().handle.first("job_admissions", job_id=job.id)["state"] == "released"
    assert ledger.run_tables().handle.first("job_finalizations", job_id=job.id)["state"] == "completed"



def test_requeue_keeps_pending_finalization_under_its_original_fence(
    tmp_path,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="live", parent_session_id="s1", prompt="live", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    claim = governor.claim_next(owner_instance_id="worker")
    fields = {
        "status": "completed",
        "head_id": "head_1",
        "result_text": "done",
        "error": None,
        "reason_code": "completed",
    }

    with pytest.raises(RuntimeError, match="job store unavailable"):
        governor.finalize_job(
            job.id,
            "completed",
            owner_instance_id="worker",
            lease_generation=claim.lease_generation,
            terminal_fields=fields,
            mutate=lambda _fields: (_ for _ in ()).throw(
                RuntimeError("job store unavailable")
            ),
        )

    assert governor.requeue_job(
        job.id,
        owner_instance_id="worker",
        lease_generation=claim.lease_generation,
    ) is False
    assert _columns(
        ledger.run_tables().handle.first("job_admissions", job_id=job.id),
        "state", "owner_instance_id", "lease_generation",
    ) == ("live", "worker", claim.lease_generation)

    job.status = JobStatus.RUNNING

    def apply_terminal(_session_id: str, _job_id: str, staged: dict) -> None:
        job.status = JobStatus(staged["status"])
        for name, value in staged.items():
            if name != "status":
                setattr(job, name, value)

    governor.reconcile(
        job_lookup=lambda _sid, _job_id: job,
        write_terminal=apply_terminal,
        mark_worker_lost=lambda _sid, _job_id: pytest.fail(
            "pending finalization must retain its original fence"
        ),
        owner_is_alive=lambda _owner: False,
        now=1,
    )
    assert job.status == JobStatus.COMPLETED
    assert ledger.run_tables().handle.first("job_admissions", job_id=job.id)["state"] == "released"
    assert ledger.run_tables().handle.first("job_finalizations", job_id=job.id)["state"] == "completed"



def test_terminal_barrier_fences_borrowed_activation_until_reconcile(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    parent = Job(id="parent", parent_session_id="s1", prompt="p", agent_id="a")
    governor.admit_job(parent, persist=lambda _job: None)
    parent_claim = governor.claim_next(owner_instance_id="worker")
    assert parent_claim is not None
    child = Job(
        id="child", parent_session_id="s1", parent_job_id=parent.id,
        prompt="c", agent_id="a",
    )
    governor.admit_job(
        child,
        persist=lambda _job: None,
        dispatch_ready=False,
        borrowed_claim=(parent.id, "worker", parent_claim.lease_generation),
    )

    assert governor.block_dispatch(child.id)
    assert not governor.start_borrowed_job(
        child.id,
        parent_job_id=parent.id,
        owner_instance_id="worker",
        lease_generation=parent_claim.lease_generation,
    )
    assert governor.unblock_terminal_dispatch(
        child.id,
        expected_state="queued",
        expected_owner_instance_id=None,
    )
    assert _columns(
        ledger.run_tables().handle.first("job_admissions", job_id=child.id),
        "dispatch_ready", "terminal_blocked",
    ) == (0, 0)
    assert governor.start_borrowed_job(
        child.id,
        parent_job_id=parent.id,
        owner_instance_id="worker",
        lease_generation=parent_claim.lease_generation,
    )



def test_terminal_barrier_reconciles_queued_admission_to_exact_owner(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="job", parent_session_id="s1", prompt="p", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    assert governor.block_dispatch(job.id)
    fence = governor.admission_fence(job.id)
    assert fence == (None, 0)
    assert governor.restore_terminal_dispatch_claim(
        job.id,
        expected_state="queued",
        expected_owner_instance_id=None,
        expected_lease_generation=fence[1],
        target_state="live",
        owner_instance_id="canonical-owner",
        lease_generation=4,
    )
    assert _columns(
        ledger.run_tables().handle.first("job_admissions", job_id=job.id),
        "state", "owner_instance_id", "lease_generation", "terminal_blocked",
    ) == ("live", "canonical-owner", 4, 0)



def test_terminal_barrier_identity_and_expiry_gate_claim_recovery(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="job", parent_session_id="s1", prompt="p", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    assert governor.block_dispatch(
        job.id, command_id="cancel-1", phase="prepared",
    )
    assert governor.claim_next(owner_instance_id="worker") is None
    assert not governor.unblock_terminal_dispatch(job.id)
    assert not governor.mark_terminal_dispatch_recovery(
        job.id, command_id="cancel-wrong",
    )
    assert governor.mark_terminal_dispatch_recovery(
        job.id, command_id="cancel-1",
    )
    assert not governor.unblock_terminal_dispatch(
        job.id, expected_lease_generation=99,
    )
    assert governor.unblock_terminal_dispatch(
        job.id,
        expected_state="queued",
        expected_owner_instance_id=None,
        expected_lease_generation=0,
    )
    claim = governor.claim_next(owner_instance_id="worker")
    assert claim is not None
    assert governor.release_job(
        job.id,
        owner_instance_id="worker",
        lease_generation=claim.lease_generation,
    )



def test_terminal_barrier_expiry_is_clock_driven(tmp_path, monkeypatch):
    import openprogram.runtime.jobs.resource_governance.finalization as governance_module

    clock = [100.0]
    monkeypatch.setattr(
        governance_module, "time", SimpleNamespace(time=lambda: clock[0]),
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="expiry", parent_session_id="s1", prompt="p", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    assert governor.block_dispatch(
        job.id, command_id="cancel-expiry", phase="prepared",
    )
    assert not governor.unblock_terminal_dispatch(
        job.id, expected_lease_generation=0,
    )
    clock[0] = 131.0
    assert governor.unblock_terminal_dispatch(
        job.id, expected_lease_generation=0,
    )



def test_terminal_barrier_preserves_deferred_dispatch_ready_and_cleans_on_release(
    tmp_path,
):
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="deferred", parent_session_id="s1", prompt="p", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None, dispatch_ready=False)
    assert governor.block_dispatch(
        job.id, command_id="cancel-deferred", phase="recovery",
    )
    assert governor.unblock_terminal_dispatch(
        job.id, expected_lease_generation=0,
    )
    assert _columns(
        ledger.run_tables().handle.first("job_admissions", job_id=job.id),
        "dispatch_ready", "terminal_blocked",
    ) == (0, 0)
    assert governor.claim_next(owner_instance_id="worker") is None
    assert governor.block_dispatch(
        job.id, command_id="cancel-deferred", phase="recovery",
    )
    assert governor.release_job(job.id)
    assert _columns(
        ledger.run_tables().handle.first("job_admissions", job_id=job.id),
        "state", "terminal_blocked", "terminal_block_command_id", "terminal_block_phase", "terminal_block_expires_at", "terminal_block_prior_dispatch_ready",
    ) == ("released", 0, None, None, None, None)



def test_legacy_admission_schema_migrates_terminal_barrier_columns(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE job_admissions (
            admission_id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL,
            session_id TEXT NOT NULL, parent_job_id TEXT,
            caller_session_id TEXT, caller_turn_id TEXT,
            creates_agent INTEGER NOT NULL, request_fingerprint TEXT NOT NULL,
            budget_scope_id TEXT NOT NULL, dispatch_ready INTEGER NOT NULL DEFAULT 1,
            borrowed_parent_job_id TEXT, resume_parent_msg_id TEXT,
            state TEXT NOT NULL, admitted_seq INTEGER NOT NULL,
            owner_instance_id TEXT, lease_generation INTEGER NOT NULL DEFAULT 0,
            lease_expires_at REAL, created_at REAL NOT NULL, started_at REAL,
            last_activity_at REAL, released_at REAL, reason_code TEXT
        )"""
    )
    conn.execute(
        """INSERT INTO job_admissions (
            admission_id, job_id, session_id, creates_agent,
            request_fingerprint, budget_scope_id, state, admitted_seq, created_at
        ) VALUES ('a', 'old', 's1', 0, 'f', 'scope', 'queued', 1, 1)"""
    )
    conn.commit()
    conn.close()
    ledger = UsageLedger(path)
    assert ResourceGovernor(ledger).admission_state("old") is not None
    row = ledger.run_tables().handle.first("job_admissions", job_id="old")
    assert row["terminal_blocked"] == 0
    assert row["terminal_block_command_id"] is None
    assert row["terminal_block_phase"] is None
    assert row["terminal_block_expires_at"] is None
    assert row["terminal_block_prior_dispatch_ready"] is None



def test_borrowed_pending_finalization_blocks_direct_and_orphan_release(
    tmp_path,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    parent = Job(id="parent", parent_session_id="s1", prompt="p", agent_id="a")
    governor.admit_job(parent, persist=lambda _job: None)
    claim = governor.claim_next(owner_instance_id="worker")
    child = Job(
        id="child", parent_session_id="s1", parent_job_id=parent.id,
        prompt="c", agent_id="a",
    )
    governor.admit_job(
        child,
        persist=lambda _job: None,
        dispatch_ready=False,
        borrowed_claim=(parent.id, "worker", claim.lease_generation),
    )
    assert governor.start_borrowed_job(
        child.id,
        parent_job_id=parent.id,
        owner_instance_id="worker",
        lease_generation=claim.lease_generation,
    )

    with pytest.raises(RuntimeError, match="job store unavailable"):
        governor.finalize_borrowed_job(
            child.id,
            parent_job_id=parent.id,
            owner_instance_id="worker",
            lease_generation=claim.lease_generation,
            reason_code="completed",
            terminal_fields={
                "status": "completed", "head_id": None, "result_text": "done",
                "error": None, "reason_code": "completed",
            },
            mutate=lambda _fields: (_ for _ in ()).throw(
                RuntimeError("job store unavailable")
            ),
        )

    assert governor.release_borrowed_job(
        child.id,
        parent_job_id=parent.id,
        owner_instance_id="worker",
        lease_generation=claim.lease_generation,
        reason_code="error.borrowed_cleanup",
    ) is False
    assert governor.release_job(
        parent.id,
        owner_instance_id="worker",
        lease_generation=claim.lease_generation,
    ) is True
    assert governor.release_orphaned_borrowed_jobs() == []
    assert _columns(
        ledger.run_tables().handle.first("job_admissions", job_id=child.id),
        "state", "owner_instance_id",
    ) == ("queued", "worker")
    assert ledger.run_tables().handle.first("job_finalizations", job_id=child.id)["state"] == "pending"



@pytest.mark.parametrize("crash_after_write", [False, True])
def test_reconcile_finishes_pending_terminal_intent_without_worker_lost(
    tmp_path, crash_after_write,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="live", parent_session_id="s1", prompt="live", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    claim = governor.claim_next(owner_instance_id="worker")
    job.status = JobStatus.RUNNING
    fields = {
        "status": "completed",
        "head_id": "head_1",
        "result_text": "done",
        "error": None,
        "reason_code": "completed",
    }
    writes: list[str] = []

    def apply_terminal(_session_id: str, _job_id: str, terminal_fields: dict) -> Job:
        writes.append("write")
        job.status = JobStatus(terminal_fields["status"])
        for name, value in terminal_fields.items():
            if name != "status":
                setattr(job, name, value)
        return job

    def crash(staged_fields: dict) -> None:
        if crash_after_write:
            apply_terminal("s1", job.id, staged_fields)
        raise RuntimeError("simulated process crash")

    with pytest.raises(RuntimeError, match="simulated process crash"):
        governor.finalize_job(
            job.id,
            "completed",
            owner_instance_id="worker",
            lease_generation=claim.lease_generation,
            terminal_fields=fields,
            mutate=crash,
        )
    writes_before_reconcile = len(writes)
    worker_lost: list[str] = []

    result = governor.reconcile(
        job_lookup=lambda _sid, _job_id: job,
        write_terminal=apply_terminal,
        mark_worker_lost=lambda _sid, job_id: worker_lost.append(job_id),
        owner_is_alive=lambda _owner: False,
        now=1,
    )

    assert job.status == JobStatus.COMPLETED
    assert job.result_text == "done"
    assert writes_before_reconcile == int(crash_after_write)
    assert writes == ["write"]
    assert worker_lost == []
    assert result.released_worker_lost == 0
    assert ledger.run_tables().handle.first("job_admissions", job_id=job.id)["state"] == "released"
    assert ledger.run_tables().handle.first("job_finalizations", job_id=job.id)["state"] == "completed"



def test_reconcile_reports_conflict_for_different_existing_terminal_payload(
    tmp_path,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="live", parent_session_id="s1", prompt="live", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    claim = governor.claim_next(owner_instance_id="worker")
    staged = {
        "status": "completed", "head_id": "completed_head",
        "result_text": "done", "error": None, "reason_code": "completed",
    }

    with pytest.raises(RuntimeError, match="crash after stage"):
        governor.finalize_job(
            job.id,
            "completed",
            owner_instance_id="worker",
            lease_generation=claim.lease_generation,
            terminal_fields=staged,
            mutate=lambda _fields: (_ for _ in ()).throw(
                RuntimeError("crash after stage")
            ),
        )
    job.status = JobStatus.ERRORED
    job.head_id = "error_head"
    job.result_text = None
    job.error = "provider failed"
    job.reason_code = "error.execution"

    for _ in range(2):
        result = governor.reconcile(
            job_lookup=lambda _sid, _job_id: job,
            write_terminal=lambda *_args: pytest.fail(
                "an existing terminal job must never be overwritten"
            ),
            mark_worker_lost=lambda *_args: pytest.fail(
                "a finalization conflict must retain the admission"
            ),
            owner_is_alive=lambda _owner: False,
            now=1,
        )
        assert result.finalization_conflicts == 1
        assert job.status == JobStatus.ERRORED
        assert job.error == "provider failed"
        assert ledger.run_tables().handle.first("job_admissions", job_id=job.id)["state"] == "live"
        assert ledger.run_tables().handle.first("job_finalizations", job_id=job.id)["state"] == "pending"



def test_finalization_intent_rejects_stale_fence_competitor_and_payload_change(
    tmp_path,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="live", parent_session_id="s1", prompt="live", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    claim = governor.claim_next(owner_instance_id="worker")
    original = {
        "status": "completed",
        "head_id": "head_1",
        "result_text": "first",
        "error": None,
        "reason_code": "completed",
    }
    competing = {**original, "result_text": "second"}
    mutations: list[str] = []

    assert governor.finalize_job(
        job.id,
        "completed",
        owner_instance_id="stale",
        lease_generation=claim.lease_generation,
        terminal_fields=original,
        mutate=lambda _fields: mutations.append("stale"),
    ) is False
    assert ledger.run_tables().handle.count("job_finalizations") == 0

    with pytest.raises(RuntimeError, match="crash after stage"):
        governor.finalize_job(
            job.id,
            "completed",
            owner_instance_id="worker",
            lease_generation=claim.lease_generation,
            terminal_fields=original,
            mutate=lambda _fields: (_ for _ in ()).throw(
                RuntimeError("crash after stage")
            ),
        )
    assert governor.finalize_job(
        job.id,
        "completed",
        owner_instance_id="worker",
        lease_generation=claim.lease_generation,
        terminal_fields=competing,
        mutate=lambda _fields: mutations.append("different payload"),
    ) is False
    with ledger.run_tables().transaction() as tables:
        tables.update(
            "job_admissions",
            {"owner_instance_id": "competitor", "lease_generation": lambda row: row["lease_generation"] + 1},
            job_id=job.id,
        )
    assert governor.finalize_job(
        job.id,
        "completed",
        owner_instance_id="competitor",
        lease_generation=claim.lease_generation + 1,
        terminal_fields=original,
        mutate=lambda _fields: mutations.append("competing fence"),
    ) is False

    row = ledger.run_tables().handle.first("job_finalizations", job_id=job.id)
    assert (row["owner_instance_id"], row["lease_generation"]) == ("worker", claim.lease_generation)
    assert json.loads(row["fields_json"]) == {"version": 1, "fields": original}
    assert row["state"] == "pending"
    assert mutations == []



def test_stopping_finalize_keeps_claim_when_store_mutation_fails(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="stopping", parent_session_id="s1", prompt="p", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    claim = governor.claim_next(owner_instance_id="worker")
    governor.request_stop(job.id, "cancel.user")

    def fail_mutation(_fields: dict) -> None:
        raise RuntimeError("job store unavailable")

    with pytest.raises(RuntimeError, match="job store unavailable"):
        governor.finalize_stopping_job(
            job.id,
            owner_instance_id="worker",
            lease_generation=claim.lease_generation,
            reason_code="cancel.user",
            terminal_fields={
                "status": "cancelled", "head_id": None, "result_text": None,
                "error": "cancelled", "reason_code": "cancel.user",
            },
            mutate=fail_mutation,
        )

    row = ledger.run_tables().handle.first("job_admissions", job_id=job.id)
    assert (row["state"], row["reason_code"]) == ("stopping", "cancel.user")



def test_stopping_finalize_mutates_then_releases_current_claim(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="stopping", parent_session_id="s1", prompt="p", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    claim = governor.claim_next(owner_instance_id="worker")
    governor.request_stop(job.id, "cancel.user")
    mutations: list[str | None] = []

    finalized = governor.finalize_stopping_job(
        job.id,
        owner_instance_id="worker",
        lease_generation=claim.lease_generation,
        reason_code="cancel.user",
        terminal_fields={
            "status": "cancelled", "head_id": None, "result_text": None,
            "error": "cancelled", "reason_code": "cancel.user",
        },
        mutate=lambda fields: mutations.append(fields["reason_code"]),
    )

    row = ledger.run_tables().handle.first("job_admissions", job_id=job.id)
    assert finalized is True
    assert mutations == ["cancel.user"]
    assert (row["state"], row["reason_code"]) == ("released", "cancel.user")


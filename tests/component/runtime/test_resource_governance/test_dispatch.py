"""governance dispatch tests."""
from __future__ import annotations
from ._support import (
    Job,
    ResourceGovernor,
    ResourceLimits,
    UsageLedger,
    os,
    resolve_resource_limits,
)


def test_stopping_keeps_live_capacity_until_worker_release(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_live_per_session=1), scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    for job_id in ("t_1", "t_2"):
        governor.admit_job(
            Job(id=job_id, parent_session_id="s1", prompt=job_id, agent_id="a"),
            persist=lambda _job: None,
        )

    assert governor.try_start("t_1", owner_instance_id="worker") is True
    assert governor.try_start("t_2", owner_instance_id="worker") is False
    governor.request_stop("t_1", "cancel.user")
    assert governor.try_start("t_2", owner_instance_id="worker") is False
    generation = ledger.connection().execute(
        "SELECT lease_generation FROM job_admissions WHERE job_id = 't_1'"
    ).fetchone()[0]
    governor.release_job(
        "t_1", "cancel.user", owner_instance_id="worker",
        lease_generation=generation,
    )
    assert governor.try_start("t_2", owner_instance_id="worker") is True



def test_stop_request_does_not_overwrite_released_reason(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="finished", parent_session_id="s1", prompt="p", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    assert governor.try_start(job.id, owner_instance_id="worker") is True
    generation = ledger.connection().execute(
        "SELECT lease_generation FROM job_admissions WHERE job_id = ?",
        (job.id,),
    ).fetchone()[0]
    assert governor.release_job(
        job.id,
        "completed",
        owner_instance_id="worker",
        lease_generation=generation,
    ) is True

    governor.request_stop(job.id, "cancel.user")

    row = ledger.connection().execute(
        "SELECT state, reason_code FROM job_admissions WHERE job_id = ?",
        (job.id,),
    ).fetchone()
    assert tuple(row) == ("released", "completed")



def test_claim_next_skips_older_job_from_saturated_session(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_live_per_session=1), scheduler_capacity=2,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    for job_id, session_id in (
        ("s1_first", "s1"), ("s1_second", "s1"), ("s2_first", "s2"),
    ):
        governor.admit_job(
            Job(
                id=job_id, parent_session_id=session_id,
                prompt=job_id, agent_id="a",
            ),
            persist=lambda _job: None,
        )

    first = governor.claim_next(owner_instance_id="worker")
    second = governor.claim_next(owner_instance_id="worker")

    assert (first.job_id, first.session_id) == ("s1_first", "s1")
    assert (second.job_id, second.session_id) == ("s2_first", "s2")
    states = {
        row["job_id"]: row["state"]
        for row in ledger.connection().execute(
            "SELECT job_id, state FROM job_admissions"
        )
    }
    assert states == {
        "s1_first": "live", "s1_second": "queued", "s2_first": "live",
    }



def test_claim_next_honors_two_live_jobs_per_session(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_live_per_session=2), scheduler_capacity=3,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    for job_id, session_id in (
        ("s1_first", "s1"), ("s1_second", "s1"), ("s2_first", "s2"),
    ):
        governor.admit_job(
            Job(
                id=job_id, parent_session_id=session_id,
                prompt=job_id, agent_id="a",
            ),
            persist=lambda _job: None,
        )

    first = governor.claim_next(owner_instance_id="worker")
    second = governor.claim_next(owner_instance_id="worker")

    assert (first.job_id, first.session_id) == ("s1_first", "s1")
    assert (second.job_id, second.session_id) == ("s1_second", "s1")
    assert ledger.connection().execute(
        "SELECT state FROM job_admissions WHERE job_id = 's1_second'"
    ).fetchone()[0] == "live"



def test_claim_next_requires_calling_process_to_hold_worker_lock(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        "openprogram.worker.lock.is_held_by", lambda _pid: False,
        raising=False,
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(
        ledger, limit_resolver=lambda _sid, _job: resolved,
    )
    governor.admit_job(
        Job(id="only", parent_session_id="s1", prompt="x", agent_id="a"),
        persist=lambda _job: None,
    )

    assert governor.claim_next(
        owner_instance_id=f"worker_{os.getpid()}_test",
    ) is None
    assert ledger.connection().execute(
        "SELECT state FROM job_admissions WHERE job_id = 'only'"
    ).fetchone()[0] == "queued"



def test_claim_next_rejects_owner_id_for_another_process(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(
        ledger, limit_resolver=lambda _sid, _job: resolved,
    )
    governor.admit_job(
        Job(id="only", parent_session_id="s1", prompt="x", agent_id="a"),
        persist=lambda _job: None,
    )

    assert governor.claim_next(
        owner_instance_id=f"worker_{os.getpid() + 1}_not_owner",
    ) is None



def test_lowered_live_limit_preserves_existing_live_and_blocks_new_claim(
    tmp_path,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    configured = {"live": 3}

    def resolve(_session_id, _job):
        return resolve_resource_limits(
            ResourceLimits(max_live_per_session=configured["live"]),
            scheduler_capacity=3,
        )

    governor = ResourceGovernor(ledger, limit_resolver=resolve)
    for job_id in ("first", "second", "third"):
        governor.admit_job(
            Job(
                id=job_id, parent_session_id="s1",
                prompt=job_id, agent_id="a",
            ),
            persist=lambda _job: None,
        )

    first = governor.claim_next(owner_instance_id="worker")
    second = governor.claim_next(owner_instance_id="worker")
    assert [first.job_id, second.job_id] == ["first", "second"]

    configured["live"] = 1
    assert governor.claim_next(owner_instance_id="worker") is None
    assert dict(ledger.connection().execute(
        "SELECT state, COUNT(*) AS count FROM job_admissions GROUP BY state"
    ).fetchall()) == {"live": 2, "queued": 1}



def test_claim_next_cannot_be_claimed_twice_across_governors(tmp_path) -> None:
    ledger_path = tmp_path / "usage.db"
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    first_governor = ResourceGovernor(
        UsageLedger(ledger_path), limit_resolver=lambda _sid, _job: resolved,
    )
    second_governor = ResourceGovernor(
        UsageLedger(ledger_path), limit_resolver=lambda _sid, _job: resolved,
    )
    first_governor.admit_job(
        Job(id="only", parent_session_id="s1", prompt="only", agent_id="a"),
        persist=lambda _job: None,
    )

    first_owner = f"worker_{os.getpid()}_first"
    second_owner = f"worker_{os.getpid()}_second"
    claim = first_governor.claim_next(owner_instance_id=first_owner)

    assert claim.job_id == "only"
    assert second_governor.claim_next(owner_instance_id=second_owner) is None



def test_reconcile_waits_for_lease_and_worker_lock_before_worker_lost_release(
    tmp_path,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="live", parent_session_id="s1", prompt="live", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    governor.claim_next(owner_instance_id=f"worker_{os.getpid()}_owner")
    ledger.connection().execute(
        "UPDATE job_admissions SET lease_expires_at = 20 WHERE job_id = 'live'"
    )
    ledger.connection().commit()
    lost = []
    lookup = lambda _sid, _job_id: job

    unexpired = governor.reconcile(
        job_lookup=lookup,
        mark_worker_lost=lambda _sid, job_id: lost.append(job_id),
        owner_is_alive=lambda _owner: False,
        now=19,
    )
    locked = governor.reconcile(
        job_lookup=lookup,
        mark_worker_lost=lambda _sid, job_id: lost.append(job_id),
        owner_is_alive=lambda _owner: True,
        now=21,
    )
    released = governor.reconcile(
        job_lookup=lookup,
        mark_worker_lost=lambda _sid, job_id: lost.append(job_id),
        owner_is_alive=lambda _owner: False,
        now=21,
    )
    repeated = governor.reconcile(
        job_lookup=lookup,
        mark_worker_lost=lambda _sid, job_id: lost.append(job_id),
        owner_is_alive=lambda _owner: False,
        now=22,
    )

    assert unexpired.released_worker_lost == 0
    assert locked.released_worker_lost == 0
    assert released.released_worker_lost == 1
    assert repeated.released_worker_lost == 0
    assert lost == ["live"]
    row = ledger.connection().execute(
        "SELECT state, reason_code FROM job_admissions WHERE job_id = 'live'"
    ).fetchone()
    assert tuple(row) == ("released", "error.worker_lost")



def test_live_lease_mutations_are_fenced_by_owner(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    governor.admit_job(
        Job(id="live", parent_session_id="s1", prompt="live", agent_id="a"),
        persist=lambda _job: None,
    )
    current_owner = f"worker_{os.getpid()}_current"
    stale_owner = f"worker_{os.getpid()}_stale"
    claim = governor.claim_next(owner_instance_id=current_owner)

    assert governor.renew_lease(
        "live", owner_instance_id=stale_owner,
        lease_generation=claim.lease_generation,
    ) is False
    assert governor.release_job(
        "live", "completed", owner_instance_id=stale_owner,
        lease_generation=claim.lease_generation,
    ) is False
    assert ledger.connection().execute(
        "SELECT state FROM job_admissions WHERE job_id = 'live'"
    ).fetchone()[0] == "live"
    assert governor.release_job(
        "live", "completed", owner_instance_id=current_owner,
        lease_generation=claim.lease_generation,
    ) is True


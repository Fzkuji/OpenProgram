"""Fault injection at every resource transaction boundary.

Each test kills or races the system at one specific commit point and asserts
the invariant that must survive it: capacity is never leaked, never
double-released, and usage is never double-counted or silently dropped.

The boundaries covered here are admission, dispatch claim, provider
reserve/start/settle, and terminal finalization.
"""
from __future__ import annotations

import multiprocessing
import threading

import pytest

from openprogram.agent.resource_governance import (
    ResourceGovernor,
    ResourceLimits,
    resolve_resource_limits,
)
from openprogram.agent.job.types import Job
from openprogram.usage.event import UsageEvent
from openprogram.usage.ledger import UsageLedger


@pytest.fixture(autouse=True)
def _worker_lock_is_held(monkeypatch):
    monkeypatch.setattr(
        "openprogram.worker.lock.is_held_by", lambda _pid: True, raising=False,
    )


def _governor(db_path, **limits):
    resolved = resolve_resource_limits(
        ResourceLimits(**limits), scheduler_capacity=4,
    )
    return ResourceGovernor(
        UsageLedger(db_path),
        limit_resolver=lambda _sid, _job: resolved,
        session_limit_resolver=lambda _sid: resolved,
    )


def _admit(governor, job_id, session="s1", **kw):
    job = Job(
        id=job_id, parent_session_id=session, prompt="p", agent_id="a", **kw,
    )
    return job, governor.admit_job(job, persist=lambda _t: None)


def _live_count(ledger):
    return ledger.connection().execute(
        "SELECT COUNT(*) FROM job_admissions WHERE state IN ('live','stopping')"
    ).fetchone()[0]


def _reserved_tokens(ledger):
    return ledger.connection().execute(
        "SELECT COALESCE(SUM(reserved_tokens), 0) FROM usage_reservations "
        "WHERE kind = 'token' AND state IN ('reserved','started')"
    ).fetchone()[0]


# --- admission boundary ----------------------------------------------------

def test_persist_failure_rolls_back_admission_and_scope(tmp_path):
    """A crash between the admission insert and the job-store write must
    leave neither a phantom admission nor an orphan budget scope."""
    governor = _governor(tmp_path / "usage.db")
    ledger = governor.ledger

    def explode(_job):
        raise OSError("job store unavailable")

    job = Job(id="t1", parent_session_id="s1", prompt="p", agent_id="a")
    with pytest.raises(OSError):
        governor.admit_job(job, persist=explode)

    assert ledger.connection().execute(
        "SELECT COUNT(*) FROM job_admissions"
    ).fetchone()[0] == 0
    assert ledger.connection().execute(
        "SELECT COUNT(*) FROM budget_scopes WHERE scope_kind = 'job'"
    ).fetchone()[0] == 0


# --- dispatch claim boundary ----------------------------------------------

def test_concurrent_claims_never_hand_one_job_to_two_owners(tmp_path):
    """20 threads racing claim_next must produce disjoint claims."""
    governor = _governor(tmp_path / "usage.db")
    for index in range(8):
        _admit(governor, f"t{index}", session=f"s{index}")

    claims: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(20)

    def claim(worker: int) -> None:
        barrier.wait()
        got = governor.claim_next(owner_instance_id=f"worker_{worker}")
        if got is not None:
            with lock:
                claims.append(got)

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    ids = [claim.job_id for claim in claims]
    assert len(ids) == len(set(ids)), "a job was claimed twice"
    # Scheduler capacity is the hard ceiling regardless of contention.
    assert len(ids) <= 4
    assert _live_count(governor.ledger) == len(ids)


def test_claim_is_capped_by_scheduler_capacity_across_processes(tmp_path):
    """Separate processes contend through SQLite, not shared memory."""
    db_path = tmp_path / "usage.db"
    governor = _governor(db_path)
    for index in range(6):
        _admit(governor, f"t{index}", session=f"s{index}")

    start = multiprocessing.Event()
    output: multiprocessing.Queue = multiprocessing.Queue()
    procs = [
        multiprocessing.Process(
            target=_claim_process, args=(db_path, index, start, output),
        )
        for index in range(6)
    ]
    for proc in procs:
        proc.start()
    start.set()
    for proc in procs:
        proc.join(timeout=30)

    claimed = [output.get() for _ in range(6)]
    won = [job_id for job_id in claimed if job_id is not None]
    assert len(won) == len(set(won)), "two processes claimed the same job"
    assert len(won) <= 4


def _claim_process(db_path, index, start, output) -> None:
    start.wait()
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=4)
    governor = ResourceGovernor(
        UsageLedger(db_path), limit_resolver=lambda _sid, _job: resolved,
    )
    claim = governor.claim_next(owner_instance_id=f"worker_{index}")
    output.put(claim.job_id if claim is not None else None)


# --- provider reserve / settle boundary -----------------------------------

def test_crash_before_settle_holds_exposure_and_recovers_once(tmp_path):
    """A worker that dies after provider I/O but before settling must keep
    its exposure: the request may really have billed. Expiry reclaims only
    requests that never started."""
    governor = _governor(tmp_path / "usage.db", max_total_tokens=10_000)
    ledger = governor.ledger
    job, _ = _admit(governor, "t1")
    model = type("M", (), {"max_tokens": 100, "cost": None})()

    never_started = governor.reserve_provider_request(
        job.id, input_token_upper_bound=10,
        requested_max_output_tokens=20, model=model,
    )
    started = governor.reserve_provider_request(
        job.id, input_token_upper_bound=10,
        requested_max_output_tokens=20, model=model,
    )
    governor.start_provider_request(started.reservation_id)

    ledger.connection().execute("UPDATE usage_reservations SET expires_at = 0")
    ledger.connection().commit()

    # Only the never-started legs are reclaimed (one token + one cost row
    # would be two, but this model has no known price, so token only).
    reclaimed = governor.recover_provider_reservations(now=1)
    assert reclaimed >= 1
    states = {
        row[0]: row[1] for row in ledger.connection().execute(
            "SELECT reservation_id, state FROM usage_reservations"
        )
    }
    assert states[never_started.reservation_id + ":token"] == "released"
    assert states[started.reservation_id + ":token"] == "started"

    # Repeated recovery is idempotent.
    assert governor.recover_provider_reservations(now=1) == 0


def test_settlement_failure_leaves_no_partial_usage(tmp_path, monkeypatch):
    """A ledger write that fails mid-settle must roll back both the event
    and the reservation state — never one without the other."""
    governor = _governor(tmp_path / "usage.db", max_total_tokens=10_000)
    ledger = governor.ledger
    job, _ = _admit(governor, "t1")
    model = type("M", (), {"max_tokens": 100, "cost": None})()
    reservation = governor.reserve_provider_request(
        job.id, input_token_upper_bound=10,
        requested_max_output_tokens=20, model=model,
    )
    governor.start_provider_request(reservation.reservation_id)
    event = UsageEvent(
        event_id="e1", session_id="s1", provider="p", model_id="m",
        input_tokens=5, output_tokens=7, total_tokens=12,
        cost_source="model_catalog",
    )

    monkeypatch.setattr(
        ledger, "append_in_transaction",
        lambda _c, _e: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    with pytest.raises(RuntimeError, match="disk full"):
        governor.settle_provider_request(reservation.reservation_id, event)

    assert ledger.connection().execute(
        "SELECT COUNT(*) FROM usage_events"
    ).fetchone()[0] == 0
    assert ledger.connection().execute(
        "SELECT DISTINCT state FROM usage_reservations"
    ).fetchone()[0] == "started"
    assert _reserved_tokens(ledger) > 0


def test_double_terminal_event_settles_exactly_once(tmp_path):
    """Providers occasionally emit two terminal events. Usage is appended
    once and the second settle is a no-op, not a double charge."""
    governor = _governor(tmp_path / "usage.db", max_total_tokens=10_000)
    ledger = governor.ledger
    job, _ = _admit(governor, "t1")
    model = type("M", (), {"max_tokens": 100, "cost": None})()
    reservation = governor.reserve_provider_request(
        job.id, input_token_upper_bound=10,
        requested_max_output_tokens=20, model=model,
    )
    governor.start_provider_request(reservation.reservation_id)
    event = UsageEvent(
        event_id="e1", session_id="s1", provider="p", model_id="m",
        input_tokens=5, output_tokens=7, total_tokens=12,
        cost_source="model_catalog",
    )

    first = governor.settle_provider_request(reservation.reservation_id, event)
    second = governor.settle_provider_request(reservation.reservation_id, event)

    assert first is not None
    assert second is None
    assert ledger.connection().execute(
        "SELECT COUNT(*) FROM usage_events"
    ).fetchone()[0] == 1
    # Settled exposure is no longer counted as outstanding.
    assert _reserved_tokens(ledger) == 0


def test_accounting_outage_denies_rather_than_billing_blind(tmp_path):
    """An ungoverned job id cannot reserve: a budgeted call must be denied
    with a stable reason instead of proceeding unmetered."""
    governor = _governor(tmp_path / "usage.db", max_total_tokens=10_000)
    model = type("M", (), {"max_tokens": 100, "cost": None})()

    plan = governor.reserve_provider_request(
        "no-such-job", input_token_upper_bound=10,
        requested_max_output_tokens=20, model=model,
    )
    assert plan.allowed is False
    assert plan.reason_code == "quota.accounting_unavailable"


def test_concurrent_reservations_cannot_exceed_the_shared_ceiling(tmp_path):
    """20 threads reserving against one budget must not oversubscribe it."""
    governor = _governor(tmp_path / "usage.db", max_total_tokens=1_000)
    job, _ = _admit(governor, "t1")
    model = type("M", (), {"max_tokens": 50, "cost": None})()

    barrier = threading.Barrier(20)
    accepted: list = []
    lock = threading.Lock()

    def reserve() -> None:
        barrier.wait()
        plan = governor.reserve_provider_request(
            job.id, input_token_upper_bound=100,
            requested_max_output_tokens=50, model=model,
        )
        if plan.allowed:
            with lock:
                accepted.append(plan)

    threads = [threading.Thread(target=reserve) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert accepted, "the budget should admit at least one request"
    assert _reserved_tokens(governor.ledger) <= 1_000


# --- finalization boundary -------------------------------------------------

def test_terminal_write_failure_keeps_the_claim_for_recovery(tmp_path):
    """If the terminal job-store write raises, the admission must not be
    released on a half-written state."""
    governor = _governor(tmp_path / "usage.db")
    ledger = governor.ledger
    job, _ = _admit(governor, "t1")
    claim = governor.claim_next(owner_instance_id="worker")
    assert claim is not None

    fields = {
        "status": "completed", "head_id": None, "result_text": None,
        "error": None, "reason_code": "completed",
    }
    with pytest.raises(OSError):
        governor.finalize_job(
            job.id, "completed",
            owner_instance_id="worker",
            lease_generation=claim.lease_generation,
            terminal_fields=fields,
            mutate=lambda _f: (_ for _ in ()).throw(OSError("store gone")),
        )

    assert _live_count(ledger) == 1, "capacity released on a failed finalize"


def test_concurrent_finalize_writes_the_terminal_state_once(tmp_path):
    """Two threads finalizing the same claim both report success — the
    second is idempotent — but the terminal store write happens once and
    capacity is released once."""
    governor = _governor(tmp_path / "usage.db")
    job, _ = _admit(governor, "t1")
    claim = governor.claim_next(owner_instance_id="worker")
    assert claim is not None

    fields = {
        "status": "completed", "head_id": None, "result_text": None,
        "error": None, "reason_code": "completed",
    }
    results: list[bool] = []
    mutations: list[dict] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def record(applied) -> None:
        with lock:
            mutations.append(applied)

    def finalize() -> None:
        barrier.wait()
        ok = governor.finalize_job(
            job.id, "completed",
            owner_instance_id="worker",
            lease_generation=claim.lease_generation,
            terminal_fields=fields,
            mutate=record,
        )
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=finalize) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Exactly one thread owns the transition. The loser either sees the
    # intent already completed (True) or loses the staging race (False);
    # what must never happen is two terminal writes or two releases.
    assert results.count(True) >= 1
    assert len(mutations) == 1, "the terminal store write ran twice"
    assert _live_count(governor.ledger) == 0
    assert governor.ledger.connection().execute(
        "SELECT COUNT(*) FROM job_admissions WHERE state = 'released'"
    ).fetchone()[0] == 1


def test_stale_lease_generation_cannot_release_a_reclaimed_job(tmp_path):
    """The ABA case: a worker whose lease expired and whose job was
    reclaimed must not release the successor's claim."""
    governor = _governor(tmp_path / "usage.db")
    job, _ = _admit(governor, "t1")
    stale = governor.claim_next(owner_instance_id="instance_old")
    assert stale is not None

    # Reclaim: back to queued, then claimed by a new owner with a new
    # generation.
    assert governor.requeue_job(
        job.id, owner_instance_id="instance_old",
        lease_generation=stale.lease_generation,
    )
    fresh = governor.claim_next(owner_instance_id="instance_new")
    assert fresh is not None
    assert fresh.lease_generation != stale.lease_generation

    assert governor.release_job(
        job.id, "cancel.user",
        owner_instance_id="instance_old",
        lease_generation=stale.lease_generation,
    ) is False
    assert _live_count(governor.ledger) == 1, "stale owner released a live claim"

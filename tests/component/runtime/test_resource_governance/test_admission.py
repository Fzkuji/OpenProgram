"""governance admission tests."""
from __future__ import annotations
from collections import Counter
import multiprocessing
import sqlite3
import threading
import time
from openprogram.runtime.jobs.resource_governance import (
    AdmissionDecision,
    AdmissionRejected,
    build_job_resource_view,
)
from openprogram.models.usage.event import UsageEvent
from ._support import (
    Job,
    ResourceGovernor,
    ResourceLimits,
    UsageLedger,
    pytest,
    resolve_resource_limits,
)


def test_admission_rejection_has_one_stable_dto() -> None:
    decision = AdmissionDecision(
        accepted=False,
        job_id=None,
        reason_code="quota.queue_full",
        retryable=True,
        effective_limits={"scheduler_capacity": 4, "limits": {}},
        usage={"tokens": {"actual": 10, "reserved": 5}},
        capacity={"session_queued": {"used": 2, "limit": 2}},
    )

    assert AdmissionRejected(decision).to_dict() == {
        "accepted": False,
        "job_id": None,
        "reason_code": "quota.queue_full",
        "retryable": True,
        "effective_limits": {"scheduler_capacity": 4, "limits": {}},
        "usage": {"tokens": {"actual": 10, "reserved": 5}},
        "capacity": {"session_queued": {"used": 2, "limit": 2}},
        "idempotent": False,
    }



def test_queue_position_follows_global_admission_order(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=4)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    older = Job(id="older", parent_session_id="s2", prompt="p", agent_id="a")
    target = Job(id="target", parent_session_id="s1", prompt="p", agent_id="a")
    newer = Job(id="newer", parent_session_id="s3", prompt="p", agent_id="a")
    for job in (older, target, newer):
        assert governor.admit_job(job, persist=lambda _job: None).accepted

    assert build_job_resource_view(
        target, ledger=ledger, resolved=resolved,
    ).capacity["queue_position"] == 2



def test_admission_is_atomic_at_queue_boundary(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_queued_per_session=5, max_jobs_per_session=20),
        scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    persisted: list[str] = []
    persisted_lock = threading.Lock()
    decisions = []

    def submit(i: int) -> None:
        job = Job(
            id=f"t_{i}", parent_session_id="s1", prompt=str(i), agent_id="a",
        )
        decision = governor.admit_job(
            job,
            persist=lambda accepted: (
                persisted_lock.acquire(), persisted.append(accepted.id), persisted_lock.release()
            ),
        )
        decisions.append(decision)

    threads = [threading.Thread(target=submit, args=(i,)) for i in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(item.accepted for item in decisions) == 5
    assert {item.reason_code for item in decisions if not item.accepted} == {"quota.queue_full"}
    assert len(persisted) == 5
    counts = Counter(row["state"] for row in ledger.run_tables().handle.rows("job_admissions"))
    assert counts == {"queued": 5}



def test_rejected_admission_has_no_persistence_side_effect(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_jobs_per_session=1), scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    persisted = []
    first = Job(id="t_1", parent_session_id="s1", prompt="one", agent_id="a")
    second = Job(id="t_2", parent_session_id="s1", prompt="two", agent_id="a")

    assert governor.admit_job(first, persist=persisted.append).accepted
    denied = governor.admit_job(second, persist=persisted.append)

    assert denied.accepted is False
    assert denied.job_id is None
    assert denied.reason_code == "quota.jobs_exhausted"
    assert [job.id for job in persisted] == ["t_1"]



def test_rejected_admission_reports_current_session_usage(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_queued_per_session=1, max_total_tokens=100),
        scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    first = Job(id="first", parent_session_id="s1", prompt="p", agent_id="a")
    assert governor.admit_job(first, persist=lambda _job: None).accepted
    assert governor.reserve_tokens(first.id, 30).accepted
    ledger.append(UsageEvent(
        event_id="actual", job_id=first.id,
        budget_scope_id=first.budget_scope_id, session_id="s1",
        provider="p", model_id="m", total_tokens=20,
        cost_source="model_catalog",
    ))

    denied = governor.admit_job(
        Job(id="second", parent_session_id="s1", prompt="p", agent_id="a"),
        persist=lambda _job: None,
    )

    assert denied.reason_code == "quota.queue_full"
    assert denied.usage["tokens"] == {"actual": 20, "reserved": 30}



def test_admission_rejects_spawn_beyond_durable_depth_without_persisting(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openprogram.state.config.read_config",
        lambda: {"agent": {"max_spawn_depth": 1, "max_spawn_fanout": 8}},
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=4)
    governor = ResourceGovernor(
        ledger, limit_resolver=lambda _sid, _job: resolved,
    )
    persisted = []

    decision = governor.admit_job(
        Job(
            id="too_deep", parent_session_id="s1", prompt="x", agent_id="a",
            chain_generations=2,
        ),
        persist=persisted.append,
        caller_turn_id="turn_1",
    )

    assert decision.accepted is False
    assert decision.reason_code == "quota.spawn_depth"
    assert decision.retryable is False
    assert persisted == []
    assert ledger.run_tables().handle.count("job_admissions") == 0



def test_fanout_admission_is_atomic_for_24_threads(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openprogram.state.config.read_config",
        lambda: {"agent": {"max_spawn_depth": 0, "max_spawn_fanout": 5}},
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=32)
    governor = ResourceGovernor(
        ledger, limit_resolver=lambda _sid, _job: resolved,
    )
    decisions = []
    lock = threading.Lock()

    def submit(index: int) -> None:
        decision = governor.admit_job(
            Job(
                id=f"thread_{index}", parent_session_id="s1",
                prompt=str(index), agent_id="a", chain_generations=1,
            ),
            persist=lambda _job: None,
            caller_turn_id="turn_1",
        )
        with lock:
            decisions.append(decision)

    threads = [threading.Thread(target=submit, args=(i,)) for i in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(decision.accepted for decision in decisions) == 5
    assert {
        decision.reason_code for decision in decisions if not decision.accepted
    } == {"quota.spawn_fanout"}
    assert ledger.run_tables().handle.count("job_admissions") == 5



def test_fanout_admission_is_atomic_for_24_threads(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openprogram.state.config.read_config",
        lambda: {"agent": {"max_spawn_depth": 0, "max_spawn_fanout": 5}},
    )
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=32)
    governor = ResourceGovernor(
        UsageLedger(tmp_path / "usage.db"), limit_resolver=lambda _sid, _job: resolved,
    )
    start = threading.Barrier(24)
    results: list[tuple[bool, str | None]] = []

    def admit(index: int) -> None:
        start.wait()
        decision = governor.admit_job(
            Job(
                id=f"thread_{index}", parent_session_id="s1",
                prompt=str(index), agent_id="a", chain_generations=1,
            ),
            persist=lambda _job: None,
            caller_turn_id="turn_1",
        )
        results.append((decision.accepted, decision.reason_code))

    threads = [threading.Thread(target=admit, args=(index,)) for index in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert sum(accepted for accepted, _reason in results) == 5
    assert {reason for accepted, reason in results if not accepted} == {
        "quota.spawn_fanout",
    }
    assert governor.ledger.run_tables().handle.count("job_admissions") == 5



def test_idempotent_admission_does_not_spend_fanout_twice(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openprogram.state.config.read_config",
        lambda: {"agent": {"max_spawn_depth": 0, "max_spawn_fanout": 1}},
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=4)
    governor = ResourceGovernor(
        ledger, limit_resolver=lambda _sid, _job: resolved,
    )
    first_job = Job(
        id="same", parent_session_id="s1", prompt="same", agent_id="a",
        chain_generations=1,
    )

    first = governor.admit_job(
        first_job, persist=lambda _job: None, caller_turn_id="turn_1",
    )
    retry = governor.admit_job(
        first_job, persist=lambda _job: None, caller_turn_id="turn_1",
    )
    denied = governor.admit_job(
        Job(
            id="second", parent_session_id="s1", prompt="second", agent_id="a",
            chain_generations=1,
        ),
        persist=lambda _job: None,
        caller_turn_id="turn_1",
    )

    assert first.accepted is True
    assert retry.accepted is True and retry.idempotent is True
    assert denied.accepted is False
    assert denied.reason_code == "quota.spawn_fanout"



def test_fanout_is_keyed_by_caller_session_across_target_sessions(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openprogram.state.config.read_config",
        lambda: {"agent": {"max_spawn_depth": 0, "max_spawn_fanout": 1}},
    )
    governor = ResourceGovernor(UsageLedger(tmp_path / "usage.db"))

    first = governor.admit_job(
        Job(id="one", parent_session_id="target_a", prompt="one", agent_id="a"),
        persist=lambda _job: None,
        caller_session_id="caller",
        caller_turn_id="turn_1",
    )
    second = governor.admit_job(
        Job(id="two", parent_session_id="target_b", prompt="two", agent_id="a"),
        persist=lambda _job: None,
        caller_session_id="caller",
        caller_turn_id="turn_1",
    )

    assert first.accepted is True
    assert second.accepted is False
    assert second.reason_code == "quota.spawn_fanout"



def test_admission_retry_is_idempotent_and_conflict_is_rejected(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=4)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    persisted = []
    job = Job(id="t_same", parent_session_id="s1", prompt="same", agent_id="a")

    first = governor.admit_job(job, persist=persisted.append)
    retry = governor.admit_job(job, persist=persisted.append)
    conflict = governor.admit_job(
        Job(id="t_same", parent_session_id="s1", prompt="different", agent_id="a"),
        persist=persisted.append,
    )

    assert first.accepted and retry.accepted and retry.idempotent
    assert conflict.accepted is False
    assert conflict.reason_code == "quota.admission_conflict"
    assert len(persisted) == 1



def test_failed_job_publication_rolls_back_provisional_admission(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=4)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)

    with pytest.raises(OSError, match="disk full"):
        governor.admit_job(
            Job(id="t_fail", parent_session_id="s1", prompt="x", agent_id="a"),
            persist=lambda _job: (_ for _ in ()).throw(OSError("disk full")),
        )

    assert ledger.run_tables().handle.count("job_admissions") == 0



def test_legacy_blocked_admission_is_recoverable_after_migration(tmp_path):
    path = tmp_path / "legacy-blocked.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE job_admissions (
            admission_id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL,
            session_id TEXT NOT NULL, parent_job_id TEXT,
            caller_session_id TEXT, caller_turn_id TEXT,
            creates_agent INTEGER NOT NULL, request_fingerprint TEXT NOT NULL,
            budget_scope_id TEXT NOT NULL, dispatch_ready INTEGER NOT NULL DEFAULT 0,
            terminal_blocked INTEGER NOT NULL DEFAULT 1,
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
        ) VALUES ('a', 'blocked', 's1', 0, 'f', 'scope', 'queued', 1, 1)"""
    )
    conn.commit()
    conn.close()
    ledger = UsageLedger(path)
    governor = ResourceGovernor(ledger)
    state = governor.admission_state("blocked")
    assert state[3] is True
    assert state[4] == "recovery"
    assert state[5] > time.time()
    assert state[7] == 0
    assert governor.unblock_terminal_dispatch(
        "blocked", expected_lease_generation=0,
    )
    assert ledger.run_tables().handle.first(
        "job_admissions", job_id="blocked",
    )["dispatch_ready"] == 0


def test_releasing_queued_job_keeps_cumulative_admission(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_queued_per_session=1, max_jobs_per_session=1),
        scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    governor.admit_job(
        Job(id="t_1", parent_session_id="s1", prompt="one", agent_id="a"),
        persist=lambda _job: None,
    )
    governor.release_job("t_1", "cancel.user")

    denied = governor.admit_job(
        Job(id="t_2", parent_session_id="s1", prompt="two", agent_id="a"),
        persist=lambda _job: None,
    )

    assert denied.reason_code == "quota.jobs_exhausted"



def test_job_store_serializes_cross_process_writers(tmp_path, monkeypatch) -> None:
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("requires fork to share the isolated store patch")
    from openprogram.runtime.jobs import store as job_store

    path = tmp_path / "jobs.json"
    monkeypatch.setattr(job_store, "_ensure_session", lambda _sid: path)
    original_load = job_store._load_raw

    def slow_load(target):
        rows = original_load(target)
        time.sleep(0.03)
        return rows

    monkeypatch.setattr(job_store, "_load_raw", slow_load)

    def write_one(index: int) -> None:
        job_store.save_job(
            "s1", Job(
                id=f"t_{index}", parent_session_id="s1", prompt="p", agent_id="a",
            ),
        )

    ctx = multiprocessing.get_context("fork")
    processes = [ctx.Process(target=write_one, args=(index,)) for index in range(12)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(5)
        assert process.exitcode == 0

    assert len(original_load(path)) == 12


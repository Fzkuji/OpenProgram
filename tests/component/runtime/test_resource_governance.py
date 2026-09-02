from __future__ import annotations

from decimal import Decimal
import json
import os
import threading
import multiprocessing
import sqlite3
import time
from types import SimpleNamespace

import pytest

from openprogram.agent.resource_governance import (
    AdmissionDecision,
    AdmissionRejected,
    ResourceLimitError,
    ResourceLimits,
    ResourceGovernor,
    build_job_resource_view,
    resolve_resource_limits,
    save_session_resource_limits,
)
from openprogram.agent.session_db import SessionDB
from openprogram.agent.job.types import Job, JobStatus
from openprogram.usage.event import UsageEvent
from openprogram.usage.ledger import UsageLedger
from tests.component.agent.async_job_support import store_fixture  # noqa: F401


@pytest.fixture(autouse=True)
def _worker_lock_is_held(monkeypatch):
    monkeypatch.setattr(
        "openprogram.worker.lock.is_held_by", lambda _pid: True,
        raising=False,
    )


def _admit_fanout_process(db_path, index, start, output) -> None:
    start.wait()
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=32)
    governor = ResourceGovernor(
        UsageLedger(db_path),
        limit_resolver=lambda _sid, _job: resolved,
    )
    decision = governor.admit_job(
        Job(
            id=f"process_{index}", parent_session_id="s1",
            prompt=str(index), agent_id="a", chain_generations=1,
        ),
        persist=lambda _job: None,
        caller_turn_id="turn_1",
    )
    output.put((decision.accepted, decision.reason_code))


def _migrate_admission_schema_process(db_path, start, output) -> None:
    start.wait()
    try:
        ledger = UsageLedger(db_path)
        columns = {
            row[1]
            for row in ledger.connection().execute(
                "PRAGMA table_info(job_admissions)"
            )
        }
        output.put(("ok", len(columns)))
        ledger.close()
    except Exception as exc:  # noqa: BLE001
        output.put(("error", type(exc).__name__, str(exc)))


def test_resource_limits_require_positive_values_or_null() -> None:
    limits = ResourceLimits.from_mapping({
        "max_live_per_session": 3,
        "max_queued_per_session": None,
        "max_cost_usd": "2.00",
    })

    assert limits.max_live_per_session == 3
    assert limits.max_queued_per_session is None
    assert limits.max_cost_usd == "2.00"

    for value in (0, -1, True, 1.5, "1"):
        with pytest.raises(ResourceLimitError):
            ResourceLimits.from_mapping({"max_total_tokens": value})
    with pytest.raises(ResourceLimitError):
        ResourceLimits.from_mapping({"max_total_tokens": 2**63})
    for value in ("0", "-1", "nan", 1.0):
        with pytest.raises(ResourceLimitError):
            ResourceLimits.from_mapping({"max_cost_usd": value})


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


def test_effective_limits_apply_scheduler_cap_and_report_sources() -> None:
    resolved = resolve_resource_limits(
        ResourceLimits(max_live_per_session=8, max_total_tokens=1000),
        session=ResourceLimits(max_live_per_session=3, max_total_tokens=800),
        job=ResourceLimits(max_total_tokens=500),
        scheduler_capacity=4,
    )

    assert resolved.scheduler_capacity == 4
    assert resolved.fields["max_live_per_session"].configured == 3
    assert resolved.fields["max_live_per_session"].effective == 3
    assert resolved.fields["max_live_per_session"].source == "session"
    assert resolved.fields["max_total_tokens"].configured == 500
    assert resolved.fields["max_total_tokens"].effective == 500
    assert resolved.fields["max_total_tokens"].source == "job"


def test_session_and_job_limits_can_only_narrow() -> None:
    with pytest.raises(ResourceLimitError):
        resolve_resource_limits(
            ResourceLimits(max_total_tokens=100),
            session=ResourceLimits(max_total_tokens=101),
            scheduler_capacity=4,
        )
    with pytest.raises(ResourceLimitError):
        resolve_resource_limits(
            ResourceLimits(max_live_per_session=2),
            job=ResourceLimits(max_live_per_session=1),
            scheduler_capacity=4,
        )


def test_only_owner_can_save_session_resource_limits(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = SessionDB(tmp_path / "sessions")
    db.create_session("s1", "main")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.agent.authority.owner_principal_id", lambda: "owner/install/1234567890abcdef")

    with pytest.raises(PermissionError):
        save_session_resource_limits(
            "s1", {"max_total_tokens": 10},
            authority={"speaker_kind": "human", "authority_tier": "paired"},
        )
    save_session_resource_limits(
        "s1", {"max_total_tokens": 10},
        authority={
            "speaker_kind": "owner", "speaker_id": "owner/local",
            "speaker_display": "Owner", "principal_id": "owner/install/1234567890abcdef",
            "authority_tier": "owner", "interaction": "interactive",
        },
    )
    assert db.get_session("s1")["resource_limits"] == {"max_total_tokens": 10}


def test_legacy_job_resource_view_is_unmetered(tmp_path) -> None:
    job = Job(id="t_old", parent_session_id="s1", prompt="p", agent_id="a")
    view = build_job_resource_view(
        job,
        ledger=UsageLedger(tmp_path / "usage.db"),
        resolved=resolve_resource_limits(ResourceLimits(), scheduler_capacity=4),
    )

    assert view.resource_state == "legacy/unmetered"
    assert view.capacity["scheduler_capacity"] == 4
    assert view.capacity["session_live"]["limit"] == 4
    assert view.budget["tokens"] == {
        "actual": None,
        "reserved": None,
        "limit": None,
    }
    assert view.budget["cost_usd"] == {
        "actual": None,
        "reserved": None,
        "limit": None,
        "known": None,
        "unknown_events": None,
    }
    assert view.budget["runtime_seconds"] == {"used": None, "limit": None}
    assert view.budget["idle_seconds"] == {"used": None, "limit": None}


def test_resource_view_reports_unknown_cost_without_treating_it_as_zero(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    ledger.append(UsageEvent(
        event_id="e1", job_id="t1", session_id="s1", provider="p",
        model_id="m", input_tokens=5, total_tokens=5,
        cost_total=0.0, cost_source="unknown",
    ))
    job = Job(
        id="t1", parent_session_id="s1", prompt="p", agent_id="a",
        admission_id="a1", budget_scope_id="b1",
    )

    view = build_job_resource_view(
        job,
        ledger=ledger,
        resolved=resolve_resource_limits(
            ResourceLimits(max_cost_usd="1.00"), scheduler_capacity=4,
        ),
    )

    assert view.budget["cost_usd"] == {
        "actual": None,
        "reserved": "0",
        "limit": None,
        "known": False,
        "unknown_events": 1,
    }
    assert view.budget["tokens"]["actual"] == 5


def test_resource_view_includes_configured_effective_and_source_limits(tmp_path) -> None:
    resolved = resolve_resource_limits(
        ResourceLimits(max_live_per_session=8, max_total_tokens=100),
        session=ResourceLimits(max_live_per_session=3),
        job=ResourceLimits(max_total_tokens=60),
        scheduler_capacity=4,
    )
    job = Job(id="t1", parent_session_id="s1", prompt="p", agent_id="a")

    view = build_job_resource_view(
        job, ledger=UsageLedger(tmp_path / "usage.db"), resolved=resolved,
    )

    assert view.limits == resolved.to_dict()
    assert view.to_dict()["limits"]["limits"]["max_live_per_session"] == {
        "configured": 3,
        "effective": 3,
        "source": "session",
    }
    assert view.to_dict()["limits"]["limits"]["max_total_tokens"] == {
        "configured": 60,
        "effective": 60,
        "source": "job",
    }


def test_job_budget_limit_excludes_shared_limits_unless_job_has_local_ceiling(
    tmp_path,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    shared = resolve_resource_limits(
        ResourceLimits(max_total_tokens=100, max_cost_usd="1.00"),
        scheduler_capacity=4,
    )
    local = resolve_resource_limits(
        ResourceLimits(max_total_tokens=100, max_cost_usd="1.00"),
        job=ResourceLimits(max_total_tokens=50, max_cost_usd="0.50"),
        scheduler_capacity=4,
    )
    governor = ResourceGovernor(
        ledger,
        limit_resolver=lambda _sid, job: local if job.id == "local" else shared,
        session_limit_resolver=lambda _sid: shared,
    )
    shared_job = Job(
        id="shared", parent_session_id="s1", prompt="p", agent_id="a",
    )
    local_job = Job(
        id="local", parent_session_id="s1", prompt="p", agent_id="a",
    )
    for job in (shared_job, local_job):
        assert governor.admit_job(job, persist=lambda _job: None).accepted

    shared_view = build_job_resource_view(
        shared_job, ledger=ledger, resolved=shared,
    )
    local_view = build_job_resource_view(
        local_job, ledger=ledger, resolved=local,
    )

    assert shared_view.budget["tokens"]["limit"] is None
    assert shared_view.budget["cost_usd"]["limit"] is None
    assert shared_view.budget["shared_remaining"]["tokens"] == 100
    assert local_view.budget["tokens"]["limit"] == 50
    assert local_view.budget["cost_usd"]["limit"] == "0.50"
    assert local_view.budget["shared_remaining"]["tokens"] == 100


def test_job_resource_view_sums_known_cost_as_exact_microusd(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=4)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="cost", parent_session_id="s1", prompt="p", agent_id="a")
    assert governor.admit_job(job, persist=lambda _job: None).accepted
    for event_id, cost in (("cost-1", 0.1), ("cost-2", 0.2)):
        ledger.append(UsageEvent(
            event_id=event_id, job_id=job.id,
            budget_scope_id=job.budget_scope_id, session_id="s1",
            provider="p", model_id="m", cost_total=cost,
            cost_source="model_catalog",
        ))

    view = build_job_resource_view(job, ledger=ledger, resolved=resolved)

    assert view.budget["cost_usd"]["actual"] == "0.300000"


def test_job_resource_view_reads_tokens_cost_and_unknown_from_one_snapshot(
    tmp_path,
) -> None:
    class InjectingUsageLedger(UsageLedger):
        race_event: UsageEvent | None = None

        def _insert_unknown(self) -> None:
            if self.race_event is not None:
                event, self.race_event = self.race_event, None
                self.append(event)

        def job_usage(self, job_id: str):
            usage = super().job_usage(job_id)
            self._insert_unknown()
            return usage

        def job_resource_usage(self, job_id: str):
            self._insert_unknown()
            return super().job_resource_usage(job_id)

    ledger = InjectingUsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=4)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="cost-race", parent_session_id="s1", prompt="p", agent_id="a")
    assert governor.admit_job(job, persist=lambda _job: None).accepted
    ledger.append(UsageEvent(
        event_id="known", job_id=job.id,
        budget_scope_id=job.budget_scope_id, session_id="s1",
        provider="p", model_id="m", total_tokens=1, cost_total=0.1,
        cost_source="model_catalog",
    ))
    ledger.race_event = UsageEvent(
        event_id="unknown", job_id=job.id,
        budget_scope_id=job.budget_scope_id, session_id="s1",
        provider="p", model_id="m", total_tokens=2,
        cost_total=0.0, cost_source="unknown",
    )

    view = build_job_resource_view(job, ledger=ledger, resolved=resolved)

    assert view.budget["tokens"]["actual"] == 3
    assert view.budget["cost_usd"]["actual"] is None
    assert view.budget["cost_usd"]["known"] is False
    assert view.budget["cost_usd"]["unknown_events"] == 1


def test_shared_remaining_counts_sibling_actual_and_open_reservation(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_total_tokens=100), scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    target = Job(id="target", parent_session_id="s1", prompt="p", agent_id="a")
    sibling = Job(id="sibling", parent_session_id="s1", prompt="p", agent_id="a")
    for job in (target, sibling):
        assert governor.admit_job(job, persist=lambda _job: None).accepted
    assert governor.reserve_tokens(sibling.id, 30).accepted
    ledger.append(UsageEvent(
        event_id="sibling-actual", job_id=sibling.id,
        budget_scope_id=sibling.budget_scope_id, session_id="s1",
        provider="p", model_id="m", total_tokens=20,
        cost_source="model_catalog",
    ))

    view = build_job_resource_view(target, ledger=ledger, resolved=resolved)

    assert view.budget["shared_remaining"] == {
        "tokens": 50,
        "cost_usd": None,
        "cost_unknown_events": 0,
    }


def test_shared_remaining_uses_tightest_ancestor_scope(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    session_resolved = resolve_resource_limits(
        ResourceLimits(max_total_tokens=200), scheduler_capacity=4,
    )
    parent_resolved = resolve_resource_limits(
        ResourceLimits(max_total_tokens=200),
        job=ResourceLimits(max_total_tokens=80),
        scheduler_capacity=4,
    )
    governor = ResourceGovernor(
        ledger,
        limit_resolver=lambda _sid, job: (
            parent_resolved if job.id == "parent" else session_resolved
        ),
        session_limit_resolver=lambda _sid: session_resolved,
    )
    parent = Job(id="parent", parent_session_id="s1", prompt="p", agent_id="a")
    target = Job(
        id="target", parent_session_id="s1", parent_job_id=parent.id,
        prompt="p", agent_id="a",
    )
    sibling = Job(
        id="sibling", parent_session_id="s1", parent_job_id=parent.id,
        prompt="p", agent_id="a",
    )
    for job in (parent, target, sibling):
        assert governor.admit_job(job, persist=lambda _job: None).accepted
    assert governor.reserve_tokens(sibling.id, 30).accepted
    ledger.append(UsageEvent(
        event_id="sibling-actual", job_id=sibling.id,
        budget_scope_id=sibling.budget_scope_id, session_id="s1",
        provider="p", model_id="m", total_tokens=20,
        cost_source="model_catalog",
    ))

    view = build_job_resource_view(target, ledger=ledger, resolved=session_resolved)

    assert view.budget["shared_remaining"]["tokens"] == 30


def test_shared_remaining_reports_unknown_ancestor_cost(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_cost_usd="1.00"), scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    target = Job(id="target", parent_session_id="s1", prompt="p", agent_id="a")
    sibling = Job(id="sibling", parent_session_id="s1", prompt="p", agent_id="a")
    for job in (target, sibling):
        assert governor.admit_job(job, persist=lambda _job: None).accepted
    ledger.append(UsageEvent(
        event_id="unknown-cost", job_id=sibling.id,
        budget_scope_id=sibling.budget_scope_id, session_id="s1",
        provider="p", model_id="m", total_tokens=1,
        cost_total=0.0, cost_source="unknown",
    ))

    view = build_job_resource_view(target, ledger=ledger, resolved=resolved)

    assert view.budget["shared_remaining"]["cost_usd"] is None
    assert view.budget["shared_remaining"]["cost_unknown_events"] == 1


def test_shared_remaining_ignores_released_reservation(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_total_tokens=100), scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    target = Job(id="target", parent_session_id="s1", prompt="p", agent_id="a")
    sibling = Job(id="sibling", parent_session_id="s1", prompt="p", agent_id="a")
    for job in (target, sibling):
        assert governor.admit_job(job, persist=lambda _job: None).accepted
    reserved = governor.reserve_tokens(sibling.id, 30)
    assert reserved.accepted
    ledger.connection().execute(
        "UPDATE usage_reservations SET state = 'released' WHERE reservation_id = ?",
        (reserved.reservation_id,),
    )
    ledger.connection().commit()

    view = build_job_resource_view(target, ledger=ledger, resolved=resolved)

    assert view.budget["shared_remaining"]["tokens"] == 100


def test_resource_view_reason_metadata_sets_retryable_and_human_key(tmp_path) -> None:
    job = Job(
        id="queued", parent_session_id="s1", prompt="p", agent_id="a",
        reason_code="quota.queue_full",
    )

    view = build_job_resource_view(
        job,
        ledger=UsageLedger(tmp_path / "usage.db"),
        resolved=resolve_resource_limits(ResourceLimits(), scheduler_capacity=4),
    )

    assert view.retryable is True
    assert view.reason_key == "resource.reason.quota.queue_full"


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
    row = ledger.connection().execute(
        "SELECT state, dispatch_ready FROM job_admissions WHERE job_id = ?",
        (job.id,),
    ).fetchone()
    assert tuple(row) == ("queued", 0)


def test_job_runner_exposes_one_canonical_resource_view_read(
    tmp_path, store_fixture, monkeypatch,
) -> None:
    from openprogram.agent.job.runner import JobRunner

    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_total_tokens=100), scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    monkeypatch.setattr(
        "openprogram.paths.get_execution_db_path",
        lambda: tmp_path / "executions.sqlite3",
    )
    runner = JobRunner(max_workers=1, governor=governor)
    job_id = runner.spawn_job(
        session_id="p1", prompt="p", agent_id="a", parent_msg_id="a1",
        defer_dispatch=True,
    )

    view = runner.get_job_resource_view(job_id)

    assert view is not None
    assert view.job_id == job_id
    assert view.resource is not None
    assert view.resource["limits"] == resolved.to_dict()
    assert runner.get_job_resource_view("missing") is None
    runner.shutdown()


def test_resource_view_reports_live_runtime_and_idle_usage(tmp_path, monkeypatch) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=50, idle_timeout_seconds=20),
        scheduler_capacity=1,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(
        id="timed-view", parent_session_id="s1", prompt="p", agent_id="a",
        admission_id="admission", budget_scope_id="scope",
    )
    assert governor.admit_job(job, persist=lambda _job: None).accepted
    assert governor.claim_next(owner_instance_id="worker") is not None
    row = ledger.connection().execute(
        "SELECT started_at, last_activity_at FROM job_admissions WHERE job_id = ?",
        (job.id,),
    ).fetchone()
    monkeypatch.setattr(
        "openprogram.agent.resource_governance.time.time", lambda: row[0] + 12.5,
    )
    ledger.connection().execute(
        "UPDATE job_admissions SET last_activity_at = ? WHERE job_id = ?",
        (row[0] + 9.0, job.id),
    )

    view = build_job_resource_view(job, ledger=ledger, resolved=resolved)

    assert view.budget["runtime_seconds"] == {"used": 12.5, "limit": 50}
    assert view.budget["idle_seconds"] == {"used": 3.5, "limit": 20}


def test_money_storage_conversion_is_exact() -> None:
    assert ResourceLimits.usd_to_microusd("1.234567") == 1_234_567
    assert ResourceLimits.microusd_to_usd(1_234_567) == Decimal("1.234567")


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
    counts = ledger.connection().execute(
        "SELECT state, COUNT(*) FROM job_admissions GROUP BY state"
    ).fetchall()
    assert [(row[0], row[1]) for row in counts] == [("queued", 5)]


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


def test_invalid_limits_rejection_reports_current_session_usage(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_total_tokens=100), scheduler_capacity=4,
    )

    def resolve(_session_id, job):
        if job.id == "invalid":
            raise ResourceLimitError("invalid")
        return resolved

    governor = ResourceGovernor(
        ledger,
        limit_resolver=resolve,
        session_limit_resolver=lambda _sid: resolved,
    )
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
        Job(id="invalid", parent_session_id="s1", prompt="p", agent_id="a"),
        persist=lambda _job: None,
    )

    assert denied.reason_code == "quota.invalid_limits"
    assert denied.usage["tokens"] == {"actual": 20, "reserved": 30}


def test_invalid_limits_rejection_fails_closed_when_usage_read_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    governor = ResourceGovernor(
        ledger,
        limit_resolver=lambda _sid, _job: (_ for _ in ()).throw(
            ResourceLimitError("invalid")
        ),
    )
    monkeypatch.setattr(
        ledger, "read",
        lambda: (_ for _ in ()).throw(OSError("ledger unavailable")),
    )

    denied = governor.admit_job(
        Job(id="invalid", parent_session_id="s1", prompt="p", agent_id="a"),
        persist=lambda _job: None,
    )

    assert denied.reason_code == "quota.accounting_unavailable"
    assert denied.retryable is True


def test_admission_rejects_spawn_beyond_durable_depth_without_persisting(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openprogram.setup._read_config",
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
    assert ledger.connection().execute(
        "SELECT COUNT(*) FROM job_admissions"
    ).fetchone()[0] == 0


def test_fanout_admission_is_atomic_for_24_threads(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openprogram.setup._read_config",
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
    assert ledger.connection().execute(
        "SELECT COUNT(*) FROM job_admissions"
    ).fetchone()[0] == 5


def test_fanout_admission_is_atomic_for_24_processes(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {"agent": {"max_spawn_depth": 0, "max_spawn_fanout": 5}},
    )
    ctx = multiprocessing.get_context("fork")
    start = ctx.Event()
    output = ctx.Queue()
    db_path = tmp_path / "usage.db"
    initial = UsageLedger(db_path)
    initial.connection()
    initial.close()
    processes = [
        ctx.Process(
            target=_admit_fanout_process,
            args=(db_path, index, start, output),
        )
        for index in range(24)
    ]
    for process in processes:
        process.start()
    start.set()
    results = [output.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    assert sum(accepted for accepted, _reason in results) == 5
    assert {reason for accepted, reason in results if not accepted} == {
        "quota.spawn_fanout",
    }
    assert UsageLedger(db_path).connection().execute(
        "SELECT COUNT(*) FROM job_admissions"
    ).fetchone()[0] == 5


def test_idempotent_admission_does_not_spend_fanout_twice(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openprogram.setup._read_config",
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
        "openprogram.setup._read_config",
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

    assert ledger.connection().execute(
        "SELECT COUNT(*) FROM job_admissions"
    ).fetchone()[0] == 0


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
    ledger.connection().execute(
        "UPDATE job_admissions SET state = 'preparing' "
        "WHERE job_id IN ('preparing_present', 'preparing_missing')"
    )
    ledger.connection().commit()

    result = governor.reconcile(
        job_lookup=lambda _sid, job_id: jobs.get(job_id),
        mark_worker_lost=lambda _sid, _job_id: None,
        owner_is_alive=lambda _owner: False,
    )

    rows = {
        row["job_id"]: (row["state"], row["reason_code"])
        for row in ledger.connection().execute(
            "SELECT job_id, state, reason_code FROM job_admissions"
        )
    }
    assert result.finalized_preparing == 1
    assert result.rolled_back_preparing == 1
    assert result.released_missing == 1
    assert "preparing_missing" not in rows
    assert rows == {
        "preparing_present": ("queued", None),
        "queued_missing": ("released", "error.job_missing"),
    }


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
    ledger.connection().execute(
        "UPDATE job_admissions SET lease_expires_at = 20 WHERE job_id = 'live'"
    )
    ledger.connection().commit()
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

    row = ledger.connection().execute(
        "SELECT state, owner_instance_id, lease_generation "
        "FROM job_admissions WHERE job_id = 'live'"
    ).fetchone()
    assert result.released_worker_lost == 1
    assert stale_finalized is False
    assert mutations == ["worker_lost"]
    assert tuple(row) == ("released", None, claim.lease_generation + 1)


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
    assert tuple(ledger.connection().execute(
        "SELECT state, owner_instance_id FROM job_admissions WHERE job_id = ?",
        (job.id,),
    ).fetchone()) == ("live", "worker")
    assert ledger.connection().execute(
        "SELECT state FROM job_finalizations WHERE job_id = ?", (job.id,),
    ).fetchone()[0] == "pending"

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
        assert ledger.connection().execute(
            "SELECT state FROM job_admissions WHERE job_id = ?", (job.id,),
        ).fetchone()[0] == "live"
        assert ledger.connection().execute(
            "SELECT state FROM job_finalizations WHERE job_id = ?", (job.id,),
        ).fetchone()[0] == "pending"

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
    assert ledger.connection().execute(
        "SELECT state FROM job_admissions WHERE job_id = ?", (job.id,),
    ).fetchone()[0] == "released"
    assert ledger.connection().execute(
        "SELECT state FROM job_finalizations WHERE job_id = ?", (job.id,),
    ).fetchone()[0] == "completed"


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
    assert tuple(ledger.connection().execute(
        "SELECT state, owner_instance_id, lease_generation "
        "FROM job_admissions WHERE job_id = ?",
        (job.id,),
    ).fetchone()) == ("live", "worker", claim.lease_generation)

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
    assert ledger.connection().execute(
        "SELECT state FROM job_admissions WHERE job_id = ?", (job.id,),
    ).fetchone()[0] == "released"
    assert ledger.connection().execute(
        "SELECT state FROM job_finalizations WHERE job_id = ?", (job.id,),
    ).fetchone()[0] == "completed"


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
    assert tuple(ledger.connection().execute(
        "SELECT dispatch_ready, terminal_blocked FROM job_admissions "
        "WHERE job_id = ?", (child.id,),
    ).fetchone()) == (0, 0)
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
    assert tuple(ledger.connection().execute(
        "SELECT state, owner_instance_id, lease_generation, terminal_blocked "
        "FROM job_admissions WHERE job_id = ?", (job.id,),
    ).fetchone()) == ("live", "canonical-owner", 4, 0)


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
    import openprogram.agent.resource_governance as governance_module

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
    assert tuple(ledger.connection().execute(
        "SELECT dispatch_ready, terminal_blocked FROM job_admissions "
        "WHERE job_id = ?", (job.id,),
    ).fetchone()) == (0, 0)
    assert governor.claim_next(owner_instance_id="worker") is None
    assert governor.block_dispatch(
        job.id, command_id="cancel-deferred", phase="recovery",
    )
    assert governor.release_job(job.id)
    assert tuple(ledger.connection().execute(
        "SELECT state, terminal_blocked, terminal_block_command_id, "
        "terminal_block_phase, terminal_block_expires_at, "
        "terminal_block_prior_dispatch_ready FROM job_admissions "
        "WHERE job_id = ?", (job.id,),
    ).fetchone()) == ("released", 0, None, None, None, None)


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
    conn.commit()
    conn.close()
    ledger = UsageLedger(path)
    columns = {
        row[1]: row[4]
        for row in ledger.connection().execute("PRAGMA table_info(job_admissions)")
    }
    assert columns["terminal_blocked"] == "0"
    assert "terminal_block_command_id" in columns
    assert "terminal_block_phase" in columns
    assert "terminal_block_expires_at" in columns
    assert "terminal_block_prior_dispatch_ready" in columns


def test_admission_schema_migration_is_process_serialized(tmp_path):
    path = tmp_path / "legacy-concurrent.db"
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
    conn.commit()
    conn.close()
    ctx = multiprocessing.get_context("fork")
    start = ctx.Event()
    output = ctx.Queue()
    processes = [
        ctx.Process(
            target=_migrate_admission_schema_process,
            args=(path, start, output),
        )
        for _ in range(8)
    ]
    for process in processes:
        process.start()
    start.set()
    results = [output.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    assert all(result[0] == "ok" for result in results), results
    columns = {
        row[1]
        for row in UsageLedger(path).connection().execute(
            "PRAGMA table_info(job_admissions)"
        )
    }
    assert {
        "terminal_blocked", "terminal_block_command_id",
        "terminal_block_phase", "terminal_block_expires_at",
        "terminal_block_prior_dispatch_ready",
    }.issubset(columns)


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
    row = ledger.connection().execute(
        "SELECT terminal_blocked, terminal_block_phase, "
        "terminal_block_expires_at, terminal_block_prior_dispatch_ready "
        "FROM job_admissions WHERE job_id = 'blocked'"
    ).fetchone()
    assert row[0] == 1
    assert row[1] == "recovery"
    assert row[2] > time.time()
    assert row[3] == 0
    governor = ResourceGovernor(ledger)
    assert governor.unblock_terminal_dispatch(
        "blocked", expected_lease_generation=0,
    )
    assert ledger.connection().execute(
        "SELECT dispatch_ready FROM job_admissions WHERE job_id = 'blocked'"
    ).fetchone()[0] == 0


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
    assert tuple(ledger.connection().execute(
        "SELECT state, owner_instance_id FROM job_admissions WHERE job_id = ?",
        (child.id,),
    ).fetchone()) == ("queued", "worker")
    assert ledger.connection().execute(
        "SELECT state FROM job_finalizations WHERE job_id = ?", (child.id,),
    ).fetchone()[0] == "pending"


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
    ledger.connection().execute(
        "UPDATE job_admissions SET lease_expires_at = 0 WHERE job_id = ?",
        (job.id,),
    )
    ledger.connection().commit()
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
    assert ledger.connection().execute(
        "SELECT state FROM job_admissions WHERE job_id = ?", (job.id,),
    ).fetchone()[0] == "released"
    assert ledger.connection().execute(
        "SELECT state FROM job_finalizations WHERE job_id = ?", (job.id,),
    ).fetchone()[0] == "completed"


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
        assert ledger.connection().execute(
            "SELECT state FROM job_admissions WHERE job_id = ?", (job.id,),
        ).fetchone()[0] == "live"
        assert ledger.connection().execute(
            "SELECT state FROM job_finalizations WHERE job_id = ?", (job.id,),
        ).fetchone()[0] == "pending"


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
    assert ledger.connection().execute(
        "SELECT COUNT(*) FROM job_finalizations"
    ).fetchone()[0] == 0

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
    ledger.connection().execute(
        "UPDATE job_admissions SET owner_instance_id = 'competitor', "
        "lease_generation = lease_generation + 1 WHERE job_id = ?",
        (job.id,),
    )
    ledger.connection().commit()
    assert governor.finalize_job(
        job.id,
        "completed",
        owner_instance_id="competitor",
        lease_generation=claim.lease_generation + 1,
        terminal_fields=original,
        mutate=lambda _fields: mutations.append("competing fence"),
    ) is False

    row = ledger.connection().execute(
        "SELECT owner_instance_id, lease_generation, fields_json, state "
        "FROM job_finalizations WHERE job_id = ?",
        (job.id,),
    ).fetchone()
    assert tuple(row[:2]) == ("worker", claim.lease_generation)
    assert json.loads(row["fields_json"]) == {"version": 1, "fields": original}
    assert row["state"] == "pending"
    assert mutations == []


def test_provider_reservation_recovery_and_settlement_are_idempotent(
    tmp_path, monkeypatch,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_total_tokens=1_000), scheduler_capacity=1,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="live", parent_session_id="s1", prompt="live", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    model = SimpleNamespace(max_tokens=100, cost=None)

    expired = governor.reserve_provider_request(
        job.id, input_token_upper_bound=10,
        requested_max_output_tokens=20, model=model,
    )
    ledger.connection().execute(
        "UPDATE usage_reservations SET expires_at = 0 "
        "WHERE reservation_id LIKE ?",
        (expired.reservation_id + ":%",),
    )
    ledger.connection().commit()
    assert governor.recover_provider_reservations(now=1) == 1

    started = governor.reserve_provider_request(
        job.id, input_token_upper_bound=10,
        requested_max_output_tokens=20, model=model,
    )
    governor.start_provider_request(started.reservation_id)
    assert governor.release_provider_request(started.reservation_id) is False
    assert ledger.connection().execute(
        "SELECT DISTINCT state FROM usage_reservations WHERE reservation_id LIKE ?",
        (started.reservation_id + ":%",),
    ).fetchone()[0] == "started"
    ledger.connection().execute(
        "UPDATE usage_reservations SET expires_at = 0 "
        "WHERE reservation_id LIKE ?",
        (started.reservation_id + ":%",),
    )
    ledger.connection().commit()
    assert governor.recover_provider_reservations(now=1) == 0

    event = UsageEvent(
        event_id="actual", session_id="s1", provider="p", model_id="m",
        input_tokens=5, output_tokens=7, total_tokens=12,
        cost_source="model_catalog",
    )
    original_append = ledger.append_in_transaction

    def fail_append(_conn, _event) -> None:
        raise RuntimeError("usage write failed")

    monkeypatch.setattr(ledger, "append_in_transaction", fail_append)
    with pytest.raises(RuntimeError, match="usage write failed"):
        governor.settle_provider_request(started.reservation_id, event)
    assert ledger.connection().execute(
        "SELECT DISTINCT state FROM usage_reservations WHERE reservation_id LIKE ?",
        (started.reservation_id + ":%",),
    ).fetchone()[0] == "started"
    assert ledger.connection().execute(
        "SELECT COUNT(*) FROM usage_events WHERE reservation_id = ?",
        (started.reservation_id + ":token",),
    ).fetchone()[0] == 0
    monkeypatch.setattr(ledger, "append_in_transaction", original_append)

    results: list[object] = []

    def settle() -> None:
        results.append(governor.settle_provider_request(started.reservation_id, event))

    threads = [threading.Thread(target=settle) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(result is not None for result in results) == 1
    assert ledger.connection().execute(
        "SELECT COUNT(*) FROM usage_events WHERE reservation_id = ?",
        (started.reservation_id + ":token",),
    ).fetchone()[0] == 1
    governor.start_provider_request(started.reservation_id)


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

    row = ledger.connection().execute(
        "SELECT state, reason_code FROM job_admissions WHERE job_id = ?",
        (job.id,),
    ).fetchone()
    assert tuple(row) == ("stopping", "cancel.user")


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

    row = ledger.connection().execute(
        "SELECT state, reason_code FROM job_admissions WHERE job_id = ?",
        (job.id,),
    ).fetchone()
    assert finalized is True
    assert mutations == ["cancel.user"]
    assert tuple(row) == ("released", "cancel.user")


def test_time_limits_start_at_live_claim_and_exclude_queue_wait(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=10, idle_timeout_seconds=4),
        scheduler_capacity=1,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    governor.admit_job(
        Job(id="timed", parent_session_id="s1", prompt="timed", agent_id="a"),
        persist=lambda _job: None,
    )
    queued = ledger.connection().execute(
        "SELECT started_at, last_activity_at FROM job_admissions WHERE job_id = 'timed'"
    ).fetchone()

    assert tuple(queued) == (None, None)
    assert governor.job_time_limits("timed") == (10, 4)
    claim = governor.claim_next(owner_instance_id="worker")
    live = ledger.connection().execute(
        "SELECT started_at, last_activity_at FROM job_admissions WHERE job_id = 'timed'"
    ).fetchone()
    assert live[0] is not None
    assert live[1] == live[0]


def test_time_limits_and_view_use_admission_snapshot_after_session_change(
    tmp_path,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    current = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=10, idle_timeout_seconds=4),
        scheduler_capacity=1,
    )
    governor = ResourceGovernor(
        ledger,
        limit_resolver=lambda _sid, _job: current,
        session_limit_resolver=lambda _sid: current,
    )
    job = Job(id="snapshot", parent_session_id="s1", prompt="p", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    current = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=2, idle_timeout_seconds=1),
        scheduler_capacity=1,
    )

    assert governor.job_time_limits(job.id) == (10, 4)
    view = build_job_resource_view(job, ledger=ledger, resolved=current)
    assert view.limits["limits"]["max_runtime_seconds"]["effective"] == 10
    assert view.budget["runtime_seconds"]["limit"] == 10

    newer = Job(id="newer", parent_session_id="s1", prompt="p", agent_id="a")
    governor.admit_job(newer, persist=lambda _job: None)
    assert governor.job_time_limits(newer.id) == (2, 1)
    current = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=20, idle_timeout_seconds=8),
        scheduler_capacity=1,
    )
    newest = Job(id="newest", parent_session_id="s1", prompt="p", agent_id="a")
    governor.admit_job(newest, persist=lambda _job: None)
    assert governor.job_time_limits(newest.id) == (20, 8)


@pytest.mark.parametrize("snapshot", [None, {"limits": {"max_runtime_seconds": {"bad": 1}}}])
def test_legacy_or_malformed_snapshot_uses_durable_job_time_limits(
    tmp_path, snapshot,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    admitted = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=10, idle_timeout_seconds=4),
        scheduler_capacity=1,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: admitted)
    job = Job(id="legacy-snapshot", parent_session_id="s1", prompt="p", agent_id="a")
    governor.admit_job(job, persist=lambda _job: None)
    job.resolved_limits_snapshot = snapshot
    current = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=2, idle_timeout_seconds=1),
        scheduler_capacity=1,
    )

    view = build_job_resource_view(job, ledger=ledger, resolved=current)

    assert governor.job_time_limits(job.id) == (10, 4)
    assert view.limits["limits"]["max_runtime_seconds"]["effective"] == 10
    assert view.limits["limits"]["idle_timeout_seconds"]["effective"] == 4


def test_meaningful_activity_is_owner_fenced_and_keepalive_is_ignored(
    tmp_path, monkeypatch,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(idle_timeout_seconds=4), scheduler_capacity=1,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    governor.admit_job(
        Job(id="timed", parent_session_id="s1", prompt="timed", agent_id="a"),
        persist=lambda _job: None,
    )
    claim = governor.claim_next(owner_instance_id="worker")
    before = ledger.connection().execute(
        "SELECT last_activity_at FROM job_admissions WHERE job_id = 'timed'"
    ).fetchone()[0]
    monkeypatch.setattr(
        "openprogram.agent.resource_governance.time.time", lambda: before + 5,
    )

    assert governor.record_activity(
        "timed", owner_instance_id="worker",
        lease_generation=claim.lease_generation,
        activity_kind="transport_keepalive",
    ) is False
    assert governor.record_activity(
        "timed", owner_instance_id="stale",
        lease_generation=claim.lease_generation,
        activity_kind="provider_data",
    ) is False
    assert governor.record_activity(
        "timed", owner_instance_id="worker",
        lease_generation=claim.lease_generation,
        activity_kind="tool_progress",
    ) is True
    after = ledger.connection().execute(
        "SELECT last_activity_at FROM job_admissions WHERE job_id = 'timed'"
    ).fetchone()[0]
    assert after == before + 5


def test_child_progress_updates_live_parent_activity(tmp_path, monkeypatch) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(idle_timeout_seconds=5), scheduler_capacity=2,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    parent = Job(id="parent", parent_session_id="s1", prompt="p", agent_id="a")
    child = Job(
        id="child", parent_session_id="s2", parent_job_id="parent",
        prompt="c", agent_id="a",
    )
    governor.admit_job(parent, persist=lambda _job: None)
    governor.admit_job(child, persist=lambda _job: None)
    governor.claim_next(owner_instance_id="worker")
    child_claim = governor.claim_next(owner_instance_id="worker")
    monkeypatch.setattr(
        "openprogram.agent.resource_governance.time.time", lambda: 1234.5,
    )

    assert governor.record_activity(
        "child", owner_instance_id="worker",
        lease_generation=child_claim.lease_generation,
        activity_kind="child_progress",
    ) is True
    rows = ledger.connection().execute(
        "SELECT job_id, last_activity_at FROM job_admissions ORDER BY job_id"
    ).fetchall()
    assert [(row["job_id"], row["last_activity_at"]) for row in rows] == [
        ("child", 1234.5), ("parent", 1234.5),
    ]


def test_child_time_limits_use_strictest_ancestor_scope(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    session_limits = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=10, idle_timeout_seconds=8),
        scheduler_capacity=2,
    )
    parent_limits = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=10, idle_timeout_seconds=8),
        job=ResourceLimits(max_runtime_seconds=3, idle_timeout_seconds=2),
        scheduler_capacity=2,
    )
    governor = ResourceGovernor(
        ledger,
        limit_resolver=lambda _sid, job: (
            parent_limits if job.id == "parent" else session_limits
        ),
        session_limit_resolver=lambda _sid: session_limits,
    )
    governor.admit_job(
        Job(id="parent", parent_session_id="s1", prompt="p", agent_id="a"),
        persist=lambda _job: None,
    )
    governor.admit_job(
        Job(
            id="child", parent_session_id="s1", parent_job_id="parent",
            prompt="c", agent_id="a",
        ),
        persist=lambda _job: None,
    )

    assert governor.job_time_limits("child") == (3, 2)


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
    from openprogram.agent.job import store as job_store

    path = tmp_path / "jobs.json"
    monkeypatch.setattr(job_store, "_ensure_session", lambda _sid: path)
    monkeypatch.setattr(job_store, "_commit", lambda *_args, **_kwargs: None)
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


def test_sibling_token_reservations_share_session_budget_atomically(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_total_tokens=100), scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    for job_id in ("t_1", "t_2"):
        governor.admit_job(
            Job(id=job_id, parent_session_id="s1", prompt=job_id, agent_id="a"),
            persist=lambda _job: None,
        )
    decisions = []

    def reserve(job_id: str) -> None:
        decisions.append(governor.reserve_tokens(job_id, 60))

    threads = [threading.Thread(target=reserve, args=(job_id,)) for job_id in ("t_1", "t_2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(item.accepted for item in decisions) == 1
    assert {item.reason_code for item in decisions if not item.accepted} == {"quota.token_exhausted"}


def test_cost_budget_fails_closed_when_price_is_unknown(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_cost_usd="1.00"), scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    governor.admit_job(
        Job(id="t_1", parent_session_id="s1", prompt="one", agent_id="a"),
        persist=lambda _job: None,
    )

    denied = governor.reserve_cost("t_1", 100_000, price_known=False)

    assert denied.accepted is False
    assert denied.reason_code == "quota.cost_unavailable"
    assert ledger.connection().execute(
        "SELECT COUNT(*) FROM usage_reservations"
    ).fetchone()[0] == 0


def test_settlement_records_actual_usage_and_releases_reservation_delta(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_total_tokens=100), scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    governor.admit_job(
        Job(id="t_1", parent_session_id="s1", prompt="one", agent_id="a"),
        persist=lambda _job: None,
    )
    reserved = governor.reserve_tokens("t_1", 80)
    assert reserved.accepted
    governor.start_reservation(reserved.reservation_id)

    governor.settle_reservation(
        reserved.reservation_id,
        UsageEvent(
            event_id="actual", job_id="t_1", session_id="s1", provider="p",
            model_id="m", input_tokens=20, output_tokens=10, total_tokens=30,
            cost_source="model_catalog",
        ),
    )

    assert governor.reserve_tokens("t_1", 70).accepted
    assert ledger.query(filters={"job_id": "t_1"})[0].total_tokens == 30


def test_job_budget_does_not_narrow_shared_session_scope(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    session_limits = resolve_resource_limits(
        ResourceLimits(max_total_tokens=100), scheduler_capacity=4,
    )
    job_limits = resolve_resource_limits(
        ResourceLimits(max_total_tokens=100),
        job=ResourceLimits(max_total_tokens=50), scheduler_capacity=4,
    )
    governor = ResourceGovernor(
        ledger,
        limit_resolver=lambda _sid, job: job_limits if job.id == "t_1" else session_limits,
        session_limit_resolver=lambda _sid: session_limits,
    )
    for job_id in ("t_1", "t_2"):
        governor.admit_job(
            Job(id=job_id, parent_session_id="s1", prompt=job_id, agent_id="a"),
            persist=lambda _job: None,
        )

    assert governor.reserve_tokens("t_1", 50).accepted
    assert governor.reserve_tokens("t_2", 50).accepted
    assert governor.reserve_tokens("t_2", 1).reason_code == "quota.token_exhausted"


def test_unknown_parent_job_still_inherits_session_budget(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_total_tokens=100), scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    governor.admit_job(
        Job(
            id="child", parent_session_id="s1", parent_job_id="missing",
            prompt="child", agent_id="a",
        ),
        persist=lambda _job: None,
    )
    governor.admit_job(
        Job(id="sibling", parent_session_id="s1", prompt="sibling", agent_id="a"),
        persist=lambda _job: None,
    )

    assert governor.reserve_tokens("child", 100).accepted
    assert governor.reserve_tokens("sibling", 1).reason_code == "quota.token_exhausted"

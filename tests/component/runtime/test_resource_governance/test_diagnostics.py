"""governance diagnostics tests."""
from __future__ import annotations
from ._support import (
    Job,
    ResourceGovernor,
    ResourceLimits,
    UsageEvent,
    UsageLedger,
    build_job_resource_view,
    resolve_resource_limits,
    store_fixture,
)


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
        "openprogram.agent.resource_governance.governor.time.time", lambda: row[0] + 12.5,
    )
    ledger.connection().execute(
        "UPDATE job_admissions SET last_activity_at = ? WHERE job_id = ?",
        (row[0] + 9.0, job.id),
    )

    view = build_job_resource_view(job, ledger=ledger, resolved=resolved)

    assert view.budget["runtime_seconds"] == {"used": 12.5, "limit": 50}
    assert view.budget["idle_seconds"] == {"used": 3.5, "limit": 20}



def test_resource_view_does_not_use_connection_after_read_lease(tmp_path, monkeypatch):
    from contextlib import contextmanager

    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_total_tokens=100), scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    job = Job(id="closing", parent_session_id="s1", prompt="p", agent_id="a")
    assert governor.admit_job(job, persist=lambda _job: None).accepted
    original_read = ledger.read

    @contextmanager
    def close_after_read():
        with original_read() as conn:
            yield conn
        # Legal shutdown immediately after releasing a read lease.
        ledger.close()

    monkeypatch.setattr(ledger, "read", close_after_read)
    try:
        view = build_job_resource_view(job, ledger=ledger, resolved=resolved)
        assert view.budget["shared_remaining"] == {
            "tokens": 100, "cost_usd": None, "cost_unknown_events": 0,
        }
    finally:
        ledger.close()

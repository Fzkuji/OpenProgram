"""governance activity tests."""
from __future__ import annotations
from ._support import (
    Job,
    ResourceGovernor,
    ResourceLimits,
    UsageLedger,
    build_job_resource_view,
    pytest,
    resolve_resource_limits,
)


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
        "openprogram.agent.resource_governance.governor.time.time", lambda: before + 5,
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
        "openprogram.agent.resource_governance.governor.time.time", lambda: 1234.5,
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


"""governance reservations tests."""
from __future__ import annotations
from ._support import (
    Job,
    ResourceGovernor,
    ResourceLimits,
    SimpleNamespace,
    UsageEvent,
    UsageLedger,
    build_job_resource_view,
    pytest,
    resolve_resource_limits,
    threading,
)


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


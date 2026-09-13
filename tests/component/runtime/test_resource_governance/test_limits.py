"""governance limits tests."""
from __future__ import annotations
from ._support import (
    Decimal,
    Job,
    ResourceGovernor,
    ResourceLimitError,
    ResourceLimits,
    SessionDB,
    UsageEvent,
    UsageLedger,
    pytest,
    resolve_resource_limits,
    save_session_resource_limits,
)


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



def test_money_storage_conversion_is_exact() -> None:
    assert ResourceLimits.usd_to_microusd("1.234567") == 1_234_567
    assert ResourceLimits.microusd_to_usd(1_234_567) == Decimal("1.234567")



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


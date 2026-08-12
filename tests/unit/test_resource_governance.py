from __future__ import annotations

from decimal import Decimal
import threading
import multiprocessing
import time

import pytest

from openprogram.agent.resource_governance import (
    ResourceLimitError,
    ResourceLimits,
    ResourceGovernor,
    build_task_resource_view,
    resolve_resource_limits,
    save_session_resource_limits,
)
from openprogram.agent.session_db import SessionDB
from openprogram.agent.task.types import Task
from openprogram.usage.event import UsageEvent
from openprogram.usage.ledger import UsageLedger


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
    for value in ("0", "-1", "nan", 1.0):
        with pytest.raises(ResourceLimitError):
            ResourceLimits.from_mapping({"max_cost_usd": value})


def test_effective_limits_apply_scheduler_cap_and_report_sources() -> None:
    resolved = resolve_resource_limits(
        ResourceLimits(max_live_per_session=8, max_total_tokens=1000),
        session=ResourceLimits(max_live_per_session=3, max_total_tokens=800),
        task=ResourceLimits(max_total_tokens=500),
        scheduler_capacity=4,
    )

    assert resolved.scheduler_capacity == 4
    assert resolved.fields["max_live_per_session"].configured == 3
    assert resolved.fields["max_live_per_session"].effective == 3
    assert resolved.fields["max_live_per_session"].source == "session"
    assert resolved.fields["max_total_tokens"].configured == 500
    assert resolved.fields["max_total_tokens"].effective == 500
    assert resolved.fields["max_total_tokens"].source == "task"


def test_session_and_task_limits_can_only_narrow() -> None:
    with pytest.raises(ResourceLimitError):
        resolve_resource_limits(
            ResourceLimits(max_total_tokens=100),
            session=ResourceLimits(max_total_tokens=101),
            scheduler_capacity=4,
        )
    with pytest.raises(ResourceLimitError):
        resolve_resource_limits(
            ResourceLimits(max_live_per_session=2),
            task=ResourceLimits(max_live_per_session=1),
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


def test_legacy_task_resource_view_is_unmetered(tmp_path) -> None:
    task = Task(id="t_old", parent_session_id="s1", prompt="p", agent_id="a")
    view = build_task_resource_view(
        task,
        ledger=UsageLedger(tmp_path / "usage.db"),
        resolved=resolve_resource_limits(ResourceLimits(), scheduler_capacity=4),
    )

    assert view.resource_state == "legacy/unmetered"
    assert view.capacity["scheduler_capacity"] == 4
    assert view.capacity["session_live"]["limit"] == 4
    assert view.budget["cost_usd"]["known"] is True


def test_resource_view_reports_unknown_cost_without_treating_it_as_zero(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    ledger.append(UsageEvent(
        event_id="e1", task_id="t1", session_id="s1", provider="p",
        model_id="m", input_tokens=5, total_tokens=5,
        cost_total=0.0, cost_source="unknown",
    ))
    task = Task(
        id="t1", parent_session_id="s1", prompt="p", agent_id="a",
        admission_id="a1", budget_scope_id="b1",
    )

    view = build_task_resource_view(
        task,
        ledger=ledger,
        resolved=resolve_resource_limits(
            ResourceLimits(max_cost_usd="1.00"), scheduler_capacity=4,
        ),
    )

    assert view.budget["cost_usd"] == {
        "actual": None,
        "reserved": "0",
        "limit": "1.00",
        "known": False,
        "unknown_events": 1,
    }
    assert view.budget["tokens"]["actual"] == 5


def test_money_storage_conversion_is_exact() -> None:
    assert ResourceLimits.usd_to_microusd("1.234567") == 1_234_567
    assert ResourceLimits.microusd_to_usd(1_234_567) == Decimal("1.234567")


def test_admission_is_atomic_at_queue_boundary(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_queued_per_session=5, max_tasks_per_session=20),
        scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    persisted: list[str] = []
    persisted_lock = threading.Lock()
    decisions = []

    def submit(i: int) -> None:
        task = Task(
            id=f"t_{i}", parent_session_id="s1", prompt=str(i), agent_id="a",
        )
        decision = governor.admit_task(
            task,
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
        "SELECT state, COUNT(*) FROM task_admissions GROUP BY state"
    ).fetchall()
    assert [(row[0], row[1]) for row in counts] == [("queued", 5)]


def test_rejected_admission_has_no_persistence_side_effect(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_tasks_per_session=1), scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    persisted = []
    first = Task(id="t_1", parent_session_id="s1", prompt="one", agent_id="a")
    second = Task(id="t_2", parent_session_id="s1", prompt="two", agent_id="a")

    assert governor.admit_task(first, persist=persisted.append).accepted
    denied = governor.admit_task(second, persist=persisted.append)

    assert denied.accepted is False
    assert denied.task_id is None
    assert denied.reason_code == "quota.tasks_exhausted"
    assert [task.id for task in persisted] == ["t_1"]


def test_admission_retry_is_idempotent_and_conflict_is_rejected(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=4)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    persisted = []
    task = Task(id="t_same", parent_session_id="s1", prompt="same", agent_id="a")

    first = governor.admit_task(task, persist=persisted.append)
    retry = governor.admit_task(task, persist=persisted.append)
    conflict = governor.admit_task(
        Task(id="t_same", parent_session_id="s1", prompt="different", agent_id="a"),
        persist=persisted.append,
    )

    assert first.accepted and retry.accepted and retry.idempotent
    assert conflict.accepted is False
    assert conflict.reason_code == "quota.admission_conflict"
    assert len(persisted) == 1


def test_failed_task_publication_rolls_back_provisional_admission(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=4)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)

    with pytest.raises(OSError, match="disk full"):
        governor.admit_task(
            Task(id="t_fail", parent_session_id="s1", prompt="x", agent_id="a"),
            persist=lambda _task: (_ for _ in ()).throw(OSError("disk full")),
        )

    assert ledger.connection().execute(
        "SELECT COUNT(*) FROM task_admissions"
    ).fetchone()[0] == 0


def test_stopping_keeps_live_capacity_until_worker_release(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_live_per_session=1), scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    for task_id in ("t_1", "t_2"):
        governor.admit_task(
            Task(id=task_id, parent_session_id="s1", prompt=task_id, agent_id="a"),
            persist=lambda _task: None,
        )

    assert governor.try_start("t_1", owner_instance_id="worker") is True
    assert governor.try_start("t_2", owner_instance_id="worker") is False
    governor.request_stop("t_1", "cancel.user")
    assert governor.try_start("t_2", owner_instance_id="worker") is False
    generation = ledger.connection().execute(
        "SELECT lease_generation FROM task_admissions WHERE task_id = 't_1'"
    ).fetchone()[0]
    governor.release_task(
        "t_1", "cancel.user", owner_instance_id="worker",
        lease_generation=generation,
    )
    assert governor.try_start("t_2", owner_instance_id="worker") is True


def test_claim_next_skips_older_task_from_saturated_session(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_live_per_session=1), scheduler_capacity=2,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    for task_id, session_id in (
        ("s1_first", "s1"), ("s1_second", "s1"), ("s2_first", "s2"),
    ):
        governor.admit_task(
            Task(
                id=task_id, parent_session_id=session_id,
                prompt=task_id, agent_id="a",
            ),
            persist=lambda _task: None,
        )

    first = governor.claim_next(owner_instance_id="worker")
    second = governor.claim_next(owner_instance_id="worker")

    assert (first.task_id, first.session_id) == ("s1_first", "s1")
    assert (second.task_id, second.session_id) == ("s2_first", "s2")
    states = {
        row["task_id"]: row["state"]
        for row in ledger.connection().execute(
            "SELECT task_id, state FROM task_admissions"
        )
    }
    assert states == {
        "s1_first": "live", "s1_second": "queued", "s2_first": "live",
    }


def test_claim_next_serializes_tasks_that_share_session_cancel_scope(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_live_per_session=2), scheduler_capacity=3,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    for task_id, session_id in (
        ("s1_first", "s1"), ("s1_second", "s1"), ("s2_first", "s2"),
    ):
        governor.admit_task(
            Task(
                id=task_id, parent_session_id=session_id,
                prompt=task_id, agent_id="a",
            ),
            persist=lambda _task: None,
        )

    first = governor.claim_next(owner_instance_id="worker")
    second = governor.claim_next(owner_instance_id="worker")

    assert (first.task_id, first.session_id) == ("s1_first", "s1")
    assert (second.task_id, second.session_id) == ("s2_first", "s2")
    assert ledger.connection().execute(
        "SELECT state FROM task_admissions WHERE task_id = 's1_second'"
    ).fetchone()[0] == "queued"


def test_claim_next_cannot_be_claimed_twice_across_governors(tmp_path) -> None:
    ledger_path = tmp_path / "usage.db"
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    first_governor = ResourceGovernor(
        UsageLedger(ledger_path), limit_resolver=lambda _sid, _task: resolved,
    )
    second_governor = ResourceGovernor(
        UsageLedger(ledger_path), limit_resolver=lambda _sid, _task: resolved,
    )
    first_governor.admit_task(
        Task(id="only", parent_session_id="s1", prompt="only", agent_id="a"),
        persist=lambda _task: None,
    )

    claim = first_governor.claim_next(owner_instance_id="worker_1")

    assert claim.task_id == "only"
    assert second_governor.claim_next(owner_instance_id="worker_2") is None


def test_reconcile_finalizes_or_rolls_back_preparing_and_releases_missing_queue(
    tmp_path,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=2)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    tasks = {}
    for task_id in ("preparing_present", "preparing_missing", "queued_missing"):
        task = Task(
            id=task_id, parent_session_id="s1", prompt=task_id, agent_id="a",
        )
        governor.admit_task(task, persist=lambda accepted: tasks.setdefault(
            accepted.id, accepted,
        ))
    tasks.pop("preparing_missing")
    tasks.pop("queued_missing")
    ledger.connection().execute(
        "UPDATE task_admissions SET state = 'preparing' "
        "WHERE task_id IN ('preparing_present', 'preparing_missing')"
    )
    ledger.connection().commit()

    result = governor.reconcile(
        task_lookup=lambda _sid, task_id: tasks.get(task_id),
        mark_worker_lost=lambda _sid, _task_id: None,
        owner_is_alive=lambda _owner: False,
    )

    rows = {
        row["task_id"]: (row["state"], row["reason_code"])
        for row in ledger.connection().execute(
            "SELECT task_id, state, reason_code FROM task_admissions"
        )
    }
    assert result.finalized_preparing == 1
    assert result.rolled_back_preparing == 1
    assert result.released_missing == 1
    assert "preparing_missing" not in rows
    assert rows == {
        "preparing_present": ("queued", None),
        "queued_missing": ("released", "error.task_missing"),
    }


def test_reconcile_waits_for_lease_and_worker_lock_before_worker_lost_release(
    tmp_path,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    task = Task(id="live", parent_session_id="s1", prompt="live", agent_id="a")
    governor.admit_task(task, persist=lambda _task: None)
    governor.claim_next(owner_instance_id="worker_123_owner")
    ledger.connection().execute(
        "UPDATE task_admissions SET lease_expires_at = 20 WHERE task_id = 'live'"
    )
    ledger.connection().commit()
    lost = []
    lookup = lambda _sid, _task_id: task

    unexpired = governor.reconcile(
        task_lookup=lookup,
        mark_worker_lost=lambda _sid, task_id: lost.append(task_id),
        owner_is_alive=lambda _owner: False,
        now=19,
    )
    locked = governor.reconcile(
        task_lookup=lookup,
        mark_worker_lost=lambda _sid, task_id: lost.append(task_id),
        owner_is_alive=lambda _owner: True,
        now=21,
    )
    released = governor.reconcile(
        task_lookup=lookup,
        mark_worker_lost=lambda _sid, task_id: lost.append(task_id),
        owner_is_alive=lambda _owner: False,
        now=21,
    )
    repeated = governor.reconcile(
        task_lookup=lookup,
        mark_worker_lost=lambda _sid, task_id: lost.append(task_id),
        owner_is_alive=lambda _owner: False,
        now=22,
    )

    assert unexpired.released_worker_lost == 0
    assert locked.released_worker_lost == 0
    assert released.released_worker_lost == 1
    assert repeated.released_worker_lost == 0
    assert lost == ["live"]
    row = ledger.connection().execute(
        "SELECT state, reason_code FROM task_admissions WHERE task_id = 'live'"
    ).fetchone()
    assert tuple(row) == ("released", "error.worker_lost")


def test_live_lease_mutations_are_fenced_by_owner(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    governor.admit_task(
        Task(id="live", parent_session_id="s1", prompt="live", agent_id="a"),
        persist=lambda _task: None,
    )
    claim = governor.claim_next(owner_instance_id="worker_current")

    assert governor.renew_lease(
        "live", owner_instance_id="worker_stale",
        lease_generation=claim.lease_generation,
    ) is False
    assert governor.release_task(
        "live", "completed", owner_instance_id="worker_stale",
        lease_generation=claim.lease_generation,
    ) is False
    assert ledger.connection().execute(
        "SELECT state FROM task_admissions WHERE task_id = 'live'"
    ).fetchone()[0] == "live"
    assert governor.release_task(
        "live", "completed", owner_instance_id="worker_current",
        lease_generation=claim.lease_generation,
    ) is True


def test_worker_lost_revokes_generation_before_terminal_store_mutation(
    tmp_path,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    task = Task(id="live", parent_session_id="s1", prompt="live", agent_id="a")
    governor.admit_task(task, persist=lambda _task: None)
    claim = governor.claim_next(owner_instance_id="worker_stale")
    ledger.connection().execute(
        "UPDATE task_admissions SET lease_expires_at = 20 WHERE task_id = 'live'"
    )
    ledger.connection().commit()
    mutations: list[str] = []

    result = governor.reconcile(
        task_lookup=lambda _sid, _task_id: task,
        mark_worker_lost=lambda _sid, _task_id: mutations.append("worker_lost"),
        owner_is_alive=lambda _owner: False,
        now=21,
    )
    stale_finalized = governor.finalize_task(
        "live", "completed",
        owner_instance_id="worker_stale",
        lease_generation=claim.lease_generation,
        mutate=lambda: mutations.append("completed"),
    )

    row = ledger.connection().execute(
        "SELECT state, owner_instance_id, lease_generation "
        "FROM task_admissions WHERE task_id = 'live'"
    ).fetchone()
    assert result.released_worker_lost == 1
    assert stale_finalized is False
    assert mutations == ["worker_lost"]
    assert tuple(row) == ("released", None, claim.lease_generation + 1)


def test_stopping_finalize_keeps_claim_when_store_mutation_fails(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    task = Task(id="stopping", parent_session_id="s1", prompt="p", agent_id="a")
    governor.admit_task(task, persist=lambda _task: None)
    claim = governor.claim_next(owner_instance_id="worker")
    governor.request_stop(task.id, "cancel.user")

    def fail_mutation(_reason_code: str | None) -> None:
        raise RuntimeError("task store unavailable")

    with pytest.raises(RuntimeError, match="task store unavailable"):
        governor.finalize_stopping_task(
            task.id,
            owner_instance_id="worker",
            lease_generation=claim.lease_generation,
            mutate=fail_mutation,
        )

    row = ledger.connection().execute(
        "SELECT state, reason_code FROM task_admissions WHERE task_id = ?",
        (task.id,),
    ).fetchone()
    assert tuple(row) == ("stopping", "cancel.user")


def test_stopping_finalize_mutates_then_releases_current_claim(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    task = Task(id="stopping", parent_session_id="s1", prompt="p", agent_id="a")
    governor.admit_task(task, persist=lambda _task: None)
    claim = governor.claim_next(owner_instance_id="worker")
    governor.request_stop(task.id, "cancel.user")
    mutations: list[str | None] = []

    finalized = governor.finalize_stopping_task(
        task.id,
        owner_instance_id="worker",
        lease_generation=claim.lease_generation,
        mutate=mutations.append,
    )

    row = ledger.connection().execute(
        "SELECT state, reason_code FROM task_admissions WHERE task_id = ?",
        (task.id,),
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
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    governor.admit_task(
        Task(id="timed", parent_session_id="s1", prompt="timed", agent_id="a"),
        persist=lambda _task: None,
    )
    queued = ledger.connection().execute(
        "SELECT started_at, last_activity_at FROM task_admissions WHERE task_id = 'timed'"
    ).fetchone()

    assert tuple(queued) == (None, None)
    assert governor.task_time_limits("timed") == (10, 4)
    claim = governor.claim_next(owner_instance_id="worker")
    live = ledger.connection().execute(
        "SELECT started_at, last_activity_at FROM task_admissions WHERE task_id = 'timed'"
    ).fetchone()
    assert live[0] is not None
    assert live[1] == live[0]


def test_meaningful_activity_is_owner_fenced_and_keepalive_is_ignored(
    tmp_path, monkeypatch,
) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(idle_timeout_seconds=4), scheduler_capacity=1,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    governor.admit_task(
        Task(id="timed", parent_session_id="s1", prompt="timed", agent_id="a"),
        persist=lambda _task: None,
    )
    claim = governor.claim_next(owner_instance_id="worker")
    before = ledger.connection().execute(
        "SELECT last_activity_at FROM task_admissions WHERE task_id = 'timed'"
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
        "SELECT last_activity_at FROM task_admissions WHERE task_id = 'timed'"
    ).fetchone()[0]
    assert after == before + 5


def test_child_progress_updates_live_parent_activity(tmp_path, monkeypatch) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(idle_timeout_seconds=5), scheduler_capacity=2,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    parent = Task(id="parent", parent_session_id="s1", prompt="p", agent_id="a")
    child = Task(
        id="child", parent_session_id="s2", parent_task_id="parent",
        prompt="c", agent_id="a",
    )
    governor.admit_task(parent, persist=lambda _task: None)
    governor.admit_task(child, persist=lambda _task: None)
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
        "SELECT task_id, last_activity_at FROM task_admissions ORDER BY task_id"
    ).fetchall()
    assert [(row["task_id"], row["last_activity_at"]) for row in rows] == [
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
        task=ResourceLimits(max_runtime_seconds=3, idle_timeout_seconds=2),
        scheduler_capacity=2,
    )
    governor = ResourceGovernor(
        ledger,
        limit_resolver=lambda _sid, task: (
            parent_limits if task.id == "parent" else session_limits
        ),
        session_limit_resolver=lambda _sid: session_limits,
    )
    governor.admit_task(
        Task(id="parent", parent_session_id="s1", prompt="p", agent_id="a"),
        persist=lambda _task: None,
    )
    governor.admit_task(
        Task(
            id="child", parent_session_id="s1", parent_task_id="parent",
            prompt="c", agent_id="a",
        ),
        persist=lambda _task: None,
    )

    assert governor.task_time_limits("child") == (3, 2)


def test_releasing_queued_task_keeps_cumulative_admission(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_queued_per_session=1, max_tasks_per_session=1),
        scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    governor.admit_task(
        Task(id="t_1", parent_session_id="s1", prompt="one", agent_id="a"),
        persist=lambda _task: None,
    )
    governor.release_task("t_1", "cancel.user")

    denied = governor.admit_task(
        Task(id="t_2", parent_session_id="s1", prompt="two", agent_id="a"),
        persist=lambda _task: None,
    )

    assert denied.reason_code == "quota.tasks_exhausted"


def test_task_store_serializes_cross_process_writers(tmp_path, monkeypatch) -> None:
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("requires fork to share the isolated store patch")
    from openprogram.agent.task import store as task_store

    path = tmp_path / "tasks.json"
    monkeypatch.setattr(task_store, "_ensure_session", lambda _sid: path)
    monkeypatch.setattr(task_store, "_commit", lambda *_args, **_kwargs: None)
    original_load = task_store._load_raw

    def slow_load(target):
        rows = original_load(target)
        time.sleep(0.03)
        return rows

    monkeypatch.setattr(task_store, "_load_raw", slow_load)

    def write_one(index: int) -> None:
        task_store.save_task(
            "s1", Task(
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
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    for task_id in ("t_1", "t_2"):
        governor.admit_task(
            Task(id=task_id, parent_session_id="s1", prompt=task_id, agent_id="a"),
            persist=lambda _task: None,
        )
    decisions = []

    def reserve(task_id: str) -> None:
        decisions.append(governor.reserve_tokens(task_id, 60))

    threads = [threading.Thread(target=reserve, args=(task_id,)) for task_id in ("t_1", "t_2")]
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
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    governor.admit_task(
        Task(id="t_1", parent_session_id="s1", prompt="one", agent_id="a"),
        persist=lambda _task: None,
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
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    governor.admit_task(
        Task(id="t_1", parent_session_id="s1", prompt="one", agent_id="a"),
        persist=lambda _task: None,
    )
    reserved = governor.reserve_tokens("t_1", 80)
    assert reserved.accepted
    governor.start_reservation(reserved.reservation_id)

    governor.settle_reservation(
        reserved.reservation_id,
        UsageEvent(
            event_id="actual", task_id="t_1", session_id="s1", provider="p",
            model_id="m", input_tokens=20, output_tokens=10, total_tokens=30,
            cost_source="model_catalog",
        ),
    )

    assert governor.reserve_tokens("t_1", 70).accepted
    assert ledger.query(filters={"task_id": "t_1"})[0].total_tokens == 30


def test_task_budget_does_not_narrow_shared_session_scope(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    session_limits = resolve_resource_limits(
        ResourceLimits(max_total_tokens=100), scheduler_capacity=4,
    )
    task_limits = resolve_resource_limits(
        ResourceLimits(max_total_tokens=100),
        task=ResourceLimits(max_total_tokens=50), scheduler_capacity=4,
    )
    governor = ResourceGovernor(
        ledger,
        limit_resolver=lambda _sid, task: task_limits if task.id == "t_1" else session_limits,
        session_limit_resolver=lambda _sid: session_limits,
    )
    for task_id in ("t_1", "t_2"):
        governor.admit_task(
            Task(id=task_id, parent_session_id="s1", prompt=task_id, agent_id="a"),
            persist=lambda _task: None,
        )

    assert governor.reserve_tokens("t_1", 50).accepted
    assert governor.reserve_tokens("t_2", 50).accepted
    assert governor.reserve_tokens("t_2", 1).reason_code == "quota.token_exhausted"


def test_unknown_parent_task_still_inherits_session_budget(tmp_path) -> None:
    ledger = UsageLedger(tmp_path / "usage.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_total_tokens=100), scheduler_capacity=4,
    )
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    governor.admit_task(
        Task(
            id="child", parent_session_id="s1", parent_task_id="missing",
            prompt="child", agent_id="a",
        ),
        persist=lambda _task: None,
    )
    governor.admit_task(
        Task(id="sibling", parent_session_id="s1", prompt="sibling", agent_id="a"),
        persist=lambda _task: None,
    )

    assert governor.reserve_tokens("child", 100).accepted
    assert governor.reserve_tokens("sibling", 1).reason_code == "quota.token_exhausted"

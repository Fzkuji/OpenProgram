"""Admissions and budgets live in the run tables and survive a restart."""
from __future__ import annotations

import sqlite3

import pytest

from openprogram.models.usage.event import UsageEvent
from openprogram.models.usage.ledger import UsageLedger
from openprogram.runtime.execution.store.run_tables import RunTables
from openprogram.runtime.jobs.resource_governance import (
    ResourceGovernor,
    ResourceLimits,
    resolve_resource_limits,
)
from openprogram.runtime.jobs.types import Job


@pytest.fixture(autouse=True)
def _worker_lock_is_held(monkeypatch):
    monkeypatch.setattr(
        "openprogram.operations.worker.lock.is_held_by", lambda _pid: True, raising=False,
    )


def _governor(path, ledger=None, **limits):
    resolved = resolve_resource_limits(ResourceLimits(**limits), scheduler_capacity=4)
    return ResourceGovernor(
        ledger or UsageLedger(path), limit_resolver=lambda _session, _job: resolved,
    )


def _admit(governor, job_id, session="s1"):
    job = Job(id=job_id, parent_session_id=session, prompt="p", agent_id="a")
    assert governor.admit_job(job, persist=lambda _job: None).accepted
    return job


def _restart(governor):
    """A new process: the same log folded into new tables."""
    tables = RunTables(governor.ledger.run_tables().log)
    governor.ledger._run_tables = tables  # noqa: SLF001
    governor._opened_tables = None  # noqa: SLF001
    governor._tables()  # noqa: SLF001
    return tables


def _model():
    return type("M", (), {"max_tokens": 100, "cost": None})()


def _event(event_id="event"):
    return UsageEvent(
        event_id=event_id, session_id="s1", provider="p", model_id="m",
        input_tokens=5, output_tokens=7, total_tokens=12, cost_source="unknown",
    )


def test_admission_and_budget_are_rebuilt_after_a_restart(tmp_path):
    governor = _governor(tmp_path / "usage.db")
    job = _admit(governor, "one")
    claim = governor.claim_next(owner_instance_id="worker")

    tables = _restart(governor)

    admission = tables.handle.first("job_admissions", job_id="one")
    assert (admission["state"], admission["lease_generation"]) == ("live", claim.lease_generation)
    assert tables.handle.first("budget_scopes", job_id="one")["budget_scope_id"] == job.budget_scope_id
    assert tables.handle.first("budget_scopes", scope_kind="session", session_id="s1") is not None


def test_a_rolled_back_admission_stays_gone_after_a_restart(tmp_path):
    governor = _governor(tmp_path / "usage.db")

    def fail(_job):
        raise OSError("store unavailable")

    with pytest.raises(OSError):
        governor.admit_job(Job(id="one", parent_session_id="s1", prompt="p", agent_id="a"), persist=fail)

    tables = _restart(governor)
    assert tables.handle.first("job_admissions", job_id="one") is None
    assert tables.handle.first("budget_scopes", job_id="one") is None


def test_a_session_left_unloaded_is_found_through_its_budget(tmp_path):
    governor = _governor(tmp_path / "usage.db", max_jobs_per_session=1)
    _admit(governor, "one")

    fresh = _governor(tmp_path / "usage.db", ledger=governor.ledger, max_jobs_per_session=1)
    _restart(fresh)

    second = Job(id="two", parent_session_id="s1", prompt="p", agent_id="a")
    assert fresh.admit_job(second, persist=lambda _job: None).reason_code == "quota.jobs_exhausted"


def test_a_started_reservation_keeps_its_exposure_across_a_restart(tmp_path):
    governor = _governor(tmp_path / "usage.db", max_total_tokens=10_000)
    job = _admit(governor, "one")
    plan = governor.reserve_provider_request(
        job.id, input_token_upper_bound=10, requested_max_output_tokens=20, model=_model(),
    )
    governor.start_provider_request(plan.reservation_id)

    _restart(governor)

    assert governor.recover_provider_reservations(now=10**12) == 0
    assert governor.ledger.resource_counts("s1", "one")["reserved_tokens"] == plan.token_reservation


def test_stopping_between_the_event_and_the_settlement_is_finished_once(tmp_path, monkeypatch):
    governor = _governor(tmp_path / "usage.db", max_total_tokens=10_000)
    ledger = governor.ledger
    job = _admit(governor, "one")
    plan = governor.reserve_provider_request(
        job.id, input_token_upper_bound=10, requested_max_output_tokens=20, model=_model(),
    )
    governor.start_provider_request(plan.reservation_id)

    # The process stops after the event commits: the settlement never runs.
    monkeypatch.setattr(ledger, "after_commit", lambda _step: None)
    governor.settle_provider_request(plan.reservation_id, _event())
    monkeypatch.undo()
    monkeypatch.setattr(
        "openprogram.operations.worker.lock.is_held_by", lambda _pid: True, raising=False,
    )

    tables = _restart(governor).handle
    assert tables.rows("usage_reservations", state="started")

    assert governor.recover_provider_reservations(now=1) == 0

    token = tables.get("usage_reservations", plan.reservation_id + ":token")
    assert (token["state"], token["settled_event_id"]) == ("settled", "event")
    events = ledger.connection().execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
    assert events == 1
    assert ledger.resource_counts("s1", "one")["reserved_tokens"] == 0


def test_a_failed_event_write_changes_no_reservation(tmp_path, monkeypatch):
    governor = _governor(tmp_path / "usage.db", max_total_tokens=10_000)
    job = _admit(governor, "one")
    plan = governor.reserve_provider_request(
        job.id, input_token_upper_bound=10, requested_max_output_tokens=20, model=_model(),
    )
    governor.start_provider_request(plan.reservation_id)
    monkeypatch.setattr(
        governor.ledger, "append_in_transaction",
        lambda _connection, _event: (_ for _ in ()).throw(RuntimeError("disk full")),
    )

    with pytest.raises(RuntimeError, match="disk full"):
        governor.settle_provider_request(plan.reservation_id, _event())

    rows = governor.ledger.run_tables().handle.rows("usage_reservations")
    assert {row["state"] for row in rows} == {"started"}


def _older_usage_file(path):
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE budget_scopes (
            budget_scope_id TEXT PRIMARY KEY, scope_kind TEXT NOT NULL, session_id TEXT NOT NULL,
            job_id TEXT UNIQUE, parent_scope_id TEXT, max_total_tokens INTEGER,
            max_cost_microusd INTEGER, max_runtime_seconds INTEGER,
            idle_timeout_seconds INTEGER, created_at REAL NOT NULL
        );
        CREATE TABLE job_admissions (
            admission_id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL, session_id TEXT NOT NULL,
            parent_job_id TEXT, caller_session_id TEXT, caller_turn_id TEXT,
            creates_agent INTEGER NOT NULL, request_fingerprint TEXT NOT NULL,
            budget_scope_id TEXT NOT NULL, state TEXT NOT NULL, admitted_seq INTEGER NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE job_finalizations (
            job_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, owner_instance_id TEXT NOT NULL,
            lease_generation INTEGER NOT NULL, fields_json TEXT NOT NULL, state TEXT NOT NULL,
            created_at REAL NOT NULL, completed_at REAL
        );
        CREATE TABLE usage_reservations (
            reservation_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, budget_scope_id TEXT NOT NULL,
            kind TEXT NOT NULL, state TEXT NOT NULL, reserved_tokens INTEGER,
            reserved_cost_microusd INTEGER, request_started_at REAL, settled_event_id TEXT,
            expires_at REAL
        );
        INSERT INTO budget_scopes VALUES ('limited', 'session', 's1', NULL, NULL, 500, NULL, NULL, NULL, 1);
        INSERT INTO budget_scopes VALUES ('under', 'job', 's1', 'old', 'limited', NULL, NULL, NULL, NULL, 1);
        INSERT INTO budget_scopes VALUES ('plain', 'session', 's2', NULL, NULL, NULL, NULL, NULL, NULL, 1);
        INSERT INTO budget_scopes VALUES ('finished', 'job', 's2', 'done', 'plain', NULL, NULL, NULL, NULL, 1);
        INSERT INTO job_admissions VALUES
            ('a1', 'old', 's1', NULL, NULL, NULL, 0, 'f', 'under', 'queued', 1, 1);
        INSERT INTO job_admissions VALUES
            ('a2', 'done', 's2', NULL, NULL, NULL, 0, 'f', 'finished', 'released', 2, 1);
        INSERT INTO usage_reservations VALUES
            ('r1', 'old', 'under', 'token', 'started', 40, NULL, 1, NULL, NULL);
        INSERT INTO usage_reservations VALUES
            ('r2', 'done', 'finished', 'token', 'settled', 40, NULL, 1, 'e', NULL);
    """)
    connection.commit()
    connection.close()


def test_rows_in_an_older_usage_file_move_once_and_its_tables_go(tmp_path):
    path = tmp_path / "usage.db"
    _older_usage_file(path)
    governor = _governor(path)

    assert governor.has_live_jobs() is False

    tables = governor.ledger.run_tables().handle
    assert {row["budget_scope_id"] for row in tables.rows("budget_scopes")} == {"limited", "under"}
    assert [row["job_id"] for row in tables.rows("job_admissions")] == ["old"]
    reservation = tables.get("usage_reservations", "r1")
    assert (reservation["session_id"], reservation["state"]) == ("s1", "started")
    assert governor.ledger.resource_counts("s1", "old")["reserved_tokens"] == 40
    assert governor.ledger.stored_run_table_rows() is None

    restarted = _restart(governor).handle
    assert restarted.get("usage_reservations", "r1")["reserved_tokens"] == 40
    assert {row["budget_scope_id"] for row in restarted.rows("budget_scopes")} == {"limited", "under"}


def test_stopping_before_the_old_tables_are_dropped_moves_nothing_twice(tmp_path, monkeypatch):
    path = tmp_path / "usage.db"
    _older_usage_file(path)
    governor = _governor(path)
    ledger = governor.ledger
    monkeypatch.setattr(
        ledger, "drop_stored_run_tables",
        lambda: (_ for _ in ()).throw(OSError("stopped")),
    )
    assert governor.has_live_jobs() is False
    assert ledger.stored_run_table_rows() is not None
    monkeypatch.undo()
    monkeypatch.setattr(
        "openprogram.operations.worker.lock.is_held_by", lambda _pid: True, raising=False,
    )

    second = _governor(path, ledger=ledger)
    assert second.has_live_jobs() is False
    tables = ledger.run_tables().handle
    assert tables.count("job_admissions", job_id="old") == 1
    assert ledger.stored_run_table_rows() is None

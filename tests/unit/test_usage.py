"""Phase 0 metering tests — ledger persistence/idempotency/aggregation,
contextvar scope, and recorder event assembly."""
from __future__ import annotations

import asyncio

import pytest

from openprogram.usage.context import (
    UsageContext,
    apply_snapshot,
    current_usage_context,
    snapshot,
    usage_scope,
)
from openprogram.usage import context as _ctx_mod
from openprogram.usage.event import UsageEvent
from openprogram.usage.ledger import UsageLedger
from openprogram.usage import recorder as _recorder
import sqlite3


@pytest.fixture(autouse=True)
def reset_usage_context():
    """The usage contextvar is process-global and the dispatcher sets it
    bare (no reset — it intends to span a whole sync turn), so an earlier
    test can leak a non-default context into these tests. Pin a clean
    default per test and restore after."""
    token = _ctx_mod._current.set(UsageContext())
    try:
        yield
    finally:
        _ctx_mod._current.reset(token)


@pytest.fixture
def ledger(tmp_path):
    return UsageLedger(db_path=tmp_path / "usage.db")


def _ev(**kw):
    base = dict(
        ts=1000.0, session_id="s1", call_kind="chat",
        provider="anthropic", model_id="claude-opus-4-6",
        input_tokens=100, output_tokens=20, total_tokens=120,
        cost_total=0.01,
    )
    base.update(kw)
    return UsageEvent(**base)


# ledger

def test_append_and_query_totals(ledger):
    ledger.append(_ev(input_tokens=100, output_tokens=20))
    ledger.append(_ev(input_tokens=50, output_tokens=10))
    rows = ledger.query()
    assert len(rows) == 1
    assert rows[0].input_tokens == 150
    assert rows[0].output_tokens == 30
    assert rows[0].events == 2


def test_append_idempotent_on_event_id(ledger):
    e = _ev(event_id="fixed-id", input_tokens=100)
    ledger.append(e)
    ledger.append(e)  # same id — must not double count
    rows = ledger.query()
    assert rows[0].events == 1
    assert rows[0].input_tokens == 100


def test_group_by_model(ledger):
    ledger.append(_ev(model_id="claude-opus-4-6", input_tokens=100))
    ledger.append(_ev(model_id="gpt-5.2", input_tokens=40))
    ledger.append(_ev(model_id="claude-opus-4-6", input_tokens=60))
    rows = {r.keys["model_id"]: r for r in ledger.query(group_by=["model_id"])}
    assert rows["claude-opus-4-6"].input_tokens == 160
    assert rows["gpt-5.2"].input_tokens == 40


def test_group_by_call_kind(ledger):
    ledger.append(_ev(call_kind="chat", input_tokens=100))
    ledger.append(_ev(call_kind="compaction", input_tokens=30))
    rows = {r.keys["call_kind"]: r for r in ledger.query(group_by=["call_kind"])}
    assert rows["chat"].input_tokens == 100
    assert rows["compaction"].input_tokens == 30


def test_time_bucket_day(ledger):
    day = 86400
    ledger.append(_ev(ts=day * 100 + 10, input_tokens=100))
    ledger.append(_ev(ts=day * 100 + 20, input_tokens=50))
    ledger.append(_ev(ts=day * 101 + 5, input_tokens=70))
    rows = {r.keys["day"]: r for r in ledger.query(group_by=["day"])}
    assert rows[100].input_tokens == 150
    assert rows[101].input_tokens == 70


def test_since_until_filter(ledger):
    ledger.append(_ev(ts=100, input_tokens=10))
    ledger.append(_ev(ts=200, input_tokens=20))
    ledger.append(_ev(ts=300, input_tokens=30))
    rows = ledger.query(since=150, until=250)
    assert rows[0].input_tokens == 20
    assert rows[0].events == 1


def test_filter_by_session(ledger):
    ledger.append(_ev(session_id="a", input_tokens=10))
    ledger.append(_ev(session_id="b", input_tokens=20))
    rows = ledger.query(filters={"session_id": "a"})
    assert rows[0].input_tokens == 10


def test_query_empty_ledger(ledger):
    # no group_by → one all-zero summary row from SUM over zero rows
    rows = ledger.query()
    assert len(rows) == 1
    assert rows[0].input_tokens == 0
    assert rows[0].events == 0


def test_existing_usage_database_is_migrated_and_reopens(tmp_path):
    path = tmp_path / "usage.db"
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE usage_events (
            event_id TEXT PRIMARY KEY, ts REAL NOT NULL, session_id TEXT,
            parent_session_id TEXT, agent_id TEXT, call_kind TEXT NOT NULL,
            call_label TEXT, origin_pid INTEGER, provider TEXT NOT NULL,
            api TEXT, model_id TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0, cost_total REAL NOT NULL DEFAULT 0,
            cost_input REAL, cost_output REAL, cost_cache_read REAL,
            cost_cache_write REAL, cost_source TEXT, token_source TEXT,
            schema_version INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("""
        INSERT INTO usage_events (
            event_id, ts, call_kind, provider, model_id, input_tokens,
            total_tokens, cost_total, cost_source
        ) VALUES ('old', 1, 'chat', 'p', 'm', 7, 7, 0, 'unknown')
    """)
    conn.commit()
    conn.close()

    upgraded = UsageLedger(path)
    assert upgraded.query()[0].input_tokens == 7
    upgraded.close()
    reopened = UsageLedger(path)
    columns = {
        row[1] for row in reopened.connection().execute("PRAGMA table_info(usage_events)")
    }
    tables = {
        row[0] for row in reopened.connection().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"job_id", "budget_scope_id", "reservation_id"} <= columns
    assert {"job_admissions", "budget_scopes", "usage_reservations"} <= tables
    assert reopened.connection().execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_legacy_task_usage_schema_is_renamed_to_jobs(tmp_path):
    path = tmp_path / "usage.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE usage_events (
            event_id TEXT PRIMARY KEY, ts REAL NOT NULL, session_id TEXT,
            parent_session_id TEXT, agent_id TEXT, call_kind TEXT NOT NULL,
            call_label TEXT, origin_pid INTEGER, provider TEXT NOT NULL,
            api TEXT, model_id TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0, cost_total REAL NOT NULL DEFAULT 0,
            cost_input REAL, cost_output REAL, cost_cache_read REAL,
            cost_cache_write REAL, cost_source TEXT, token_source TEXT,
            schema_version INTEGER NOT NULL DEFAULT 1, task_id TEXT,
            budget_scope_id TEXT, reservation_id TEXT
        );
        CREATE INDEX ix_usage_task ON usage_events(task_id);
        CREATE TABLE task_admissions (
            admission_id TEXT PRIMARY KEY, task_id TEXT UNIQUE NOT NULL,
            session_id TEXT NOT NULL, parent_task_id TEXT, caller_session_id TEXT,
            caller_turn_id TEXT, creates_agent INTEGER NOT NULL,
            request_fingerprint TEXT NOT NULL, budget_scope_id TEXT NOT NULL,
            dispatch_ready INTEGER NOT NULL DEFAULT 1,
            borrowed_parent_task_id TEXT, resume_parent_msg_id TEXT, state TEXT NOT NULL,
            admitted_seq INTEGER NOT NULL, owner_instance_id TEXT,
            lease_generation INTEGER NOT NULL DEFAULT 0, lease_expires_at REAL,
            created_at REAL NOT NULL, started_at REAL, last_activity_at REAL,
            released_at REAL, reason_code TEXT
        );
        CREATE TABLE task_finalizations (
            task_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            owner_instance_id TEXT NOT NULL, lease_generation INTEGER NOT NULL,
            fields_json TEXT NOT NULL, state TEXT NOT NULL,
            created_at REAL NOT NULL, completed_at REAL
        );
        CREATE TABLE budget_scopes (
            budget_scope_id TEXT PRIMARY KEY,
            scope_kind TEXT NOT NULL CHECK (scope_kind IN ('session','task')),
            session_id TEXT NOT NULL, task_id TEXT UNIQUE, parent_scope_id TEXT,
            max_total_tokens INTEGER, max_cost_microusd INTEGER,
            max_runtime_seconds INTEGER, idle_timeout_seconds INTEGER,
            created_at REAL NOT NULL
        );
        CREATE TABLE usage_reservations (
            reservation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
            budget_scope_id TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
            reserved_tokens INTEGER, reserved_cost_microusd INTEGER,
            request_started_at REAL, settled_event_id TEXT, expires_at REAL
        );
        INSERT INTO usage_events (
            event_id, ts, call_kind, provider, model_id, task_id
        ) VALUES ('event', 1, 'chat', 'p', 'm', 't_1');
        INSERT INTO budget_scopes (
            budget_scope_id, scope_kind, session_id, task_id, created_at
        ) VALUES ('scope', 'task', 's_1', 't_1', 1);
    """)
    conn.commit()
    conn.close()

    ledger = UsageLedger(path)
    migrated = ledger.connection()

    tables = {
        row[0] for row in migrated.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "job_admissions" in tables
    assert "job_finalizations" in tables
    assert "task_admissions" not in tables
    assert migrated.execute(
        "SELECT job_id FROM usage_events WHERE event_id = 'event'"
    ).fetchone()[0] == "t_1"
    assert tuple(migrated.execute(
        "SELECT scope_kind, job_id FROM budget_scopes WHERE budget_scope_id = 'scope'"
    ).fetchone()) == ("job", "t_1")
    indexes = {
        row[0] for row in migrated.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    assert "ix_usage_task" not in indexes
    assert "ix_usage_job" in indexes
    ledger.close()

    reopened = UsageLedger(path)
    reopened_indexes = {
        row[0] for row in reopened.connection().execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    assert "ix_usage_task" not in reopened_indexes
    assert "ix_usage_job" in reopened_indexes
    reopened.close()


def test_usage_aggregation_tracks_unknown_cost_events(ledger):
    ledger.append(_ev(event_id="known", cost_total=0.5, cost_source="model_catalog"))
    ledger.append(_ev(event_id="unknown", cost_total=0.0, cost_source="unknown"))

    row = ledger.query()[0]

    assert row.cost_total == 0.5
    assert row.cost_known is False
    assert row.unknown_cost_events == 1


# contextvar scope

def test_usage_scope_sets_and_resets():
    assert current_usage_context().call_kind == "unknown"
    with usage_scope(call_kind="chat", agent_id="ag1"):
        c = current_usage_context()
        assert c.call_kind == "chat"
        assert c.agent_id == "ag1"
    assert current_usage_context().call_kind == "unknown"


def test_usage_scope_nesting_inherits():
    with usage_scope(call_kind="exec", agent_id="ag1"):
        with usage_scope(call_label="inner"):
            c = current_usage_context()
            assert c.call_kind == "exec"      # inherited
            assert c.agent_id == "ag1"        # inherited
            assert c.call_label == "inner"    # overridden


def test_snapshot_roundtrip():
    from openprogram.usage.context import UsageContext, _current
    with usage_scope(call_kind="memory", parent_session_id="p1"):
        snap = snapshot()
    # apply_snapshot sets the process-global contextvar; restore the default
    # afterwards so the value doesn't leak into later tests.
    token = _current.set(UsageContext())
    try:
        apply_snapshot(snap)
        c = current_usage_context()
        assert c.call_kind == "memory"
        assert c.parent_session_id == "p1"
    finally:
        _current.reset(token)


def test_scope_propagates_into_async_task():
    async def main():
        with usage_scope(call_kind="subagent"):
            async def child():
                return current_usage_context().call_kind
            return await asyncio.create_task(child())
    assert asyncio.run(main()) == "subagent"


# recorder

class _FakeUsage:
    def __init__(self, i, o, cr=0, cw=0):
        self.input, self.output, self.cache_read, self.cache_write = i, o, cr, cw
        self.cost = None


class _FakeMsg:
    def __init__(self, usage):
        self.usage = usage


class _FakeModel:
    provider = "anthropic"
    api = "anthropic-messages"
    id = "claude-opus-4-6"
    cost = None  # unknown pricing → cost_source unknown


def test_recorder_records_into_ledger(ledger, monkeypatch):
    monkeypatch.setattr(_recorder, "default_ledger", ledger)
    with usage_scope(call_kind="compaction"):
        ev = _recorder.record_message(
            _FakeModel(), _FakeMsg(_FakeUsage(100, 20)), session_id="s9")
    assert ev is not None
    assert ev.call_kind == "compaction"
    assert ev.session_id == "s9"
    rows = ledger.query(group_by=["call_kind"])
    assert rows[0].keys["call_kind"] == "compaction"
    assert rows[0].input_tokens == 100


def test_recorder_skips_zero_token_call(ledger, monkeypatch):
    monkeypatch.setattr(_recorder, "default_ledger", ledger)
    ev = _recorder.record_message(_FakeModel(), _FakeMsg(_FakeUsage(0, 0)))
    assert ev is None
    assert ledger.query()[0].events == 0


def test_recorder_never_raises_on_bad_input(ledger, monkeypatch):
    monkeypatch.setattr(_recorder, "default_ledger", ledger)
    assert _recorder.record_message(None, None) is None
    assert _recorder.record_message(_FakeModel(), _FakeMsg(None)) is None

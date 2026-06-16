"""Phase 0 metering tests — ledger persistence/idempotency/aggregation,
contextvar scope, and recorder event assembly."""
from __future__ import annotations

import asyncio

import pytest

from openprogram.metering.context import (
    apply_snapshot,
    current_usage_context,
    snapshot,
    usage_scope,
)
from openprogram.metering.event import UsageEvent
from openprogram.metering.ledger import UsageLedger
from openprogram.metering import recorder as _recorder


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


# ── ledger ──

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


# ── contextvar scope ──

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
    with usage_scope(call_kind="memory", parent_session_id="p1"):
        snap = snapshot()
    apply_snapshot(snap)
    c = current_usage_context()
    assert c.call_kind == "memory"
    assert c.parent_session_id == "p1"


def test_scope_propagates_into_async_task():
    async def main():
        with usage_scope(call_kind="subagent"):
            async def child():
                return current_usage_context().call_kind
            return await asyncio.create_task(child())
    assert asyncio.run(main()) == "subagent"


# ── recorder ──

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

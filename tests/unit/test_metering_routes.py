"""Phase 5 metering routes — /api/usage/summary + /api/usage/trend backed by
the ledger. Verifies aggregation shape, by_kind/by_model breakdowns, the
day/hour trend buckets, and since/until time filtering."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.metering.event import UsageEvent
from openprogram.metering.ledger import UsageLedger


DAY = 86400


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient whose usage routes read a temp ledger seeded with a few
    events spanning 3 days, 2 models, 3 call_kinds."""
    lg = UsageLedger(db_path=tmp_path / "usage.db")
    # The routes import default_ledger lazily inside _ledger(); patch the name
    # in the ledger module so default_ledger resolves to our temp instance.
    import openprogram.metering.ledger as _lmod
    monkeypatch.setattr(_lmod, "default_ledger", lg)

    base = 1_000_000.0  # fixed epoch so day buckets are deterministic
    seed = [
        # (ts, kind, model, provider, inp, out, cr, cost)
        (base + 0 * DAY, "chat", "claude-opus-4-8", "anthropic", 100, 20, 50, 0.10),
        (base + 0 * DAY, "exec", "claude-opus-4-8", "anthropic", 400, 90, 0, 0.40),
        (base + 1 * DAY, "chat", "gpt-5.5", "openai", 200, 50, 0, 0.05),
        (base + 1 * DAY, "tool", "gpt-5.5", "openai", 80, 20, 0, 0.01),
        (base + 2 * DAY, "chat", "claude-opus-4-8", "anthropic", 90, 28, 0, 0.03),
    ]
    for ts, kind, model, prov, inp, out, cr, cost in seed:
        lg.append(UsageEvent(
            ts=ts, session_id="s", call_kind=kind, provider=prov, model_id=model,
            input_tokens=inp, output_tokens=out, cache_read_tokens=cr,
            total_tokens=inp + out, cost_total=cost, cost_source="model_catalog",
        ))

    app = FastAPI()
    from openprogram.webui.routes import usage as routes_usage
    routes_usage.register(app)
    c = TestClient(app)
    c._seed_base = base  # type: ignore[attr-defined]
    return c


# ── summary ──

def test_summary_totals(client):
    d = client.get("/api/usage/summary").json()
    t = d["totals"]
    assert t["input_tokens"] == 100 + 400 + 200 + 80 + 90
    assert t["output_tokens"] == 20 + 90 + 50 + 20 + 28
    assert t["total_tokens"] == t["input_tokens"] + t["output_tokens"]
    assert t["cache_read_tokens"] == 50
    assert t["events"] == 5
    assert abs(t["cost"] - (0.10 + 0.40 + 0.05 + 0.01 + 0.03)) < 1e-9


def test_summary_by_model_sorted_desc(client):
    rows = client.get("/api/usage/summary").json()["by_model"]
    assert len(rows) == 2
    # opus: in 100+400+90=590 out 138 -> 728 ; gpt: in 280 out 70 -> 350
    assert rows[0]["model"] == "claude-opus-4-8"
    assert rows[0]["provider"] == "anthropic"
    assert rows[0]["total_tokens"] == 728
    assert rows[0]["events"] == 3
    assert rows[1]["model"] == "gpt-5.5"
    assert rows[1]["total_tokens"] == 350


def test_summary_by_kind(client):
    rows = client.get("/api/usage/summary").json()["by_kind"]
    by = {r["kind"]: r for r in rows}
    assert set(by) == {"chat", "exec", "tool"}
    # chat: (100+20)+(200+50)+(90+28) = 488
    assert by["chat"]["total_tokens"] == 488
    assert by["chat"]["events"] == 3
    assert by["exec"]["total_tokens"] == 490
    assert by["tool"]["total_tokens"] == 100


# ── trend ──

def test_trend_day_buckets(client):
    d = client.get("/api/usage/trend?bucket=day").json()
    assert d["bucket"] == "day"
    trend = d["trend"]
    assert len(trend) == 3  # 3 distinct days
    # ascending by ts
    assert trend[0]["ts"] < trend[1]["ts"] < trend[2]["ts"]
    # day 0: chat(120) + exec(490) = 610
    assert trend[0]["total_tokens"] == 610
    # day 1: chat(250) + tool(100) = 350
    assert trend[1]["total_tokens"] == 350
    # day 2: chat(118)
    assert trend[2]["total_tokens"] == 118


def test_trend_bucket_falls_back_to_day(client):
    # unknown bucket value -> day
    d = client.get("/api/usage/trend?bucket=bogus").json()
    assert d["bucket"] == "day"


def test_trend_hour_bucket(client):
    d = client.get("/api/usage/trend?bucket=hour").json()
    assert d["bucket"] == "hour"
    # events on 3 different days = 3 different hour buckets too (>=3 here since
    # all share the same intra-day offset, the day spacing makes 3 buckets)
    assert len(d["trend"]) == 3


# ── time filtering ──

def test_since_filter(client):
    base = client._seed_base  # type: ignore[attr-defined]
    # only events at/after day 1
    d = client.get(f"/api/usage/summary?since={base + 1 * DAY}").json()
    # day1 (chat 250 + tool 100) + day2 (chat 118) = 4 events
    assert d["totals"]["events"] == 3
    assert d["totals"]["total_tokens"] == 250 + 100 + 118


def test_until_filter(client):
    base = client._seed_base  # type: ignore[attr-defined]
    # until is exclusive — only day 0 (< base + 1 day)
    d = client.get(f"/api/usage/summary?until={base + 1 * DAY}").json()
    assert d["totals"]["events"] == 2  # chat + exec on day 0
    assert d["totals"]["total_tokens"] == 610

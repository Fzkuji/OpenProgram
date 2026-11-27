"""Token Usage never hides its total behind one unpriced call, and a subscription reads as included."""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.models.usage.event import UsageEvent
from openprogram.models.usage.ledger import UsageLedger


@pytest.fixture
def client(tmp_path, monkeypatch):
    ledger = UsageLedger(db_path=tmp_path / "usage.db")
    import openprogram.models.usage.ledger as ledger_module
    monkeypatch.setattr(ledger_module, "default_ledger", ledger)
    now = time.time()

    def event(provider: str, cost: float, source: str = "model_catalog") -> UsageEvent:
        return UsageEvent(ts=now, session_id="s", call_kind="chat", provider=provider, model_id="m",
                          input_tokens=10, output_tokens=5, cache_read_tokens=100, total_tokens=15,
                          cost_total=cost, cost_source=source)

    ledger.append_many([event("deepseek", 0.5), event("deepseek", 0.0, "unknown"),
                        event("kimi-coding", 3.0), event("zai-coding-plan", 1.0)])
    app = FastAPI()
    from openprogram.interfaces.server.routes.settings import usage
    usage.register(app)
    yield TestClient(app)
    ledger.close()


def test_summary_splits_billed_included_and_unpriced(client):
    body = client.get("/api/usage/summary").json()
    totals = body["totals"]
    assert totals["billed_cost"] == pytest.approx(0.5)
    assert totals["included_cost"] == pytest.approx(4.0)
    assert totals["unpriced_events"] == 1
    # Cached tokens count in the total even where the stored total left them out.
    assert totals["total_tokens"] == 4 * 115
    included = {row["provider"]: row["included"] for row in body["by_model"]}
    assert included == {"deepseek": False, "kimi-coding": True, "zai-coding-plan": True}


def test_trend_carries_the_billed_cost(client):
    trend = client.get("/api/usage/trend?days=2").json()["trend"]
    assert trend[-1]["billed_cost"] == pytest.approx(0.5) and trend[-1]["cost"] == pytest.approx(4.5)
    series = client.get("/api/usage/trend?days=2&group=model_id").json()["series"]
    assert series["m"][-1]["billed_cost"] == pytest.approx(0.5)


def test_source_and_function_rows_split_their_cost(client):
    body = client.get("/api/usage/summary").json()
    (kind,) = body["by_kind"]
    assert kind["kind"] == "chat"
    assert kind["billed_cost"] == pytest.approx(0.5)
    assert kind["included_cost"] == pytest.approx(4.0)
    assert kind["unpriced_events"] == 1
    (label,) = body["by_label"]
    assert label["billed_cost"] == pytest.approx(0.5) and label["included_cost"] == pytest.approx(4.0)

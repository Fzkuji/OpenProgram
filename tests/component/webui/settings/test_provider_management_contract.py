"""Every provider-management route answers the shape its TypedDict documents.

One isolated Worker state (config, key store, usage ledger, quota store);
no request leaves the process: the key check, the quota endpoint and the
local-server probe are fakes.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.models.auth.store import AuthStore, set_store_for_testing
from openprogram.models.listing import credentials as cr
from openprogram.models.listing import detected, key_check
from openprogram.models.usage import provider_status, provider_views, quota, quota_fetchers
from openprogram.models.usage.event import UsageEvent
from openprogram.models.usage.ledger import UsageLedger
from openprogram.models.usage.quota import QuotaLimit

_SECRET = "sk-or-test-0000000000000000wxyz"


def _probe(status, *, http_status=200, kind="openrouter_key", via="GET /key", detail=None):
    payload = {"status": status, "ok": status == cr.VALID, "kind": kind, "via": via, "detail": detail,
               "http_status": http_status}
    return SimpleNamespace(status=status, ok=status == cr.VALID, kind=kind, via=via, detail=detail,
                           http_status=http_status, model=None, to_dict=lambda: dict(payload))


@pytest.fixture
def api(tmp_path, monkeypatch):
    from openprogram.state import config as user_config
    from openprogram.models.auth.account import account_priority, account_selection
    from openprogram.models.auth import rotation
    import openprogram.models.usage.ledger as ledger_module

    monkeypatch.setattr(user_config, "get_config_path", lambda: tmp_path / "config.json")
    for module in (account_priority, account_selection, rotation):
        monkeypatch.setattr(module, "default_root", lambda: tmp_path / "sidecars")
    store = AuthStore(root=tmp_path / "store")
    set_store_for_testing(store)
    ledger = UsageLedger(db_path=tmp_path / "usage.db")
    monkeypatch.setattr(ledger_module, "default_ledger", ledger)
    qstore = quota.QuotaStore(ledger)
    for module in (quota, provider_status, provider_views):
        monkeypatch.setattr(module, "default_store", qstore)
    monkeypatch.setattr(quota_fetchers, "default_store", qstore, raising=False)
    quota_fetchers.reset_policy()

    sent: list[str] = []

    def fake_fetch(module, cred, **_):
        sent.append(cred.provider)
        limits = [QuotaLimit(limit_key="key_limit", label="Key limit", unit="usd", source="endpoint",
                             fetched_at=time.time(), limit=10, remaining=4)]
        qstore.observe(cred.provider, cred.account, limits, force_write=True, replace_source=True)
        return limits

    monkeypatch.setattr(quota_fetchers, "fetch_one", fake_fetch)
    monkeypatch.setattr(quota_fetchers, "_spawn", lambda work: None)
    monkeypatch.setattr(cr, "validate_credential", lambda *a, **k: _probe(cr.VALID))
    monkeypatch.setattr(cr, "_http_get", lambda *a, **k: None)  # no local server answers
    monkeypatch.setattr(key_check, "suggested_models", lambda provider, rows=None: (2, [
        {"id": "a/model-1", "name": "Model 1", "context_window": 200000, "input_cost": 1.0,
         "output_cost": 2.0, "reasoning": True, "vision": False, "tools": True, "enabled": False}]))
    ledger.append(UsageEvent(ts=time.time(), session_id="s", call_kind="chat", provider="openrouter",
                             model_id="a/model-1", input_tokens=100, output_tokens=10, cache_read_tokens=50,
                             total_tokens=110, cost_total=0.5, cost_source="model_catalog"))

    from openprogram.interfaces.server.routes.identity import accounts, providers
    from openprogram.interfaces.server.routes.settings import usage
    app = FastAPI()
    providers.register(app)
    accounts.register(app)
    usage.register(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, store=store, sent=sent)
    set_store_for_testing(None)
    ledger.close()


def _keys(typed) -> set[str]:
    return set(typed.__annotations__)


def test_every_route_answers_its_documented_shape(api, monkeypatch):
    c = api.client
    monkeypatch.setenv("OPENROUTER_API_KEY", _SECRET)

    found = c.get("/api/providers/detected")
    assert found.status_code == 200 and _SECRET not in found.text
    body = found.json()
    assert set(body) == _keys(detected.DetectedPayload)
    env = next(row for row in body["env"] if row["env_var"] == "OPENROUTER_API_KEY")
    assert set(env) == _keys(detected.DetectedEnvKey) and env["provider"] == "openrouter"
    assert all(set(row) == _keys(detected.DetectedLocal) and row["running"] is False for row in body["local"])
    assert all(set(row) == _keys(detected.PopularProvider) for row in body["popular"])

    added = c.post("/api/providers/openrouter/accounts/env", json={"env_var": "OPENROUTER_API_KEY"})
    assert added.status_code == 200, added.text
    assert _SECRET not in added.text
    check = added.json()["check"]
    assert set(check) == _keys(key_check.KeyCheck)
    assert check["result"] == "works" and check["auto_enabled"] is True
    assert check["balance"] == {"amount": 4.0, "unit": "usd", "label": "Key limit"}
    assert set(check["models"][0]) == _keys(key_check.SuggestedModel)
    assert check["enabled_models"] == ["a/model-1"]

    status = c.get("/api/providers/status").json()
    assert set(status) == {"generated_at", "stale_after_s", "providers"}
    row = next(p for p in status["providers"] if p["id"] == "openrouter")
    assert set(row) == _keys(provider_status.ProviderStatus)
    assert _SECRET not in str(status)

    refreshed = c.post("/api/providers/status/refresh", json={"providers": ["openrouter"]})
    assert refreshed.status_code == 200 and [p["id"] for p in refreshed.json()["providers"]] == ["openrouter"]
    one = c.post("/api/providers/openrouter/refresh")
    assert one.status_code == 200 and set(one.json()) == _keys(provider_status.ProviderStatus)
    assert c.post("/api/providers/nobody/refresh").status_code == 404

    usage = c.get("/api/providers/openrouter/usage?days=7&group=day,model")
    assert usage.status_code == 200
    u = usage.json()
    assert set(u) == _keys(provider_views.ProviderUsage)
    assert set(u["totals"]) == _keys(provider_views.UsageTotals)
    assert len(u["series"]) == 7 and set(u["series"][-1]) == _keys(provider_views.UsageDay)
    assert set(u["models"][0]) == _keys(provider_views.UsageModel)
    assert u["totals"]["total_tokens"] == 160
    assert c.get("/api/providers/openrouter/usage?days=0").status_code == 400
    assert c.get("/api/providers/openrouter/usage?group=week").status_code == 400

    enabled = c.get("/api/models/enabled").json()
    assert set(enabled) == {"models", "providers"}
    for state in enabled["providers"].values():
        assert set(state) == _keys(provider_views.PickerProvider)

    fallback = c.get("/api/providers/fallback?provider=openrouter&model=a/model-1&context_tokens=1000")
    assert fallback.status_code == 200 and set(fallback.json()) == _keys(provider_views.FallbackPayload)
    assert c.get("/api/providers/fallback?provider=&model=x").status_code == 400

    validated = c.post("/api/providers/openrouter/accounts/default/validate")
    assert validated.status_code == 200 and set(validated.json()["check"]) == _keys(key_check.KeyCheck)
    assert _SECRET not in validated.text

    summary = c.get("/api/usage/summary").json()
    for field in ("billed_cost", "included_cost", "unpriced_events"):
        assert field in summary["totals"]


def test_a_key_the_provider_did_not_accept_does_not_turn_the_provider_on(api, monkeypatch):
    # A 5xx on the auth endpoint says nothing about the key: it is kept, not trusted.
    monkeypatch.setattr(cr, "validate_credential", lambda *a, **k: _probe(cr.UNKNOWN, http_status=503))
    r = api.client.post("/api/providers/openrouter/accounts/keys", json={"api_key": _SECRET, "validate": True})
    assert r.status_code == 200 and r.json()["check"]["result"] == "unverified"
    assert r.json()["check"]["auto_enabled"] is False and r.json()["check"]["models"] == []


def test_a_rejected_key_is_answered_and_not_stored(api, monkeypatch):
    monkeypatch.setattr(cr, "validate_credential", lambda *a, **k: _probe(cr.INVALID_CREDENTIAL, http_status=401))
    r = api.client.post("/api/providers/openrouter/accounts/keys", json={"api_key": _SECRET, "validate": True})
    assert r.status_code == 400 and set(r.json()["check"]) == set(key_check.KeyCheck.__annotations__)
    assert api.store.find_pool("openrouter", "default") is None
    assert _SECRET not in r.text

"""GET /api/providers/status and its Refresh, read from a temp ledger and fake providers."""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.models.usage import provider_status, quota, quota_fetchers
from openprogram.models.usage.event import UsageEvent
from openprogram.models.usage.ledger import UsageLedger
from openprogram.models.usage.quota import QuotaLimit

ROWS = [{"id": "openrouter", "label": "OpenRouter", "enabled": True},
        {"id": "openai-codex", "label": "OpenAI Codex", "enabled": True}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    ledger = UsageLedger(db_path=tmp_path / "usage.db")
    import openprogram.models.usage.ledger as ledger_module
    monkeypatch.setattr(ledger_module, "default_ledger", ledger)
    store = quota.QuotaStore(ledger)
    monkeypatch.setattr(quota, "default_store", store)
    monkeypatch.setattr(provider_status, "default_store", store)
    monkeypatch.setattr(provider_status, "connected_providers", lambda: list(ROWS))
    monkeypatch.setattr(provider_status, "keys_of", lambda provider: [])
    monkeypatch.setattr(provider_status, "_checked", {})
    reads: list[tuple] = []

    def fake_refresh(providers=None, *, reason="open", **_):
        reads.append((tuple(providers or ()), reason))
        store.observe("openrouter", "", [QuotaLimit(
            limit_key="key_limit", label="Key limit", unit="usd", source="endpoint",
            fetched_at=time.time(), limit=10, remaining=7)], force_write=True)
        return {"openrouter": 1}

    monkeypatch.setattr(quota_fetchers, "refresh", fake_refresh)
    ledger.append(UsageEvent(ts=time.time(), session_id="s", call_kind="chat", provider="openai-codex",
                             model_id="gpt-6.1-sol", input_tokens=100, output_tokens=10, cache_read_tokens=900,
                             total_tokens=1010, cost_total=2.0, cost_source="model_catalog"))
    app = FastAPI()
    from openprogram.interfaces.server.routes.identity import providers as routes
    routes.register(app)
    c = TestClient(app)
    c.reads = reads  # type: ignore[attr-defined]
    yield c
    ledger.close()


def test_status_lists_every_connected_provider(client):
    payload = client.get("/api/providers/status").json()
    by_id = {p["id"]: p for p in payload["providers"]}
    codex = by_id["openai-codex"]
    assert codex["billing"] == "subscription"
    assert codex["spend"]["included"] is True and codex["spend"]["cost_usd"] is None
    assert codex["spend"]["cache_share"] == pytest.approx(0.9)
    assert codex["default_model"] == "gpt-6.1-sol"
    assert by_id["openrouter"]["quota"]["kind"] == "none"
    assert set(codex) == set(provider_status.ProviderStatus.__annotations__)
    # A page opening reads endpoints older than ten minutes, after answering.
    deadline = time.time() + 2
    while not client.reads and time.time() < deadline:
        time.sleep(0.01)
    assert client.reads == [(("openrouter", "openai-codex"), "open")]


def test_refresh_reads_now_and_answers_with_the_new_values(client):
    payload = client.post("/api/providers/status/refresh", json={"providers": ["openrouter"]}).json()
    assert [p["id"] for p in payload["providers"]] == ["openrouter"]
    assert payload["providers"][0]["quota"]["limits"][0]["remaining"] == 7
    assert client.reads == [(("openrouter",), "manual")]


def test_refresh_refuses_a_bad_body(client):
    assert client.post("/api/providers/status/refresh", json={"providers": "openrouter"}).status_code == 400
    assert client.post("/api/providers/status/refresh", json={"other": 1}).status_code == 400


def test_one_provider_refreshes_on_its_own(client):
    status = client.post("/api/providers/openrouter/refresh").json()
    assert status["id"] == "openrouter" and status["quota"]["limits"][0]["remaining"] == 7
    assert status["fix"] is None and status["console_url"] == "https://openrouter.ai/settings/keys"
    assert client.reads == [(("openrouter",), "manual")]
    assert client.post("/api/providers/mistral/refresh").status_code == 404


def test_usage_of_one_provider(client):
    usage = client.get("/api/providers/openai-codex/usage?days=7&group=day,model").json()
    assert usage["totals"]["included"] is True and usage["totals"]["cost_usd"] is None
    assert usage["totals"]["list_price_usd"] == 2.0 and len(usage["series"]) == 7
    assert usage["series"][-1]["models"]["gpt-6.1-sol"]["tokens"] == 1010
    assert usage["models"][0]["model_id"] == "gpt-6.1-sol"
    assert client.get("/api/providers/openai-codex/usage?group=week").status_code == 400
    assert client.get("/api/providers/openai-codex/usage?days=365").status_code == 400


def test_detected_and_fallback_routes(client, monkeypatch):
    from openprogram.models.listing import detected
    from openprogram.models.usage import provider_views
    monkeypatch.setattr(detected, "detected", lambda: {"env": [], "local": [], "popular": [], "catalogue_count": 0})
    assert client.get("/api/providers/detected").json()["catalogue_count"] == 0
    monkeypatch.setattr(provider_views, "fallback_for",
                        lambda provider, model, context_tokens=0: {"suggestion": None, "alternatives": [],
                                                                    "asked": [provider, model, context_tokens]})
    got = client.get("/api/providers/fallback?provider=openai-codex&model=gpt&context_tokens=900").json()
    assert got["asked"] == ["openai-codex", "gpt", 900]
    assert client.get("/api/providers/fallback?provider=&model=gpt").status_code == 400


def test_the_model_picker_list_carries_each_providers_state(client, monkeypatch):
    from openprogram.models import listing
    from openprogram.models.usage import provider_views
    monkeypatch.setattr(listing, "list_enabled_models", lambda: [{"provider": "deepseek", "id": "flash"}])
    monkeypatch.setattr(provider_views, "picker_states", lambda ids: {pid: {"id": pid, "state": "not_configured"}
                                                                       for pid in ids})
    body = client.get("/api/models/enabled").json()
    assert body["models"] == [{"provider": "deepseek", "id": "flash"}]
    assert body["providers"]["deepseek"]["state"] == "not_configured"


def _hooks(monkeypatch, provider: str, **hooks):
    """Give one provider login hooks, as its auth adapter registers them."""
    from openprogram.models.auth import credential_provider
    real = credential_provider.get_provider_config

    def config(provider_id: str):
        if provider_id == provider:
            return credential_provider.ProviderAuthConfig(provider_id=provider_id, **hooks)
        return credential_provider.ProviderAuthConfig(provider_id=provider_id)

    monkeypatch.setattr(credential_provider, "get_provider_config", config)
    return real


def test_refresh_reads_the_login_again_before_the_quota_and_says_when(client, monkeypatch):
    order: list[str] = []
    _hooks(monkeypatch, "openai-codex", reread_login=lambda: order.append("login"))
    before = client.get("/api/providers/status").json()
    assert {p["id"]: p["checked_at"] for p in before["providers"]}["openai-codex"] is None

    started = time.time()
    status = client.post("/api/providers/openai-codex/refresh").json()

    assert order == ["login"]
    # The page's own background read ("open") may land at any time; Refresh is the manual one.
    assert [read for read in client.reads if read[1] == "manual"] == [(("openai-codex",), "manual")]
    assert status["checked_at"] >= started
    # Only the provider that was asked is stamped.
    after = {p["id"]: p for p in client.get("/api/providers/status").json()["providers"]}
    assert after["openrouter"]["checked_at"] is None


def test_a_login_that_cannot_be_reread_does_not_fail_refresh(client, monkeypatch):
    def broken():
        raise RuntimeError("store unreadable")

    _hooks(monkeypatch, "openai-codex", reread_login=broken)
    response = client.post("/api/providers/openai-codex/refresh")
    assert response.status_code == 200 and response.json()["id"] == "openai-codex"


def test_a_subscription_names_its_plan_and_an_api_key_provider_never_does(client, monkeypatch):
    _hooks(monkeypatch, "openai-codex", plan=lambda: "Plus")
    by_id = {p["id"]: p for p in client.get("/api/providers/status").json()["providers"]}
    assert by_id["openai-codex"]["plan"] == "plus"
    assert by_id["openrouter"]["plan"] is None


def test_the_plan_a_reply_named_wins_over_the_sign_in(client, monkeypatch):
    _hooks(monkeypatch, "openai-codex", plan=lambda: "plus")
    quota.default_store.note_plan("openai-codex", "", "pro")
    by_id = {p["id"]: p for p in client.get("/api/providers/status").json()["providers"]}
    assert by_id["openai-codex"]["plan"] == "pro"


def test_an_unknown_plan_is_null(client):
    by_id = {p["id"]: p for p in client.get("/api/providers/status").json()["providers"]}
    assert by_id["openai-codex"]["plan"] is None

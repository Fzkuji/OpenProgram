"""The status of each connected provider: health, quota, 30-day spend and keys."""
from __future__ import annotations

import pytest

from openprogram.models.usage import provider_status, quota
from openprogram.models.usage.event import UsageEvent
from openprogram.models.usage.ledger import UsageLedger
from openprogram.models.usage.quota import QuotaLimit, QuotaReport

DAY = 86400
NOW = 1_800_000_000.0


def _key(status: str = "valid", *, active: bool = True, cooldown: float | None = None,
         cred: str = "cred-1") -> dict:
    return {"account": "default", "credential_id": cred, "kind": "api_key", "status": status,
            "active": active, "cooldown_until": cooldown, "last_error": "HTTP 429" if cooldown else None,
            "last_used_at": NOW - 60, "use_count": 3}


@pytest.mark.parametrize("status, state", [
    ("valid", "ready"), ("expiring_soon", "ready"),
    ("needs_reauth", "signed_out"), ("stale", "signed_out"), ("billing_blocked", "no_credit"),
    ("revoked", "invalid_key"), ("unknown", "unknown"),
])
def test_health_maps_the_credential_status(status, state):
    assert provider_status.health_of([_key(status)], [], NOW)["state"] == state


def test_a_cooling_key_reads_rate_limited_until_its_cooldown_ends():
    health = provider_status.health_of([_key("valid", cooldown=NOW + 120)], [], NOW)
    assert health == {"state": "rate_limited", "until": NOW + 120, "detail": "HTTP 429"}


def test_a_throttled_key_reads_ready_once_its_cooldown_has_passed():
    cooling = provider_status.health_of([_key("rate_limited", cooldown=NOW + 120)], [], NOW)
    assert cooling["state"] == "rate_limited" and cooling["until"] == NOW + 120
    expired = provider_status.health_of([_key("rate_limited", cooldown=NOW - 37 * 3600)], [], NOW)
    assert expired == {"state": "ready", "until": None, "detail": None}
    assert provider_status.health_of([_key("rate_limited")], [], NOW)["state"] == "ready"


def _read(status: int | None, at: float, error: str | None = None) -> dict:
    return {"provider": "deepseek", "account": "cred-1", "read_at": at, "status": status, "error": error}


def test_the_newest_evidence_about_a_key_decides_its_health():
    blocked = {**_key("billing_blocked"), "last_error": "billing_blocked: HTTP 402", "status_at": NOW - 600}
    refused = _read(401, NOW - 30, "HTTP 401")
    # A 401 read after the 402 stop: the key is invalid, not out of credit.
    assert provider_status.health_of([blocked], [], NOW, {"cred-1": refused}) == {
        "state": "invalid_key", "until": None, "detail": "quota endpoint answered 401"}
    # At the same moment the 401 still outranks the stop; a 402 written later wins.
    assert provider_status.health_of([{**blocked, "status_at": NOW - 30}], [], NOW,
                                     {"cred-1": refused})["state"] == "invalid_key"
    assert provider_status.health_of([{**blocked, "status_at": NOW - 10}], [], NOW,
                                     {"cred-1": refused})["state"] == "no_credit"
    # A read that answered after the stop overrules it; its balance then decides.
    answered = {"cred-1": _read(200, NOW - 30)}
    assert provider_status.health_of([blocked], [], NOW, answered)["state"] == "ready"
    empty = QuotaLimit(limit_key="balance", label="Balance", unit="usd", source="endpoint",
                       fetched_at=NOW - 30, limit=None, remaining=0)
    report = QuotaReport("deepseek", "cred-1", [empty])
    assert provider_status.health_of([blocked], [report], NOW, answered)["state"] == "no_credit"
    # A failed read that is not a 401 changes nothing; nor does a throttle outrank a later 401.
    failed = {"cred-1": _read(500, NOW - 30, "HTTP 500")}
    assert provider_status.health_of([blocked], [], NOW, failed)["state"] == "no_credit"
    cooling = {**_key("rate_limited", cooldown=NOW + 120), "status_at": NOW - 600}
    assert provider_status.health_of([cooling], [], NOW, {"cred-1": refused})["state"] == "invalid_key"


def test_the_best_key_wins():
    keys = [_key("revoked", cred="a", active=True), _key("valid", cred="b", active=False)]
    assert provider_status.health_of(keys, [], NOW)["state"] == "ready"
    assert provider_status.health_of([], [], NOW)["state"] == "unknown"


def test_a_spent_quota_overrides_ready():
    spent = QuotaLimit(limit_key="primary", label="5h", unit="percent", source="headers", fetched_at=NOW,
                       used_fraction=1.0, window_minutes=300, resets_at=NOW + 600)
    health = provider_status.health_of([_key()], [QuotaReport("openai-codex", "cred-1", [spent])], NOW)
    assert health == {"state": "rate_limited", "until": NOW + 600, "detail": "5h"}
    empty = QuotaLimit(limit_key="key_limit", label="Key limit", unit="usd", source="endpoint",
                       fetched_at=NOW, limit=5, remaining=0)
    health = provider_status.health_of([_key()], [QuotaReport("openrouter", "cred-1", [empty])], NOW)
    assert health["state"] == "no_credit"


def _rows(ledger: UsageLedger, provider: str, *events) -> list[dict]:
    for offset, inp, out, cached, cost, source in events:
        ledger.append(UsageEvent(ts=NOW - offset * DAY, session_id="s", call_kind="chat", provider=provider,
                                 model_id="m", input_tokens=inp, output_tokens=out, cache_read_tokens=cached,
                                 total_tokens=inp + out, cost_total=cost, cost_source=source))
    return [row for row in ledger.provider_days(since=NOW - 30 * DAY) if row["provider"] == provider]


def test_spend_counts_cached_tokens_and_the_cache_share(tmp_path):
    ledger = UsageLedger(db_path=tmp_path / "usage.db")
    # DeepSeek-shaped rows written before the recorder fix: total = input + output only.
    rows = _rows(ledger, "deepseek", (1, 700, 1000, 73000, 0.01, "model_catalog"),
                 (2, 300, 500, 27000, 0.02, "unknown"), (40, 1, 1, 1, 9.0, "model_catalog"))
    spend = provider_status.spend_of("deepseek", rows, NOW)
    assert spend["total_tokens"] == 700 + 1000 + 73000 + 300 + 500 + 27000
    assert spend["cache_read_tokens"] <= spend["total_tokens"]
    assert spend["cache_share"] == pytest.approx(100000 / 101000, abs=1e-4)
    assert spend["cost_usd"] == pytest.approx(0.03) and spend["unpriced_calls"] == 1
    assert len(spend["series"]) == 30 and sum(day["calls"] for day in spend["series"]) == 2
    ledger.close()


def test_a_subscription_reads_as_included(tmp_path):
    ledger = UsageLedger(db_path=tmp_path / "usage.db")
    rows = _rows(ledger, "openai-codex", (0, 1000, 100, 9000, 1.25, "model_catalog"))
    spend = provider_status.spend_of("openai-codex", rows, NOW)
    assert spend["included"] is True and spend["cost_usd"] is None
    assert spend["list_price_usd"] == pytest.approx(1.25)
    ledger.close()


def test_build_status_puts_it_together(tmp_path, monkeypatch):
    ledger = UsageLedger(db_path=tmp_path / "usage.db")
    _rows(ledger, "openrouter", (0, 100, 10, 0, 0.5, "model_catalog"))
    store = quota.QuotaStore(ledger)
    monkeypatch.setattr(provider_status, "default_store", store)
    store.observe("openrouter", "cred-1", [QuotaLimit(
        limit_key="key_limit", label="Key limit", unit="usd", source="endpoint", fetched_at=NOW,
        limit=20, remaining=18.42)], force_write=True)
    store.observe("openrouter", "removed-key", [QuotaLimit(
        limit_key="key_limit", label="Key limit", unit="usd", source="endpoint", fetched_at=NOW,
        limit=1, remaining=0)], force_write=True)
    monkeypatch.setattr(provider_status, "keys_of", lambda provider: [_key()])
    payload = provider_status.build_status(
        providers=[{"id": "openrouter", "label": "OpenRouter", "enabled": True},
                   {"id": "mistral", "label": "Mistral", "enabled": True}],
        ledger=ledger, now=NOW,
    )
    openrouter, mistral = payload["providers"]
    assert openrouter["billing"] == "api_key" and openrouter["health"]["state"] == "ready"
    assert openrouter["quota"]["kind"] == "balance" and openrouter["quota"]["account"] == "cred-1"
    assert openrouter["quota"]["limits"][0]["remaining"] == 18.42
    assert openrouter["spend"]["cost_usd"] == pytest.approx(0.5)
    assert openrouter["default_model"] == "m"
    assert mistral["quota"] == {"kind": "none", "account": None, "fetched_at": None, "stale": False, "limits": [],
                                "read_error": None, "read_at": None}
    assert payload["stale_after_s"] == quota.STALE_AFTER_S
    ledger.close()


def test_recorded_totals_include_cached_tokens():
    from types import SimpleNamespace

    from openprogram.models.usage.recorder import build_message_event

    model = SimpleNamespace(provider="deepseek", api="openai-completions", id="deepseek-flash", cost=None)
    usage = SimpleNamespace(input=707, output=1095, cache_read=73472, cache_write=0, total_tokens=75274,
                            tokens_reported=True, provider_cost_usd=None, first_token_ms=None, first_text_ms=None)
    event = build_message_event(model, SimpleNamespace(usage=usage))
    assert event.total_tokens == 707 + 1095 + 73472
    assert event.cache_read_tokens <= event.total_tokens

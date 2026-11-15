"""A key's check reads as one line, and the first usable key turns its provider on."""
from __future__ import annotations

import pytest

from openprogram.models.listing import credentials as cr
from openprogram.models.listing import key_check


def _probe(status, *, http=None, via="GET /models", kind="openai_bearer", model=None, detail=None):
    return cr.CredentialResult(provider_id="deepseek", status=status, ok=status == cr.VALID, kind=kind,
                               via=via, http_status=http, model=model, detail=detail)


@pytest.mark.parametrize("probe, result", [
    (_probe(cr.VALID, http=200, via="POST /chat/completions"), "works"),
    (_probe(cr.VALID, http=200, kind="openrouter_key", via="GET /key"), "works"),
    (_probe(cr.VALID_MODEL_UNAVAILABLE, http=503), "works"),
    (_probe(cr.VALID, http=200), "unverified"),
    (_probe(cr.INVALID_CREDENTIAL, http=401), "rejected"),
    (_probe(cr.VALID_NO_BALANCE, http=402), "no_credit"),
    (_probe(cr.NOT_AVAILABLE, http=403), "not_available"),
    (_probe(cr.UNKNOWN, detail="Couldn't reach deepseek"), "unreachable"),
    (_probe(cr.UNKNOWN, http=200, detail="Key accepted, but no model"), "unverified"),
])
def test_each_probe_reads_as_one_result(probe, result):
    assert key_check.result_of(probe) == result


def test_a_rejected_key_points_at_the_keys_page_and_no_credit_at_billing():
    rejected = key_check.check_of("deepseek", _probe(cr.INVALID_CREDENTIAL, http=401))
    assert (rejected["fix_page"], rejected["fix_url"]) == ("keys", "https://platform.deepseek.com/api_keys")
    broke = key_check.check_of("deepseek", _probe(cr.VALID_NO_BALANCE, http=402))
    assert (broke["fix_page"], broke["fix_url"]) == ("billing", "https://platform.deepseek.com/top_up")


def test_a_403_naming_the_region_is_not_a_bad_key():
    res = cr._interpret("openai", "openai_bearer",
                        (403, '{"error": {"code": "unsupported_country_region_territory"}}', 12), via="GET /models")
    assert res.status == cr.NOT_AVAILABLE and res.http_status == 403
    plain = cr._interpret("openai", "openai_bearer", (403, '{"error": "forbidden"}', 12), via="GET /models")
    assert plain.status == cr.INVALID_CREDENTIAL


@pytest.fixture
def config(monkeypatch):
    cfg: dict = {}
    from openprogram.models.listing import toggle
    from openprogram.models.providers.settings import storage
    monkeypatch.setattr(storage, "_read_providers_cfg", lambda: cfg)
    monkeypatch.setattr(toggle, "toggle_provider",
                        lambda pid, on: cfg.setdefault(pid, {}).update(enabled=on))
    monkeypatch.setattr(toggle, "toggle_model",
                        lambda pid, mid, on: cfg.setdefault(pid, {}).setdefault("models", []).append({"id": mid}))
    monkeypatch.setattr(key_check, "read_new_balance",
                        lambda pid, account: {"amount": 18.42, "unit": "usd", "label": "Balance"})
    monkeypatch.setattr(key_check, "suggested_models", lambda pid, rows=None: (6, [
        {"id": f"m{i}", "name": f"M{i}", "context_window": 128000, "input_cost": 0.1, "output_cost": 0.2,
         "reasoning": False, "vision": False, "tools": True, "enabled": False} for i in range(3)]))
    return cfg


def test_the_first_usable_key_turns_the_provider_and_its_suggested_models_on(config):
    check = key_check.connect("deepseek", key_check.check_of("deepseek", _probe(cr.VALID, http=200)),
                              account="cred-1", first_credential=True)
    assert check["auto_enabled"] is True and check["provider_enabled"] is True
    assert check["balance"]["amount"] == 18.42 and check["model_count"] == 6
    assert check["enabled_models"] == ["m0", "m1", "m2"]
    assert config["deepseek"]["enabled"] is True


def test_a_second_key_or_an_unusable_one_changes_nothing(config):
    later = key_check.connect("deepseek", key_check.check_of("deepseek", _probe(cr.VALID, http=200)),
                              account="cred-2", first_credential=False)
    assert later["auto_enabled"] is False
    assert not config.get("deepseek", {}).get("enabled")
    region = key_check.connect("deepseek", key_check.check_of("deepseek", _probe(cr.NOT_AVAILABLE, http=403)),
                               account="cred-1", first_credential=True)
    assert region["auto_enabled"] is False and region["models"] == []


def test_suggestions_put_enabled_models_first_then_the_newest(monkeypatch):
    from openprogram.models.providers.catalog.sources import models_dev
    meta = {"old": {"release_date": "2024-01-01", "input_cost": 1.0},
            "new": {"release_date": "2026-09-01", "input_cost": 2.0},
            "embed": {"release_date": "2026-10-01", "output_modalities": ["embedding"]}}
    monkeypatch.setattr(models_dev, "lookup", lambda pid, mid: meta.get(mid))
    rows = [{"id": "old"}, {"id": "new"}, {"id": "embed"}, {"id": "mine", "enabled": True}]
    count, top = key_check.suggested_models("x", rows)
    assert count == 3 and [m["id"] for m in top] == ["mine", "new", "old"]
    assert top[1]["input_cost"] == 2.0

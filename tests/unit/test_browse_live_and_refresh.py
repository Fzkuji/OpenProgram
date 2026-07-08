"""Task 4: browse goes live, read paths switch.

  * list_models_for_provider = live merge of official-API list ⊕ models.dev,
    NEVER persisted; enabled flag from config spec rows;
  * no-key / offline degradation falls back to models.dev then empty, never
    raising;
  * a short-TTL browse cache; force_refresh bypasses it;
  * list_enabled_models reads MODEL_REGISTRY directly (config spec rows), no
    network;
  * the Fetch button (fetch_models_remote) = force-refresh browse + overwrite
    the spec rows of enabled models present in the fresh result, no file
    persistence, then reload the registry.

No network, no real config: fetch_and_normalize, models.dev, _is_configured
and config IO are all stubbed at the seam each module binds.
"""
from __future__ import annotations

import copy

import pytest

from openprogram.webui._model_catalog import listing
from openprogram.webui._model_catalog import fetchers as F
from openprogram.webui._model_catalog import storage as st
from openprogram.webui._model_catalog import provider_models as pm
from openprogram.webui._model_catalog import providers as cat


@pytest.fixture(autouse=True)
def _clear_browse_cache():
    listing._reset_browse_cache()
    yield
    listing._reset_browse_cache()


@pytest.fixture
def mem_cfg(monkeypatch):
    store: dict = {}
    _read = lambda: copy.deepcopy(store)
    _write = lambda cfg: store.clear() or store.update(copy.deepcopy(cfg))
    monkeypatch.setattr(st, "_read_providers_cfg", _read)
    monkeypatch.setattr(st, "_write_providers_cfg", _write)
    st._reset_spec_migration()
    return store


# ---------------------------------------------------------------------------
# Browse: no key → models.dev; enabled flag from config spec rows
# ---------------------------------------------------------------------------

def test_no_key_browse_returns_models_dev(monkeypatch, mem_cfg):
    monkeypatch.setattr(cat, "_is_configured", lambda pid: False)
    monkeypatch.setattr(pm, "_models_dev_for", lambda pid: {
        "md-1": {"name": "MD One", "context_window": 128000},
        "md-2": {"name": "MD Two"},
    })
    # If browse ever hit the official API despite no key, this would blow up.
    def _boom(*a, **k):
        raise AssertionError("official API must not be called without a key")
    monkeypatch.setattr(F, "fetch_and_normalize", _boom)

    rows = listing.list_models_for_provider("acme")
    ids = {r["id"] for r in rows}
    assert ids == {"md-1", "md-2"}
    assert all(r["enabled"] is False for r in rows)


def test_enabled_flag_from_config_spec_rows(monkeypatch, mem_cfg):
    monkeypatch.setattr(cat, "_is_configured", lambda pid: False)
    monkeypatch.setattr(pm, "_models_dev_for", lambda pid: {
        "md-1": {"name": "MD One"}, "md-2": {"name": "MD Two"},
    })
    mem_cfg["acme"] = {"models": [{"id": "md-2", "name": "MD Two"}]}
    rows = {r["id"]: r for r in listing.list_models_for_provider("acme")}
    assert rows["md-2"]["enabled"] is True
    assert rows["md-1"]["enabled"] is False


def test_official_api_wins_and_models_dev_enriches(monkeypatch, mem_cfg):
    monkeypatch.setattr(cat, "_is_configured", lambda pid: True)
    monkeypatch.setattr(pm, "_models_dev_for", lambda pid: {
        "live-1": {"context_window": 200000, "input_cost": 3.0},
    })
    monkeypatch.setattr(F, "fetch_and_normalize", lambda pid, timeout=15.0: {
        "models": [{"id": "live-1", "name": "Live One"}],
    })
    rows = {r["id"]: r for r in listing.list_models_for_provider("acme")}
    assert set(rows) == {"live-1"}
    assert rows["live-1"]["name"] == "Live One"          # official wins
    assert rows["live-1"]["context_window"] == 200000    # models.dev fills


# ---------------------------------------------------------------------------
# Degradation: official API error → models.dev; both gone → manual/empty
# ---------------------------------------------------------------------------

def test_official_error_falls_back_to_models_dev(monkeypatch, mem_cfg):
    monkeypatch.setattr(cat, "_is_configured", lambda pid: True)
    monkeypatch.setattr(pm, "_models_dev_for", lambda pid: {"md-x": {"name": "X"}})
    monkeypatch.setattr(F, "fetch_and_normalize", lambda pid, timeout=15.0: {
        "error": "401 unauthorized",
    })
    rows = listing.list_models_for_provider("acme")
    assert {r["id"] for r in rows} == {"md-x"}


def test_fully_offline_returns_manual_rows_no_raise(monkeypatch, mem_cfg):
    monkeypatch.setattr(cat, "_is_configured", lambda pid: True)
    monkeypatch.setattr(pm, "_models_dev_for", lambda pid: {})  # models.dev down
    monkeypatch.setattr(F, "fetch_and_normalize",
                        lambda pid, timeout=15.0: {"error": "network down"})
    mem_cfg["acme"] = {"models": [
        {"id": "hand-1", "name": "Hand One", "source": "manual"},
    ]}
    rows = listing.list_models_for_provider("acme")  # must not raise
    assert {r["id"] for r in rows} == {"hand-1"}
    assert rows[0]["enabled"] is True  # it's a stored spec row


# ---------------------------------------------------------------------------
# Cache: repeated browse hits the API once; force_refresh re-hits
# ---------------------------------------------------------------------------

def test_browse_cache_ttl_and_force_refresh(monkeypatch, mem_cfg):
    monkeypatch.setattr(cat, "_is_configured", lambda pid: True)
    monkeypatch.setattr(pm, "_models_dev_for", lambda pid: {})
    calls = {"n": 0}

    def _fetch(pid, timeout=15.0):
        calls["n"] += 1
        return {"models": [{"id": "m", "name": "M"}]}
    monkeypatch.setattr(F, "fetch_and_normalize", _fetch)

    listing.list_models_for_provider("acme")
    listing.list_models_for_provider("acme")
    assert calls["n"] == 1  # second read served from cache

    listing.list_models_for_provider("acme", force_refresh=True)
    assert calls["n"] == 2  # force bypasses cache


# ---------------------------------------------------------------------------
# list_enabled_models reads the registry directly (no network)
# ---------------------------------------------------------------------------

def test_enabled_models_reads_registry(monkeypatch):
    import openprogram.providers.models_generated as mg
    from openprogram.providers.types import Model
    monkeypatch.setattr(cat, "_label", lambda pid: pid.upper())
    monkeypatch.setattr(mg, "MODEL_REGISTRY", {
        "openai/gpt-x": Model.model_validate({
            "id": "gpt-x", "name": "GPT-X", "provider": "openai",
            "api": "openai-responses", "base_url": "https://api.openai.com/v1",
        }),
    })
    monkeypatch.setattr(st, "_read_providers_cfg",
                        lambda: {"openai": {"enabled": True}})
    # If it delegated to the live browse path this would explode.
    monkeypatch.setattr(listing, "_browse_models",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no browse")))
    out = listing.list_enabled_models()
    assert [(m["provider"], m["id"]) for m in out] == [("openai", "gpt-x")]
    assert out[0]["provider_label"] == "OPENAI"
    assert out[0]["enabled"] is True


# ---------------------------------------------------------------------------
# Fetch button = Refresh: overwrite enabled specs, no persistence, reload reg
# ---------------------------------------------------------------------------

def test_refresh_overwrites_enabled_specs(monkeypatch, mem_cfg):
    monkeypatch.setattr(cat, "_is_configured", lambda pid: True)
    monkeypatch.setattr(pm, "_models_dev_for", lambda pid: {})
    # Enabled model has a STALE stored spec (old context window).
    mem_cfg["acme"] = {
        "enabled": True,
        "models": [{"id": "keep", "name": "Keep", "context_window": 1}],
    }
    # Fresh official-API result carries the corrected context window, plus a
    # new model the user hasn't enabled.
    monkeypatch.setattr(F, "fetch_and_normalize", lambda pid, timeout=15.0: {
        "models": [
            {"id": "keep", "name": "Keep", "context_window": 999999},
            {"id": "unenabled", "name": "New"},
        ],
    })
    reloads = {"n": 0}
    import openprogram.providers.models_generated as mg
    monkeypatch.setattr(mg, "reload", lambda: reloads.__setitem__("n", reloads["n"] + 1))

    res = F.fetch_models_remote("acme")
    assert res["refreshed"] == ["keep"]
    # Stale spec healed in place.
    rows = {r["id"]: r for r in mem_cfg["acme"]["models"]}
    assert rows["keep"]["context_window"] == 999999
    # Un-enabled model did NOT get a spec row (Refresh only touches enabled).
    assert "unenabled" not in rows
    # Registry rebuilt so the runtime sees the healed spec.
    assert reloads["n"] == 1


def test_refresh_keeps_enabled_model_absent_upstream(monkeypatch, mem_cfg):
    monkeypatch.setattr(cat, "_is_configured", lambda pid: True)
    monkeypatch.setattr(pm, "_models_dev_for", lambda pid: {})
    mem_cfg["acme"] = {
        "enabled": True,
        "models": [{"id": "ghost", "name": "Ghost", "context_window": 5}],
    }
    # Upstream no longer lists "ghost".
    monkeypatch.setattr(F, "fetch_and_normalize", lambda pid, timeout=15.0: {
        "models": [{"id": "other", "name": "Other"}],
    })
    import openprogram.providers.models_generated as mg
    monkeypatch.setattr(mg, "reload", lambda: None)

    res = F.fetch_models_remote("acme")
    assert res["refreshed"] == []
    # Stored spec for the absent enabled model is preserved untouched.
    rows = {r["id"]: r for r in mem_cfg["acme"]["models"]}
    assert rows["ghost"]["context_window"] == 5

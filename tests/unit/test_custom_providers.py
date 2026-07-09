"""Custom (user-added) providers — tier-3 listing + create/delete + manual add.

Covers the "Add custom provider" feature:

  * create validates the id (bad slug, collision with an alias / existing id);
  * a created custom provider surfaces as a tier-3 sidebar row (``custom: true``);
  * delete refuses a non-custom provider and removes a custom one;
  * a manually-added model produces a working ENABLED_MODELS entry after reload;
  * toggle works for a dir-less provider (spec built from the browse row +
    provider config base_url).

No network, no real config: config IO + models.dev + _is_configured are stubbed
at the seam each module binds (same pattern as test_browse_live_and_refresh).
"""
from __future__ import annotations

import copy

import pytest

from openprogram.webui._model_listing import listing
from openprogram.webui._model_listing import fetchers as F
from openprogram.webui._model_listing import storage as st
from openprogram.webui._model_listing import provider_models as pm
from openprogram.webui._model_listing import providers as cat


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
    # ``toggle`` binds these two names at import time (``from .storage import
    # ...``), so patching the storage module alone doesn't reach them — patch
    # the toggle module's copies too so toggle_model writes to the store, not
    # the real config.
    from openprogram.webui._model_listing import toggle as tg
    monkeypatch.setattr(tg, "_read_providers_cfg", _read)
    monkeypatch.setattr(tg, "_write_providers_cfg", _write)
    st._reset_spec_migration()
    return store


# ---------------------------------------------------------------------------
# create_custom_provider — validation
# ---------------------------------------------------------------------------

def test_create_rejects_bad_slug(mem_cfg):
    res = st.create_custom_provider("Bad_Slug", "Bad", "https://x.test/v1")
    assert res["ok"] is False and "slug" in res["error"].lower()
    assert "Bad_Slug" not in mem_cfg


def test_create_rejects_empty_base_url(mem_cfg):
    res = st.create_custom_provider("acme", "Acme", "")
    assert res["ok"] is False and "base_url" in res["error"].lower()


def test_create_rejects_alias_collision(mem_cfg, monkeypatch):
    # "codex" resolves to openai-codex — a reserved alias.
    monkeypatch.setattr("openprogram.providers.get_providers", lambda: [])
    from openprogram.webui._model_listing import sources as S
    monkeypatch.setattr(S.models_dev, "list_providers", lambda: [])
    res = st.create_custom_provider("codex", "Codex", "https://x.test/v1")
    assert res["ok"] is False and "alias" in res["error"].lower()
    assert "codex" not in mem_cfg


def test_create_rejects_existing_provider_id(mem_cfg, monkeypatch):
    monkeypatch.setattr("openprogram.providers.get_providers", lambda: ["openai"])
    from openprogram.webui._model_listing import sources as S
    monkeypatch.setattr(S.models_dev, "list_providers", lambda: [])
    res = st.create_custom_provider("openai", "OpenAI", "https://x.test/v1")
    assert res["ok"] is False and "exists" in res["error"].lower()


def test_create_writes_marker_config(mem_cfg, monkeypatch):
    monkeypatch.setattr("openprogram.providers.get_providers", lambda: [])
    from openprogram.webui._model_listing import sources as S
    monkeypatch.setattr(S.models_dev, "list_providers", lambda: [])
    res = st.create_custom_provider(
        "frontier-intelligence", "Frontier", "https://api.frontier.tech/v1"
    )
    assert res["ok"] is True
    pcfg = mem_cfg["frontier-intelligence"]
    assert pcfg["source"] == "custom"
    assert pcfg["enabled"] is True
    assert pcfg["label"] == "Frontier"
    assert pcfg["base_url"] == "https://api.frontier.tech/v1"
    assert pcfg["models"] == []


def test_create_label_falls_back_to_title_case(mem_cfg, monkeypatch):
    monkeypatch.setattr("openprogram.providers.get_providers", lambda: [])
    from openprogram.webui._model_listing import sources as S
    monkeypatch.setattr(S.models_dev, "list_providers", lambda: [])
    res = st.create_custom_provider("frontier-intelligence", "", "https://x.test/v1")
    assert res["ok"] is True
    assert mem_cfg["frontier-intelligence"]["label"] == "Frontier Intelligence"


# ---------------------------------------------------------------------------
# Derived-id path (id omitted) — slugify + collision auto-suffix + label norm
# ---------------------------------------------------------------------------

@pytest.fixture
def _no_known_providers(monkeypatch):
    """No tier-1/tier-2 providers so derived-id collisions come only from cfg."""
    monkeypatch.setattr("openprogram.providers.get_providers", lambda: [])
    from openprogram.webui._model_listing import sources as S
    monkeypatch.setattr(S.models_dev, "list_providers", lambda: [])


def test_derived_id_slugifies_spaces_and_case(mem_cfg, _no_known_providers):
    res = st.create_custom_provider("", "Frontier Intelligence", "https://x.test/v1")
    assert res["ok"] is True
    assert res["id"] == "frontier-intelligence"
    assert res["label"] == "Frontier Intelligence"
    assert "frontier-intelligence" in mem_cfg


def test_derived_id_strips_illegal_chars(mem_cfg, _no_known_providers):
    res = st.create_custom_provider("", "My  Cool!! Provider (v2)", "https://x.test/v1")
    assert res["ok"] is True
    assert res["id"] == "my-cool-provider-v2"


def test_derived_id_cjk_only_name_rejected(mem_cfg, _no_known_providers):
    res = st.create_custom_provider("", "自定义供应商", "https://x.test/v1")
    assert res["ok"] is False and "letters or digits" in res["error"].lower()
    assert res == res | {"ok": False}  # nothing created


def test_derived_id_emoji_only_name_rejected(mem_cfg, _no_known_providers):
    res = st.create_custom_provider("", "🚀🔥", "https://x.test/v1")
    assert res["ok"] is False and "letters or digits" in res["error"].lower()


def test_derived_id_collision_auto_suffixes(mem_cfg, _no_known_providers):
    r1 = st.create_custom_provider("", "Frontier Intelligence", "https://x.test/v1")
    r2 = st.create_custom_provider("", "Frontier Intelligence", "https://y.test/v1")
    r3 = st.create_custom_provider("", "Frontier Intelligence", "https://z.test/v1")
    assert r1["id"] == "frontier-intelligence"
    assert r2["id"] == "frontier-intelligence-2"
    assert r3["id"] == "frontier-intelligence-3"
    assert {r1["id"], r2["id"], r3["id"]} <= set(mem_cfg)


def test_explicit_id_collision_still_400(mem_cfg, _no_known_providers):
    # An EXPLICIT id colliding with a non-custom config key must 400, never
    # auto-suffix (API compatibility — only the derived path auto-resolves).
    mem_cfg["realprovider"] = {"enabled": True}  # not source=custom
    res = st.create_custom_provider("realprovider", "Real", "https://x.test/v1")
    assert res["ok"] is False and "exists" in res["error"].lower()


def test_label_normalization_title_cases_lowercase(mem_cfg, _no_known_providers):
    res = st.create_custom_provider("", "  frontier   intelligence  ", "https://x.test/v1")
    assert res["ok"] is True
    assert res["label"] == "Frontier Intelligence"


def test_label_normalization_preserves_mixed_case(mem_cfg, _no_known_providers):
    res = st.create_custom_provider("", "OpenAI Compatible", "https://x.test/v1")
    assert res["ok"] is True
    assert res["label"] == "OpenAI Compatible"
    assert res["id"] == "openai-compatible"


# ---------------------------------------------------------------------------
# Tier-3 listing
# ---------------------------------------------------------------------------

def test_list_providers_surfaces_custom_tier3(mem_cfg, monkeypatch):
    import openprogram.providers as P
    from openprogram.webui._model_listing import sources as S
    monkeypatch.setattr(P, "get_providers", lambda: [])
    monkeypatch.setattr(P, "get_models", lambda pid: [])
    monkeypatch.setattr(S.models_dev, "list_providers", lambda: [])
    monkeypatch.setattr(cat, "_is_configured", lambda pid: False)
    mem_cfg["frontier-intelligence"] = {
        "enabled": True,
        "source": "custom",
        "label": "Frontier",
        "base_url": "https://api.frontier.tech/v1",
        "models": [{"id": "m1"}],
    }
    rows = {p["id"]: p for p in listing.list_providers()}
    assert "frontier-intelligence" in rows
    row = rows["frontier-intelligence"]
    assert row["custom"] is True
    assert row["label"] == "Frontier"
    assert row["kind"] == "api"
    assert row["base_url"] == "https://api.frontier.tech/v1"
    assert row["model_count"] == 1
    # api_key_env is synthesised so the frontend renders the keys section.
    assert row["api_key_env"] == "FRONTIER_INTELLIGENCE_API_KEY"


def test_custom_row_sorts_last(mem_cfg, monkeypatch):
    import openprogram.providers as P
    from openprogram.providers.types import Model
    from openprogram.webui._model_listing import sources as S
    monkeypatch.setattr(P, "get_providers", lambda: ["zzz-provider"])
    monkeypatch.setattr(P, "get_models", lambda pid: [Model.model_validate({
        "id": "z", "name": "Z", "provider": pid, "api": "openai-completions",
        "base_url": "https://z.test/v1",
    })])
    monkeypatch.setattr(cat, "_label", lambda pid: pid)
    monkeypatch.setattr(cat, "_is_configured", lambda pid: False)
    monkeypatch.setattr(S.models_dev, "list_providers", lambda: [])
    # A custom provider whose label alphabetically sorts BEFORE "zzz-provider"
    # must still land at the end.
    mem_cfg["zzz-provider"] = {"enabled": False}
    mem_cfg["aaa-custom"] = {
        "enabled": True, "source": "custom", "label": "AAA Custom",
        "base_url": "https://a.test/v1", "models": [],
    }
    ids = [p["id"] for p in listing.list_providers()]
    assert ids[-1] == "aaa-custom"  # custom last despite "AAA" sort


# ---------------------------------------------------------------------------
# delete_custom_provider
# ---------------------------------------------------------------------------

def test_delete_refuses_non_custom(mem_cfg):
    mem_cfg["openai"] = {"enabled": True}  # not source=custom
    res = st.delete_custom_provider("openai")
    assert res["ok"] is False and "not a custom" in res["error"].lower()
    assert "openai" in mem_cfg  # untouched


def test_delete_removes_custom(mem_cfg):
    mem_cfg["frontier-intelligence"] = {
        "enabled": True, "source": "custom", "label": "F",
        "base_url": "https://x.test/v1", "models": [{"id": "m1"}],
    }
    res = st.delete_custom_provider("frontier-intelligence")
    assert res["ok"] is True
    assert "frontier-intelligence" not in mem_cfg


# ---------------------------------------------------------------------------
# Manual model add → working ENABLED_MODELS entry after reload
# ---------------------------------------------------------------------------

def test_manual_model_add_yields_enabled_registry_entry(mem_cfg, monkeypatch):
    mem_cfg["frontier-intelligence"] = {
        "enabled": True, "source": "custom", "label": "F",
        "base_url": "https://api.frontier.tech/v1", "models": [],
    }
    res = st.add_manual_model("frontier-intelligence", "test-model")
    assert res["ok"] is True
    row = {r["id"]: r for r in mem_cfg["frontier-intelligence"]["models"]}["test-model"]
    assert row["source"] == "manual"
    assert row["api"] == "openai-completions"
    assert row["base_url"] == "https://api.frontier.tech/v1"

    # After a real reload the model resolves in the runtime registry.
    import openprogram.providers._config_read as cr
    import openprogram.providers.enabled_models as mg
    monkeypatch.setattr(cr, "read_providers_config", lambda: st._read_providers_cfg())
    mg.reload()
    key = "frontier-intelligence/test-model"
    assert key in mg.ENABLED_MODELS
    m = mg.ENABLED_MODELS[key]
    assert m.base_url == "https://api.frontier.tech/v1"
    assert m.api == "openai-completions"


def test_manual_add_empty_id_rejected(mem_cfg):
    mem_cfg["acme"] = {"source": "custom", "base_url": "https://x/v1", "models": []}
    res = st.add_manual_model("acme", "")
    assert res["ok"] is False


def test_manual_add_unknown_provider_rejected(mem_cfg, monkeypatch):
    # An unknown provider (not tier-1/tier-2, no custom config key) would write
    # an ENABLED_MODELS row with an empty base_url that can't dispatch — reject
    # it and write nothing.
    monkeypatch.setattr("openprogram.providers.get_providers", lambda: [])
    from openprogram.webui._model_listing import sources as S
    monkeypatch.setattr(S.models_dev, "list_providers", lambda: [])
    res = st.add_manual_model("totally-not-a-provider", "ghost-model")
    assert res["ok"] is False and "unknown provider" in res["error"].lower()
    assert "totally-not-a-provider" not in mem_cfg


# ---------------------------------------------------------------------------
# toggle_model for a dir-less custom provider — spec built from browse row +
# provider config base_url
# ---------------------------------------------------------------------------

def test_toggle_dirless_provider_builds_full_spec(mem_cfg, monkeypatch):
    mem_cfg["frontier-intelligence"] = {
        "enabled": True, "source": "custom", "label": "F",
        "base_url": "https://api.frontier.tech/v1", "models": [],
    }
    # Provider has a key → browse hits the (custom-routed) official API.
    monkeypatch.setattr(cat, "_is_configured", lambda pid: True)
    monkeypatch.setattr(pm, "_models_dev_for", lambda pid: {})  # no models.dev
    monkeypatch.setattr(F, "fetch_and_normalize", lambda pid, timeout=15.0: {
        "models": [{"id": "browsed-1", "name": "Browsed One"}],
    })
    from openprogram.webui._model_listing import toggle as tg
    res = tg.toggle_model("frontier-intelligence", "browsed-1", True)
    assert res["enabled"] is True
    rows = {r["id"]: r for r in mem_cfg["frontier-intelligence"]["models"]}
    spec = rows["browsed-1"]
    # base_url stamped from provider config (browse row had none).
    assert spec["base_url"] == "https://api.frontier.tech/v1"
    assert spec["api"] == "openai-completions"

    # And it resolves in the runtime registry after reload.
    import openprogram.providers._config_read as cr
    import openprogram.providers.enabled_models as mg
    monkeypatch.setattr(cr, "read_providers_config", lambda: st._read_providers_cfg())
    mg.reload()
    assert "frontier-intelligence/browsed-1" in mg.ENABLED_MODELS
    assert mg.ENABLED_MODELS[
        "frontier-intelligence/browsed-1"
    ].base_url == "https://api.frontier.tech/v1"


def test_custom_browse_fetch_failure_is_empty_not_error(mem_cfg, monkeypatch):
    # A custom provider whose /models 401s / is unimplemented degrades to an
    # empty browse list, never raising (and never caching failure as success).
    mem_cfg["frontier-intelligence"] = {
        "enabled": True, "source": "custom", "label": "F",
        "base_url": "https://api.frontier.tech/v1", "models": [],
    }
    monkeypatch.setattr(cat, "_is_configured", lambda pid: True)
    monkeypatch.setattr(pm, "_models_dev_for", lambda pid: {})
    monkeypatch.setattr(F, "fetch_and_normalize",
                        lambda pid, timeout=15.0: {"error": "HTTP 401"})
    rows = listing.list_models_for_provider("frontier-intelligence")
    assert rows == []  # graceful empty, no raise

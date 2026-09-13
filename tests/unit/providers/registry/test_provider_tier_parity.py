"""Provider-listing tier parity + manual-row lifecycle regressions.

Covers the fix batch after 62483bac (same root cause: a provider moving
between listing tiers must not change field semantics or lose data):

  * _repair_over_merged_specs must NOT delete user-added manual rows on a
    config that never had a legacy ``enabled_models`` list (fresh installs
    never bump the migration marker, so the pass re-runs every process);
  * fresh install (empty registry/config) still lists the shipped static
    providers — incl. the subscription ones models.dev doesn't know;
  * a community provider promoted into tier 1 (user enabled a model) keeps
    supports_fetch / doc_url;
  * a custom provider promoted into tier 1 keeps its user label, custom
    badge, synthesised api_key_env and supports_fetch;
  * disabling a manual model keeps its spec row (enabled: false) and the
    toggle can re-enable it.

No network, no real config: config IO + models.dev + _is_configured are
stubbed at the seam each module binds (same pattern as
test_browse_live_and_refresh / test_custom_providers).
"""
from __future__ import annotations

import copy

import pytest

from openprogram.webui._model_listing import listing
from openprogram.webui._model_listing import provider_models as pm
from openprogram.providers import metadata as cat
from openprogram.providers import storage as st
from openprogram.webui._model_listing import toggle as tg


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
    def _update(mutator):
        current = copy.deepcopy(store)
        result = mutator(current)
        _write(current)
        return result

    monkeypatch.setattr(st, "_read_providers_cfg", _read)
    monkeypatch.setattr(st, "_write_providers_cfg", _write)
    monkeypatch.setattr(st, "_update_providers_cfg", _update)
    monkeypatch.setattr(tg, "_update_providers_cfg", _update)
    # The spec-migration marker lives in the TOP-LEVEL config
    # (setup._read_config / _write_config), not in the providers
    # section stubbed above. Without these two stubs the migration pass
    # that create_custom_provider / add_manual_model trigger READS the
    # host's real ~/.openprogram marker (making the outcome depend on
    # the machine and on whatever earlier tests did to that file), and
    # a triggered repair WRITES the user's real config from a unit
    # test. Individual tests re-stub _spec_migration_version to
    # exercise the pre-migration path explicitly.
    monkeypatch.setattr(
        st, "_spec_migration_version", lambda: st._SPEC_MIGRATION_VERSION)
    monkeypatch.setattr(st, "_bump_spec_migration_version", lambda: None)
    st._reset_spec_migration()
    yield store
    # Leave the process flags in the settled state so no later test's
    # first _read_providers_cfg re-runs the migration against the real
    # config mid-suite.
    st._spec_migration_done = True
    st._spec_migration_running = False


@pytest.fixture
def _offline(monkeypatch):
    """Keep every models.dev lookup off the network."""
    from openprogram.providers import sources as S
    monkeypatch.setattr(S.models_dev, "list_providers", lambda: [])
    monkeypatch.setattr(cat, "_models_dev_info", lambda pid: {})
    monkeypatch.setattr(pm, "_models_dev_for", lambda pid: {})


# ---------------------------------------------------------------------------
# Fix 1: repair pass must not delete add_manual_model rows
# ---------------------------------------------------------------------------

def test_repair_keeps_manual_rows_without_legacy_list(mem_cfg, _offline, monkeypatch):
    # Fresh-install shape: custom provider created + model typed by hand,
    # no legacy enabled_models list anywhere, migration marker still 0.
    res = st.create_custom_provider("acme", "Acme", "https://acme.test/v1")
    assert res["ok"] is True
    assert st.add_manual_model("acme", "m1")["ok"] is True
    providers = st._read_providers_cfg()
    monkeypatch.setattr(st, "_spec_migration_version", lambda: 0)
    assert st._repair_over_merged_specs(providers) is False
    assert [r["id"] for r in providers["acme"]["models"]] == ["m1"]


def test_repair_still_prunes_v1_bulk_merge_artefacts(monkeypatch):
    # The case the pass exists for: legacy enabled_models present, manual-
    # tagged rows beyond it are v1 bulk-merge flood — pruned.
    providers = {"openrouter": {
        "enabled_models": ["keep"],
        "models": [
            {"id": "keep", "source": "manual"},
            {"id": "flood", "source": "manual"},
        ],
    }}
    monkeypatch.setattr(st, "_spec_migration_version", lambda: 0)
    assert st._repair_over_merged_specs(providers) is True
    assert [r["id"] for r in providers["openrouter"]["models"]] == ["keep"]


# ---------------------------------------------------------------------------
# Fix 2: fresh install lists the shipped static providers
# ---------------------------------------------------------------------------

def test_fresh_install_lists_shipped_providers(mem_cfg, _offline, monkeypatch):
    import openprogram.providers as P
    monkeypatch.setattr(P, "get_providers", lambda: [])   # empty registry
    monkeypatch.setattr(P, "get_models", lambda pid: [])
    monkeypatch.setattr(cat, "is_configured", lambda pid: False)
    ids = {p["id"] for p in listing.list_providers()}
    # Subscription providers models.dev doesn't list MUST still be present.
    assert {"openai-codex", "claude-code", "gemini-subscription"} <= ids
    # Regular shipped key providers too.
    assert {"openai", "anthropic", "deepseek"} <= ids
    # Alias dirs never surface as their own row.
    assert "chatgpt-subscription" not in ids
    # Wire-format metadata dirs are not providers.
    assert "openai-completions" not in ids and "openai-responses" not in ids


# ---------------------------------------------------------------------------
# Fix 3: tier promotion keeps field semantics
# ---------------------------------------------------------------------------

def _model(pid, mid="m1", base="https://x.test/v1"):
    from openprogram.providers.types import Model
    return Model.model_validate({
        "id": mid, "name": mid, "provider": pid,
        "api": "openai-completions", "base_url": base,
    })


def test_promoted_community_provider_keeps_supports_fetch(mem_cfg, monkeypatch):
    import openprogram.providers as P
    from openprogram.providers import sources as S
    monkeypatch.setattr(P, "get_providers", lambda: ["fireworks"])
    monkeypatch.setattr(P, "get_models", lambda pid: [_model(pid)])
    monkeypatch.setattr(cat, "_shipped_provider_ids", lambda: set())
    monkeypatch.setattr(cat, "is_configured", lambda pid: True)
    monkeypatch.setattr(cat, "_models_dev_info", lambda pid: {})
    monkeypatch.setattr(S.models_dev, "list_providers", lambda: [{
        "id": "fireworks", "label": "Fireworks",
        "env_var": "FIREWORKS_API_KEY",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "doc_url": "https://docs.fireworks.ai",
        "model_ids": ["m1", "m2", "m3"],
    }])
    mem_cfg["fireworks"] = {"enabled": True, "models": [{"id": "m1"}]}
    rows = {p["id"]: p for p in listing.list_providers()}
    row = rows["fireworks"]
    # Promoted into tier 1 (registry has its model) yet keeps tier-2 traits.
    assert row["supports_fetch"] is True
    assert row["doc_url"] == "https://docs.fireworks.ai"
    assert row["enabled_model_count"] == 1
    # Exactly one row — no tier-2 duplicate.
    assert sum(1 for p in listing.list_providers() if p["id"] == "fireworks") == 1


def test_promoted_custom_provider_keeps_label_badge_and_fetch(mem_cfg, _offline, monkeypatch):
    import openprogram.providers as P
    monkeypatch.setattr(P, "get_providers", lambda: ["my-vllm"])
    monkeypatch.setattr(P, "get_models", lambda pid: [_model(pid, "qwen3")])
    monkeypatch.setattr(cat, "_shipped_provider_ids", lambda: set())
    monkeypatch.setattr(cat, "is_configured", lambda pid: False)
    mem_cfg["my-vllm"] = {
        "enabled": True, "source": "custom", "label": "My vLLM",
        "base_url": "http://localhost:8000/v1",
        "models": [{"id": "qwen3", "source": "manual"}],
    }
    rows = [p for p in listing.list_providers() if p["id"] == "my-vllm"]
    assert len(rows) == 1  # tier 1 row, no tier-3 duplicate
    row = rows[0]
    assert row["label"] == "My vLLM"
    assert row["custom"] is True
    assert row["supports_fetch"] is True
    assert row["api_key_env"] == "MY_VLLM_API_KEY"


def test_tier2_rows_carry_setup_hint_and_login_methods(mem_cfg, monkeypatch):
    # anthropic listed only by the community catalogue (nothing enabled,
    # shipped tier stubbed away) must still surface its native login methods
    # and setup hint — a fresh install's first screen.
    import openprogram.providers as P
    from openprogram.providers import sources as S
    monkeypatch.setattr(P, "get_providers", lambda: [])
    monkeypatch.setattr(P, "get_models", lambda pid: [])
    monkeypatch.setattr(cat, "_shipped_provider_ids", lambda: set())
    monkeypatch.setattr(cat, "is_configured", lambda pid: False)
    monkeypatch.setattr(cat, "_models_dev_info", lambda pid: {})
    monkeypatch.setattr(S.models_dev, "list_providers", lambda: [{
        "id": "anthropic", "label": "Anthropic", "env_var": "ANTHROPIC_API_KEY",
        "base_url": "https://api.anthropic.com", "doc_url": None,
        "model_ids": ["claude-opus-4-8"],
    }])
    rows = {p["id"]: p for p in listing.list_providers()}
    row = rows["anthropic"]
    assert row.get("setup_hint")
    assert [m["id"] for m in row.get("login_methods", [])]  # native logins


# ---------------------------------------------------------------------------
# Fix 6: disabling a manual row keeps it; toggle on restores it
# ---------------------------------------------------------------------------

def test_manual_row_disable_keeps_row_and_reenables(mem_cfg, _offline, monkeypatch):
    monkeypatch.setattr(cat, "is_configured", lambda pid: False)
    mem_cfg["acme"] = {
        "enabled": True, "source": "custom", "label": "Acme",
        "base_url": "https://acme.test/v1", "models": [],
    }
    assert st.add_manual_model("acme", "m1")["ok"] is True

    tg.toggle_model("acme", "m1", False)
    rows = mem_cfg["acme"]["models"]
    assert [r["id"] for r in rows] == ["m1"]          # row survives
    assert rows[0]["enabled"] is False
    assert "m1" not in listing._enabled_ids(mem_cfg["acme"])
    listed = {m["id"]: m for m in listing.list_models_for_provider("acme")}
    assert listed["m1"]["enabled"] is False           # visible, toggled off

    listing._reset_browse_cache()
    tg.toggle_model("acme", "m1", True)
    rows = mem_cfg["acme"]["models"]
    assert [r["id"] for r in rows] == ["m1"]
    assert rows[0].get("enabled") is not False
    assert rows[0].get("source") == "manual"          # provenance kept
    assert "m1" in listing._enabled_ids(mem_cfg["acme"])


def test_disabled_manual_row_excluded_from_runtime_registry(mem_cfg, _offline):
    from openprogram.providers.enabled_models import _build_model_from_row  # noqa: F401
    import openprogram.providers.enabled_models as mg
    import openprogram.providers._config_read as cr
    cfg = {"acme": {"enabled": True, "models": [
        {"id": "on", "name": "on", "api": "openai-completions",
         "base_url": "https://a.test/v1"},
        {"id": "off", "name": "off", "api": "openai-completions",
         "base_url": "https://a.test/v1", "source": "manual", "enabled": False},
    ]}}
    orig = cr.read_providers_config
    cr.read_providers_config = lambda: cfg
    try:
        fresh = mg._load()
    finally:
        cr.read_providers_config = orig
    assert "acme/on" in fresh and "acme/off" not in fresh

"""Task 2: enable-copies-spec write path + one-time storage migration.

The only persisted model data is moving to "full spec of each user-enabled
model, stored under config.providers.<p>.models (list[dict])". This pins:

  * toggle_model(enable) copies the FULL listing row into providers.<p>.models
    (nested `cost` preserved, not flattened);
  * toggle_model(disable) removes that row;
  * enabled_models (old id list) is still maintained in parallel (双写);
  * the storage-layer migration backfills spec rows from enabled_models for
    existing configs, merges custom_models with source="manual", and keeps
    ids it can't resolve.
"""
from __future__ import annotations

import pytest

from openprogram.webui._model_catalog import storage as st
from openprogram.webui._model_catalog import toggle as tg


@pytest.fixture
def mem_cfg(monkeypatch):
    """In-memory providers config, wired through storage read/write."""
    import copy
    store: dict = {}
    # Read returns a deep copy so the caller's in-place mutation only lands
    # via _write_providers_cfg (mirrors real config.json round-tripping).
    _read = lambda: copy.deepcopy(store)
    _write = lambda cfg: store.clear() or store.update(copy.deepcopy(cfg))
    # toggle.py binds these names at import time, so patch it too (not just st).
    monkeypatch.setattr(st, "_read_providers_cfg", _read)
    monkeypatch.setattr(st, "_write_providers_cfg", _write)
    monkeypatch.setattr(tg, "_read_providers_cfg", _read)
    monkeypatch.setattr(tg, "_write_providers_cfg", _write)
    st._reset_spec_migration()
    return store


@pytest.fixture
def stub_listing(monkeypatch):
    """A single provider "acme" with one model carrying a NESTED cost object
    plus key_prefix — the fields the spec row must preserve verbatim."""
    row = {
        "id": "acme-1",
        "name": "Acme One",
        "api": "anthropic-messages",
        "base_url": "https://acme.example/anthropic",
        "context_window": 200000,
        "max_tokens": 8192,
        "vision": True,
        "reasoning": True,
        "thinking_levels": ["low", "high"],
        "default_thinking_level": "low",
        "thinking_variant": "opus47",
        "tools": True,
        "cost": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
        "headers": {"x-acme": "1"},
        "key_prefix": "acme-plan",
        "enabled": False,  # UI flag — must NOT leak into the stored spec
    }

    def _list(provider_id):
        return [dict(row)] if provider_id == "acme" else []

    import openprogram.webui._model_catalog.listing as listing
    monkeypatch.setattr(listing, "list_models_for_provider", _list)
    return row


def test_enable_writes_full_spec_row(mem_cfg, stub_listing):
    tg.toggle_model("acme", "acme-1", True)
    rows = mem_cfg["acme"]["models"]
    assert len(rows) == 1
    spec = rows[0]
    # Nested cost preserved verbatim (not flattened).
    assert spec["cost"] == {"input": 3.0, "output": 15.0,
                            "cache_read": 0.3, "cache_write": 3.75}
    assert spec["id"] == "acme-1"
    assert spec["name"] == "Acme One"
    assert spec["api"] == "anthropic-messages"
    assert spec["base_url"] == "https://acme.example/anthropic"
    assert spec["context_window"] == 200000
    assert spec["max_tokens"] == 8192
    assert spec["thinking_levels"] == ["low", "high"]
    assert spec["default_thinking_level"] == "low"
    assert spec["thinking_variant"] == "opus47"
    assert spec["headers"] == {"x-acme": "1"}
    assert spec["key_prefix"] == "acme-plan"
    assert "enabled" not in spec  # UI-only flag stripped
    # 双写: old id list still maintained.
    assert mem_cfg["acme"]["enabled_models"] == ["acme-1"]


def test_disable_removes_spec_row(mem_cfg, stub_listing):
    tg.toggle_model("acme", "acme-1", True)
    tg.toggle_model("acme", "acme-1", False)
    assert mem_cfg["acme"].get("models", []) == []
    assert mem_cfg["acme"]["enabled_models"] == []


def test_enable_is_idempotent(mem_cfg, stub_listing):
    tg.toggle_model("acme", "acme-1", True)
    tg.toggle_model("acme", "acme-1", True)
    assert len(mem_cfg["acme"]["models"]) == 1


def test_migration_backfills_spec_from_enabled_models(monkeypatch, stub_listing):
    """Existing config: enabled_models id but no models row → backfilled."""
    store = {"acme": {"enabled": True, "enabled_models": ["acme-1"]}}
    monkeypatch.setattr(st, "_read_providers_cfg", lambda: dict(store))
    written = {}
    monkeypatch.setattr(st, "_write_providers_cfg",
                        lambda cfg: written.update(cfg))
    st._reset_spec_migration()

    st._migrate_specs(store)
    rows = store["acme"]["models"]
    assert [r["id"] for r in rows] == ["acme-1"]
    # Spec equivalent to the listing/registry entry (nested cost intact).
    assert rows[0]["cost"]["input"] == 3.0
    # enabled_models untouched (双写, read paths not switched).
    assert store["acme"]["enabled_models"] == ["acme-1"]


def test_migration_keeps_unresolvable_ids(monkeypatch, stub_listing):
    """An id the listing can't resolve stays in enabled_models, not dropped."""
    store = {"acme": {"enabled": True, "enabled_models": ["acme-1", "ghost"]}}
    st._reset_spec_migration()
    st._migrate_specs(store)
    ids = [r["id"] for r in store["acme"]["models"]]
    assert "acme-1" in ids
    assert "ghost" not in ids  # couldn't resolve → no spec row
    assert "ghost" in store["acme"]["enabled_models"]  # but not dropped


def test_migration_merges_custom_models_as_manual(monkeypatch, stub_listing):
    store = {"acme": {
        "enabled": True,
        "enabled_models": [],
        "custom_models": [{"id": "manual-x", "name": "Manual X",
                           "context_window": 4096}],
    }}
    st._reset_spec_migration()
    st._migrate_specs(store)
    rows = store["acme"]["models"]
    manual = next(r for r in rows if r["id"] == "manual-x")
    assert manual["source"] == "manual"
    assert manual["name"] == "Manual X"
    # Original custom_models key left untouched (read paths not switched).
    assert store["acme"]["custom_models"] == [
        {"id": "manual-x", "name": "Manual X", "context_window": 4096}
    ]

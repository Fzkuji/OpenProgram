"""Task 2: enable-copies-spec write path + one-time storage migration.

The only persisted model data is moving to "full spec of each user-enabled
model, stored under config.providers.<p>.models (list[dict])". This pins:

  * toggle_model(enable) copies the FULL listing row into providers.<p>.models
    (nested `cost` preserved, not flattened);
  * toggle_model(disable) removes that row;
  * enabled_models (old id list) is NO LONGER maintained — spec rows are the
    single source of truth (Task 5 retired the dual-write);
  * the storage-layer migration backfills spec rows from enabled_models for
    existing configs, merges custom_models with source="manual", and keeps
    ids it can't resolve (read-side compat for old configs — still there).
"""
from __future__ import annotations

import pytest

from openprogram.webui._model_listing import storage as st
from openprogram.webui._model_listing import toggle as tg


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

    import openprogram.webui._model_listing.listing as listing
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
    # Single-write: the legacy id list is no longer maintained.
    assert "enabled_models" not in mem_cfg["acme"]


def test_disable_removes_spec_row(mem_cfg, stub_listing):
    tg.toggle_model("acme", "acme-1", True)
    tg.toggle_model("acme", "acme-1", False)
    assert mem_cfg["acme"].get("models", []) == []
    assert "enabled_models" not in mem_cfg["acme"]


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


def test_migration_builds_minimal_row_when_unresolvable(monkeypatch, stub_listing):
    """C2: an id live browse can't resolve (offline) still gets a MINIMAL spec
    row built from local metadata, so it resolves without network. "acme" has
    no provider dir → api defaults to openai-completions, tagged
    migration-minimal so a later online Refresh overwrites it."""
    store = {"acme": {"enabled": True, "base_url": "https://acme.example/v1",
                      "enabled_models": ["acme-1", "ghost"]}}
    st._reset_spec_migration()
    st._migrate_specs(store)
    rows = {r["id"]: r for r in store["acme"]["models"]}
    assert "acme-1" in rows            # resolved via listing (full spec)
    ghost = rows["ghost"]              # built minimal, not dropped
    assert ghost["api"] == "openai-completions"
    assert ghost["base_url"] == "https://acme.example/v1"  # user base_url fallback
    assert ghost["source"] == "migration-minimal"


def test_migration_merges_only_enabled_custom_models_as_manual(monkeypatch, stub_listing):
    """Only a custom_models row whose id is in enabled_models is merged as a
    spec row. A non-enabled custom row is an availability cache, not user
    enablement — it stays in custom_models and never enters the registry.
    """
    # "manual-x" is enabled but unresolvable by the stub listing → it comes in
    # via the enabled-backfill path (as a manual entry, not a minimal one, so
    # we exercise the custom-merge branch): make it resolvable-as-custom by
    # NOT listing it and relying on the custom row. Use a distinct enabled id
    # the listing can't resolve so the custom row is the only source.
    store = {"acme": {
        "enabled": True,
        "enabled_models": ["manual-x"],
        "custom_models": [
            {"id": "manual-x", "name": "Manual X", "context_window": 4096},
            {"id": "cached-y", "name": "Cached Y", "context_window": 2048},
        ],
    }}
    st._reset_spec_migration()
    st._migrate_specs(store)
    rows = store["acme"]["models"]
    ids = {r["id"] for r in rows}
    # The enabled id landed as a spec row (via backfill or custom-merge)...
    assert "manual-x" in ids
    # ...but the non-enabled cache row did NOT enter the registry.
    assert "cached-y" not in ids
    # Original custom_models key left fully untouched (read paths not switched).
    assert store["acme"]["custom_models"] == [
        {"id": "manual-x", "name": "Manual X", "context_window": 4096},
        {"id": "cached-y", "name": "Cached Y", "context_window": 2048},
    ]


# --- Repair pass: prune the v1 bulk-merge from already-migrated configs -----


def _repair_provider():
    """A provider already polluted by the v1 bulk merge: 3 enabled + a flood of
    non-enabled manual rows, plus rows the repair MUST keep."""
    return {
        "enabled": True,
        "enabled_models": ["keep-a", "keep-b"],
        "models": [
            # id in enabled_models → keep even though tagged manual
            {"id": "keep-a", "name": "Keep A", "source": "manual"},
            # toggled AFTER migration → no source key → keep
            {"id": "keep-b", "name": "Keep B"},
            # migration-minimal → id in enabled by construction → keep
            {"id": "keep-b", "name": "Keep B min", "source": "migration-minimal"},
            # bulk-merge artefacts (manual, not enabled) → DROP
            {"id": "flood-1", "name": "Flood 1", "source": "manual"},
            {"id": "flood-2", "name": "Flood 2", "source": "manual"},
        ],
    }


def test_repair_prunes_non_enabled_manual_rows_only():
    providers = {"p": _repair_provider()}
    # exercise via a stubbed version marker (< target) so the pass runs
    import openprogram.webui.server as server
    _orig = server._load_config
    server._load_config = lambda: {"spec_migration_version": 0}
    try:
        repaired = st._repair_over_merged_specs(providers)
    finally:
        server._load_config = _orig
    assert repaired is True
    ids = [r["id"] for r in providers["p"]["models"]]
    assert "flood-1" not in ids and "flood-2" not in ids
    # every keep row survives (including the no-source and migration-minimal)
    assert ids.count("keep-a") == 1
    assert sum(1 for i in ids if i == "keep-b") == 2


def test_repair_is_one_shot_via_version_marker():
    providers = {"p": _repair_provider()}
    import openprogram.webui.server as server
    _orig = server._load_config
    # marker already at the target version → repair is a no-op
    server._load_config = lambda: {"spec_migration_version": st._SPEC_MIGRATION_VERSION}
    try:
        repaired = st._repair_over_merged_specs(providers)
    finally:
        server._load_config = _orig
    assert repaired is False
    # nothing pruned
    assert len(providers["p"]["models"]) == 5


# --- Toggle refreshes the runtime registry in place (no process restart) ----


@pytest.fixture
def live_cfg(monkeypatch):
    """Persistent in-memory config wired through the REAL _write_providers_cfg
    (so its reload() side-effect fires) and the REAL enabled_models registry
    (so we prove the chat picker sees the toggle without a restart)."""
    import copy
    import openprogram.providers._config_read as cr
    import openprogram.providers.enabled_models as mg

    store: dict = {}
    _read = lambda: copy.deepcopy(store)

    def _save(cfg):
        store.clear()
        store.update(copy.deepcopy(cfg.get("providers", cfg)))

    # storage._write_providers_cfg goes through server._load_config/_save_config,
    # then calls enabled_models.reload(); reload() reads via cr.read_providers_config.
    import openprogram.webui.server as server
    monkeypatch.setattr(server, "_load_config", lambda: {"providers": copy.deepcopy(store)})
    monkeypatch.setattr(server, "_save_config", _save)
    monkeypatch.setattr(cr, "read_providers_config", lambda: copy.deepcopy(store))
    monkeypatch.setattr(st, "_read_providers_cfg", _read)
    monkeypatch.setattr(tg, "_read_providers_cfg", _read)
    st._reset_spec_migration()
    mg.reload()
    yield store
    mg.reload()  # leave the shared registry clean for the next test


def test_toggle_enable_refreshes_registry_without_restart(live_cfg, stub_listing):
    import openprogram.providers.enabled_models as mg
    from openprogram.providers.models import get_model
    from openprogram.webui._model_listing.listing import list_enabled_models

    live_cfg["acme"] = {"enabled": True}
    st._reset_spec_migration()
    mg.reload()
    assert get_model("acme-plan", "acme-1") is None  # key_prefix in stub row

    tg.toggle_model("acme", "acme-1", True)

    # Registry + picker reflect the enable immediately — no reload() call here.
    assert get_model("acme-plan", "acme-1") is not None
    assert any(r["id"] == "acme-1" for r in list_enabled_models())

    tg.toggle_model("acme", "acme-1", False)
    assert get_model("acme-plan", "acme-1") is None
    assert not any(r["id"] == "acme-1" for r in list_enabled_models())


def test_repair_pass_runs_through_run_once(monkeypatch):
    """End-to-end: _run_spec_migration_once fires the repair, bumps the marker,
    and persists the pruned providers."""
    config = {"spec_migration_version": 0, "providers": {"p": _repair_provider()}}
    import openprogram.webui.server as server
    monkeypatch.setattr(server, "_load_config", lambda: config)
    monkeypatch.setattr(server, "_save_config", lambda c: config.update(c))
    st._reset_spec_migration()
    st._run_spec_migration_once(config["providers"])
    ids = [r["id"] for r in config["providers"]["p"]["models"]]
    assert "flood-1" not in ids and "flood-2" not in ids
    # marker bumped so it never runs again
    assert config["spec_migration_version"] == st._SPEC_MIGRATION_VERSION

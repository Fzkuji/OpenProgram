"""Task 5: legacy readers switch to spec rows (providers.<p>.models).

After the dual-write retired, ``enabled_models`` is no longer written, so any
reader still counting from it would under-count. These pin that the readers
now derive from the spec rows, with the legacy id list kept only as a
migration-era fallback for not-yet-migrated configs.
"""
from __future__ import annotations

from openprogram.webui._model_catalog.listing import _enabled_ids
import openprogram.webui._runtime_management as rm


def test_enabled_ids_prefers_spec_rows():
    """Spec rows are the source of truth; enabled_models is only a fallback."""
    # Post-retirement shape: spec rows present, no enabled_models list.
    assert _enabled_ids({"models": [{"id": "m-1"}, {"id": "m-2"}]}) == {"m-1", "m-2"}
    # Spec rows win even if a stale enabled_models list disagrees.
    assert _enabled_ids(
        {"models": [{"id": "m-1"}], "enabled_models": ["ghost"]}
    ) == {"m-1"}
    # Not-yet-migrated config (no spec rows) → legacy fallback.
    assert _enabled_ids({"enabled_models": ["old-1"]}) == {"old-1"}
    # Neither → empty.
    assert _enabled_ids({}) == set()


def test_preferred_default_model_from_spec_rows(monkeypatch, tmp_path):
    """_preferred_default_model falls back to the first spec row id."""
    cfg = {"acme": {"enabled": True, "models": [{"id": "spec-first"}]}}
    import openprogram.webui._model_catalog as mc
    monkeypatch.setattr(mc, "_read_providers_cfg", lambda: cfg)
    # No top-level default_provider/default_model file → point at empty tmp.
    monkeypatch.setattr("openprogram.paths.get_config_path",
                        lambda: str(tmp_path / "nope.json"))
    assert rm._preferred_default_model("acme") == "spec-first"


def test_preferred_default_model_legacy_fallback(monkeypatch, tmp_path):
    """No spec rows → the legacy enabled_models list still resolves."""
    cfg = {"acme": {"enabled": True, "enabled_models": ["old-1"]}}
    import openprogram.webui._model_catalog as mc
    monkeypatch.setattr(mc, "_read_providers_cfg", lambda: cfg)
    monkeypatch.setattr("openprogram.paths.get_config_path",
                        lambda: str(tmp_path / "nope.json"))
    assert rm._preferred_default_model("acme") == "old-1"

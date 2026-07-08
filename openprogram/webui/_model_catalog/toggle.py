"""Enable / disable a provider or one of its models.

Both call sites are tiny — single-field updates to the providers
config — but they pair naturally because the UI also calls them
together (toggle a provider on, then check a few models on)."""
from __future__ import annotations

from typing import Any

from .storage import (
    _cache_lock,
    _read_providers_cfg,
    _remove_spec_row,
    _upsert_spec_row,
    _write_providers_cfg,
    spec_row_for,
)


def toggle_provider(provider_id: str, enabled: bool) -> dict[str, Any]:
    """Enable/disable a whole provider."""
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.setdefault(provider_id, {})
        pcfg["enabled"] = bool(enabled)
        _write_providers_cfg(cfg)
    return {"provider": provider_id, "enabled": bool(enabled)}


def toggle_model(provider_id: str, model_id: str, enabled: bool) -> dict[str, Any]:
    """Enable/disable ``model_id`` for a provider.

    Dual-writes both persistence layers (transition period):
      * the full spec row into ``providers.<p>.models`` (new source of truth
        the runtime will switch to) — copied from the current registry/listing
        via ``spec_row_for``;
      * the ``enabled_models`` id whitelist (old shape, still read everywhere).

    Idempotent both ways. If the spec can't be resolved (e.g. an id that isn't
    in the listing), the id-list write still happens so behaviour is unchanged.
    """
    # spec_row_for reads config → keep it OUTSIDE the lock (the lock is not
    # reentrant; _read_providers_cfg inside would deadlock).
    spec = spec_row_for(provider_id, model_id) if enabled else None
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.setdefault(provider_id, {})
        lst = pcfg.setdefault("enabled_models", [])
        if enabled:
            if model_id not in lst:
                lst.append(model_id)
            if spec is not None:
                _upsert_spec_row(pcfg, spec)
        else:
            if model_id in lst:
                lst.remove(model_id)
            _remove_spec_row(pcfg, model_id)
        _write_providers_cfg(cfg)
    return {"provider": provider_id, "model": model_id, "enabled": bool(enabled)}

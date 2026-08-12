"""Enable / disable a provider or one of its models.

Both call sites are tiny — single-field updates to the providers
config — but they pair naturally because the UI also calls them
together (toggle a provider on, then check a few models on)."""
from __future__ import annotations

from typing import Any

from openprogram.providers.storage import (
    _cache_lock,
    _remove_spec_row,
    _update_providers_cfg,
    _upsert_spec_row,
)

from .listing import spec_row_for


def toggle_provider(provider_id: str, enabled: bool) -> dict[str, Any]:
    """Enable/disable a whole provider."""
    with _cache_lock:

        def toggle(cfg: dict[str, Any]) -> None:
            cfg.setdefault(provider_id, {})["enabled"] = bool(enabled)

        _update_providers_cfg(toggle)
    return {"provider": provider_id, "enabled": bool(enabled)}


def toggle_model(provider_id: str, model_id: str, enabled: bool) -> dict[str, Any]:
    """Enable/disable ``model_id`` for a provider.

    The full spec row under ``providers.<p>.models`` is the single source of
    truth: enable copies the current registry/listing row (via ``spec_row_for``)
    in, disable removes it — EXCEPT manual rows (``source: "manual"``), which
    only live in config (the live browse can't resurface a hand-typed id), so
    disable keeps the row with ``enabled: false`` instead of destroying it.
    The legacy ``enabled_models`` id list is no longer maintained — readers
    derive enablement from the spec rows.

    Idempotent both ways. If the spec can't be resolved (e.g. an id that isn't
    in the listing), enable is a no-op for that id.
    """
    # spec_row_for reads config → keep it OUTSIDE the lock (the lock is not
    # reentrant; _read_providers_cfg inside would deadlock).
    # Enabling fires one live browse of the provider (via spec_row_for →
    # list_models_for_provider) to snapshot the current row — intended, and
    # cheap: browse results are TTL-cached, so a burst of enables shares one.
    spec = spec_row_for(provider_id, model_id) if enabled else None
    with _cache_lock:

        def toggle(cfg: dict[str, Any]) -> None:
            pcfg = cfg.setdefault(provider_id, {})
            existing = next(
                (
                    row
                    for row in (pcfg.get("models") or [])
                    if row.get("id") == model_id
                ),
                None,
            )
            if enabled:
                if existing is not None:
                    existing.pop("enabled", None)
                elif spec is not None:
                    _upsert_spec_row(pcfg, spec)
            else:
                keep = existing is not None and (
                    existing.get("source") == "manual"
                    or pcfg.get("source") == "custom"
                )
                if keep:
                    existing["enabled"] = False
                else:
                    _remove_spec_row(pcfg, model_id)

        _update_providers_cfg(toggle)
    return {"provider": provider_id, "model": model_id, "enabled": bool(enabled)}

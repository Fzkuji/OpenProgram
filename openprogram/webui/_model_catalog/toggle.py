"""Enable / disable a provider or one of its models.

Both call sites are tiny — single-field updates to the providers
config — but they pair naturally because the UI also calls them
together (toggle a provider on, then check a few models on)."""
from __future__ import annotations

from typing import Any

from .storage import _cache_lock, _read_providers_cfg, _write_providers_cfg


def toggle_provider(provider_id: str, enabled: bool) -> dict[str, Any]:
    """Enable/disable a whole provider."""
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.setdefault(provider_id, {})
        pcfg["enabled"] = bool(enabled)
        _write_providers_cfg(cfg)
    return {"provider": provider_id, "enabled": bool(enabled)}


def toggle_model(provider_id: str, model_id: str, enabled: bool) -> dict[str, Any]:
    """Add/remove ``model_id`` in the provider's ``enabled_models``
    whitelist. Idempotent: re-enabling an already-enabled model is a
    no-op, same for re-disabling."""
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.setdefault(provider_id, {})
        lst = pcfg.setdefault("enabled_models", [])
        if enabled and model_id not in lst:
            lst.append(model_id)
        elif not enabled and model_id in lst:
            lst.remove(model_id)
        _write_providers_cfg(cfg)
    return {"provider": provider_id, "model": model_id, "enabled": bool(enabled)}

"""Runtime model registry — built from the user's enabled models in config.

``ENABLED_MODELS`` holds ONLY the models the user has enabled (the full spec
rows persisted under config ``providers.<p>.models``) plus anything registered
dynamically at runtime (custom-model side-effect registration in the webui,
and the codex runtime-registration helper). Subscription providers no longer
seed rows at import — their default set is written to config as an enable at
login (``openprogram.auth.login_enable``). ``_load()`` reads those config spec
rows, fills
each row's missing api/base_url from the provider's ``providers/<p>/provider.json``
endpoints (row values win), and keys them ``"<key_prefix or provider>/<id>"``.

The dict object is MUTABLE and shared: dynamic writers do
``ENABLED_MODELS[k] = m`` in place. Public interface:
``from openprogram.providers.enabled_models import ENABLED_MODELS``.

An empty/missing config is a legal fresh-install state → empty registry.
"""
from __future__ import annotations

from .types import Model


def _build_model_from_row(row: dict, provider_id: str, endpoints: dict) -> Model:
    """A config spec row → Model. The row already carries most fields
    (incl. nested cost/headers/compat and usually ``api``); the provider's
    endpoint only fills api/base_url the row omits. Row values always win."""
    ep = endpoints.get(row.get("endpoint", "default")) or endpoints.get("default") or {}
    data = dict(row)
    data["provider"] = provider_id
    # Reader tolerance for configs that never pass through the webui spec
    # migration (pure-CLI users): map the legacy models.dev flat keys onto the
    # Model schema. ``input_modalities`` → ``input`` (filtered to the schema's
    # allowed values — drops "pdf" etc.); flat ``*_cost`` → nested ``cost``.
    # A row that already carries ``input``/``cost`` (webui-normalized) wins.
    if "input" not in data and "input_modalities" in data:
        _allowed = {"text", "image", "video", "audio"}
        mods = [m for m in (data.get("input_modalities") or []) if m in _allowed]
        data["input"] = mods or ["text"]
    if "cost" not in data and any(
        k in data for k in ("input_cost", "output_cost", "cache_read_cost", "cache_write_cost")
    ):
        data["cost"] = {
            "input": float(data.get("input_cost", 0) or 0),
            "output": float(data.get("output_cost", 0) or 0),
            "cache_read": float(data.get("cache_read_cost", 0) or 0),
            "cache_write": float(data.get("cache_write_cost", 0) or 0),
        }
    if not data.get("api"):
        data["api"] = ep.get("api", "openai-completions")
    if not data.get("base_url"):
        data["base_url"] = ep.get("base_url", "")
    return Model.model_validate(data)


def _load() -> dict[str, Model]:
    from ._config_read import read_providers_config
    from ._provider_meta import provider_endpoints
    from openprogram.auth.aliases import resolve

    merged: dict[str, Model] = {}
    try:
        providers_cfg = read_providers_config()
    except Exception:
        return merged
    cfg = providers_cfg or {}
    for provider_id, pcfg in cfg.items():
        if not isinstance(pcfg, dict):
            continue
        # An alias config key (e.g. legacy ``chatgpt-subscription``) whose
        # canonical id is ALSO a config key would produce duplicate registry
        # rows — same model twice in the picker, twice in the sidebar. Skip
        # the alias's rows; the canonical key owns them. A lone alias key
        # (canonical absent) still loads and routes via its resolved
        # endpoints — old configs keep working. get_model's alias fallback
        # keeps ``chatgpt-subscription/...`` lookups resolving either way.
        canon = resolve(provider_id)
        if canon != provider_id and canon in cfg:
            continue
        endpoints = provider_endpoints(provider_id)
        for row in (pcfg.get("models") or []):
            if not isinstance(row, dict):
                continue
            try:
                m = _build_model_from_row(row, provider_id, endpoints)
            except Exception:
                continue
            prefix = row.get("key_prefix") or provider_id
            merged[f"{prefix}/{m.id}"] = m
    return merged


ENABLED_MODELS: dict[str, Model] = _load()


def reload() -> dict[str, Model]:
    """Rebuild the registry from the current config spec rows, in place.

    Clears and repopulates the SAME ``ENABLED_MODELS`` dict object (never
    rebinds the name) so every module that did
    ``from ...enabled_models import ENABLED_MODELS`` sees the update —
    and dynamic writers' entries survive only if config still carries them.
    Called after a config write that changes enabled model specs (e.g. the
    Fetch/Refresh button).

    Returns the same dict for convenience.
    """
    fresh = _load()
    ENABLED_MODELS.clear()
    ENABLED_MODELS.update(fresh)
    return ENABLED_MODELS

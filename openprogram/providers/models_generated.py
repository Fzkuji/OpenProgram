"""Runtime model registry — built from the user's enabled models in config.

``MODEL_REGISTRY`` holds ONLY the models the user has enabled (the full spec
rows persisted under config ``providers.<p>.models``) plus anything registered
dynamically at runtime (the claude-code seed, and custom-model side-effect
registration in the webui). ``_load()`` reads those config spec rows, fills
each row's missing api/base_url from the provider's ``providers/<p>/provider.json``
endpoints (row values win), and keys them ``"<key_prefix or provider>/<id>"``.

The dict object is MUTABLE and shared: dynamic writers do
``MODEL_REGISTRY[k] = m`` in place. Public interface is unchanged
(``from openprogram.providers.models_generated import MODEL_REGISTRY``).

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
    if not data.get("api"):
        data["api"] = ep.get("api", "openai-completions")
    if not data.get("base_url"):
        data["base_url"] = ep.get("base_url", "")
    return Model.model_validate(data)


def _load() -> dict[str, Model]:
    from ._config_read import read_providers_config
    from ._provider_meta import provider_endpoints

    merged: dict[str, Model] = {}
    try:
        providers_cfg = read_providers_config()
    except Exception:
        return merged
    for provider_id, pcfg in (providers_cfg or {}).items():
        if not isinstance(pcfg, dict):
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


MODEL_REGISTRY: dict[str, Model] = _load()

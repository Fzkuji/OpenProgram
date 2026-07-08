"""Provider-level api/base_url from providers/<p>/provider.json — no MODEL_REGISTRY,
no webui import. Breaks the providers<->webui circular dep for the derivation
helpers (_default_api_for / _resolve_base_url)."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).parent


def _provider_dir(provider_id: str) -> Path | None:
    # Same hyphen->underscore resolution as provider_models._provider_dir, but
    # WITHOUT importing webui (that would re-create the cycle this module breaks).
    # Read-only: never creates a dir.
    for name in (provider_id, provider_id.replace("-", "_")):
        d = _ROOT / name
        if (d / "provider.json").is_file():
            return d
    return None


def _endpoints(provider_id: str) -> dict:
    d = _provider_dir(provider_id)
    if d is None:
        return {}
    try:
        return (json.loads((d / "provider.json").read_text(encoding="utf-8")).get("endpoints") or {})
    except (OSError, json.JSONDecodeError):
        return {}


def provider_endpoints(provider_id: str) -> dict:
    """Full endpoint map {name: {api, base_url}} from provider.json, or {}."""
    return _endpoints(provider_id)


def provider_apis(provider_id: str) -> set[str]:
    return {e.get("api") for e in _endpoints(provider_id).values() if e.get("api")}


def provider_base_url(provider_id: str) -> str | None:
    eps = _endpoints(provider_id)
    ep = eps.get("default") or (next(iter(eps.values())) if eps else None)
    return ep.get("base_url") if ep else None

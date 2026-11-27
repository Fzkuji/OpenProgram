"""models.dev enrichment for the live browse path.

Browse is live-only now: the provider's official /v1/models list is the
authoritative "which models + context", and models.dev fills the price /
capability fields the official API doesn't return. This module exposes the
models.dev lookup (``_models_dev_for``) plus the shared hyphen/underscore
provider-directory resolver. No per-provider fetch cache is persisted.
"""
from __future__ import annotations

from typing import Any

def _models_dev_for(provider_id: str) -> dict[str, dict[str, Any]]:
    """models.dev models for a provider (id -> normalised row). Empty on
    failure/offline."""
    from openprogram.models.auth.provider_policy import UNSUPPORTED_PROVIDERS
    if provider_id in UNSUPPORTED_PROVIDERS:
        return {}
    from openprogram.models.providers.catalog.sources import models_dev
    try:
        return models_dev.list_models(provider_id) or {}
    except Exception:
        return {}

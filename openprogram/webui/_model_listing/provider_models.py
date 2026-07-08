"""models.dev enrichment for the live browse path.

Browse is live-only now: the provider's official /v1/models list is the
authoritative "which models + context", and models.dev fills the price /
capability fields the official API doesn't return. This module exposes the
models.dev lookup (``_models_dev_for``) plus the shared hyphen/underscore
provider-directory resolver. No per-provider fetch cache is persisted.
"""
from __future__ import annotations

from typing import Any

# claude-code / openai-codex etc. have no models.dev entry — borrow the
# standard sibling's models.dev data (same models, different wire/billing).
_SUBSCRIPTION_BORROW = {
    "claude-code": "anthropic",
    "openai-codex": "openai",
    "gemini-subscription": "google",
}


def _models_dev_for(provider_id: str) -> dict[str, dict[str, Any]]:
    """models.dev models for a provider (id -> normalised row). Borrows the
    standard sibling for subscription providers. Empty on failure/offline."""
    from .sources import models_dev
    src = _SUBSCRIPTION_BORROW.get(provider_id, provider_id)
    try:
        return models_dev.list_models(src) or {}
    except Exception:
        return {}

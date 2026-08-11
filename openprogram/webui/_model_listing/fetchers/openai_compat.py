"""Generic OpenAI-compatible ``/v1/models`` fetcher.

The shared fallback: used for every provider listed in
``providers.FETCH_MODELS_PROVIDERS`` (and custom providers) that ships no
``providers/<name>/list_models.py`` of its own. Bearer auth + standard
``{data: [{id, ...}]}`` envelope. Belongs to no single provider, so it lives
here in the dispatcher package rather than in a provider directory.
"""
from __future__ import annotations

from typing import Any


def _fetch_openai_compat(provider_id: str, timeout: float) -> Any:
    """OpenAI-compatible /v1/models: GET base + '/models', Bearer auth."""
    from openprogram.security import safe_http
    from openprogram.security.url_policy import normalize_origin
    from openprogram.providers.metadata import env_var_for
    from openprogram.providers.env_api_keys import resolve_api_key_with_auth_store
    from openprogram.providers.storage import _resolve_base_url

    api_key = resolve_api_key_with_auth_store(provider_id)
    env = env_var_for(provider_id)
    if api_key is None and env:
        return {"error": f"No API key for {provider_id} (set {env})"}
    base = _resolve_base_url(provider_id)
    if not base:
        return {"error": f"No base URL resolvable for {provider_id}"}
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with safe_http.configured_safe_client(
            "webui.model_listing.configured", base
        ) as client:
            r = client.get(base + "/models", headers=headers, timeout=timeout)
            if r.status_code >= 400:
                return {"error": f"HTTP {r.status_code} for {normalize_origin(base)}"}
            data = r.json()
    except Exception as e:
        return {"error": f"{type(e).__name__} for {normalize_origin(base)}"}
    items = data.get("data") or data.get("models") or []
    return items if isinstance(items, list) else {"error": "unexpected response shape"}

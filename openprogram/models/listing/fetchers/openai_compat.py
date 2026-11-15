"""Generic OpenAI-compatible ``/v1/models`` fetcher.

The shared fallback: used for every provider listed in
``providers.FETCH_MODELS_PROVIDERS`` (and custom providers) that ships no
``providers/adapters/<name>/list_models.py`` of its own. Bearer auth + standard
``{data: [{id, ...}]}`` envelope. Belongs to no single provider, so it lives
here in the dispatcher package rather than in a provider directory.
"""
from __future__ import annotations

from typing import Any


def _fetch_openai_compat(provider_id: str, timeout: float) -> Any:
    """OpenAI-compatible /v1/models: GET base + '/models', Bearer auth."""
    from openprogram.host.security import safe_http
    from openprogram.host.security.url_policy import OwnerURLException, normalize_origin
    from openprogram.models.providers.catalog.metadata import env_var_for
    from openprogram.models.providers.settings.env_api_keys import resolve_api_key_with_auth_store
    from openprogram.models.providers.settings.storage import _resolve_base_url

    api_key = resolve_api_key_with_auth_store(provider_id)
    env = env_var_for(provider_id)
    from openprogram.models.providers.catalog.local import is_local_provider
    if api_key is None and env and not is_local_provider(provider_id):
        return {"error": f"No API key for {provider_id} (set {env})"}
    base = _resolve_base_url(provider_id)
    if not base:
        return {"error": f"No base URL resolvable for {provider_id}"}
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with safe_http.configured_safe_client(
            "webui.model_listing.configured",
            base,
            owner_exception=OwnerURLException(
                consumer="webui.model_listing.configured",
                origin=normalize_origin(base),
            ),
        ) as client:
            r = client.get(base + "/models", headers=headers, timeout=timeout)
            if not 200 <= r.status_code < 300:
                return {"error": f"HTTP {r.status_code} for {normalize_origin(base)}"}
            data = r.json()
    except ValueError:
        return {"error": f"Non-JSON response from {normalize_origin(base)}/models"}
    except Exception as e:
        return {"error": f"{type(e).__name__} for {normalize_origin(base)}"}
    if not isinstance(data, dict):
        return {"error": "unexpected response shape"}
    if "error" in data:
        return {"error": "Provider returned an error response"}
    items = data.get("data") if "data" in data else data.get("models")
    return items if isinstance(items, list) else {"error": "unexpected response shape"}

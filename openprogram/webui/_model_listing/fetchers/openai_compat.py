"""Generic OpenAI-compatible ``/v1/models`` fetcher.

The shared fallback: used for every provider listed in
``providers._FETCH_MODELS_PROVIDERS`` (and custom providers) that ships no
``providers/<name>/list_models.py`` of its own. Bearer auth + standard
``{data: [{id, ...}]}`` envelope. Belongs to no single provider, so it lives
here in the dispatcher package rather than in a provider directory.
"""
from __future__ import annotations

from typing import Any


def _fetch_openai_compat(provider_id: str, timeout: float) -> Any:
    """OpenAI-compatible /v1/models: GET base + '/models', Bearer auth."""
    import httpx

    from ..providers import _env_var_for
    from ..storage import _resolve_api_key, _resolve_base_url

    api_key = _resolve_api_key(provider_id)
    env = _env_var_for(provider_id)
    if api_key is None and env:
        return {"error": f"No API key for {provider_id} (set {env})"}
    base = _resolve_base_url(provider_id)
    if not base:
        return {"error": f"No base URL resolvable for {provider_id}"}
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        r = httpx.get(base + "/models", headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    items = data.get("data") or data.get("models") or []
    return items if isinstance(items, list) else {"error": "unexpected response shape"}

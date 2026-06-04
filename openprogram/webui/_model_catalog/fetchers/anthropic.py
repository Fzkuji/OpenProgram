"""Anthropic ``/v1/models`` fetcher — ``x-api-key`` header, returns
``{data: [{id, display_name, ...}]}``. Differs from the generic
OpenAI-compatible fetcher in two ways: auth header (``x-api-key``,
not ``Authorization: Bearer``) and the mandatory
``anthropic-version`` header.

Works for any provider that speaks the Anthropic Messages wire format,
not just native Anthropic: native uses ``api.anthropic.com``, everyone
else (minimax, minimax-cn, …) hits ``<their base_url>/v1/models``."""
from __future__ import annotations

from typing import Any


def _fetch_anthropic(provider_id: str, timeout: float) -> Any:
    import httpx

    from ..storage import _resolve_api_key, _resolve_base_url

    api_key = _resolve_api_key(provider_id)
    if not api_key:
        from ..providers import _env_var_for
        env = _env_var_for(provider_id)
        return {"error": f"No API key set ({env})." if env else "No API key set."}
    # Native Anthropic has a fixed host; third-party Anthropic-wire
    # providers carry their own base_url (…/anthropic).
    if provider_id == "anthropic":
        url = "https://api.anthropic.com/v1/models"
    else:
        base = _resolve_base_url(provider_id)
        if not base:
            return {"error": f"No base URL resolvable for {provider_id}."}
        url = base.rstrip("/") + "/v1/models"
    try:
        r = httpx.get(
            url,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    out = []
    for it in (data.get("data") or []):
        mid = it.get("id")
        if not mid:
            continue
        out.append({
            "id": mid,
            "name": it.get("display_name") or mid,
            # Anthropic doesn't expose context_window in /v1/models, but
            # registry has it. Caller's normalization stage backfills.
        })
    return out

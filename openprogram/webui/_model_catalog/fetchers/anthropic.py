"""Anthropic ``/v1/models`` fetcher — ``x-api-key`` header, returns
``{data: [{id, display_name, ...}]}``. Differs from the generic
OpenAI-compatible fetcher in two ways: auth header (``x-api-key``,
not ``Authorization: Bearer``) and the mandatory
``anthropic-version`` header."""
from __future__ import annotations

from typing import Any


def _fetch_anthropic(provider_id: str, timeout: float) -> Any:
    import httpx

    from ..storage import _resolve_api_key

    api_key = _resolve_api_key(provider_id)
    if not api_key:
        return {"error": "No ANTHROPIC_API_KEY set"}
    try:
        r = httpx.get(
            "https://api.anthropic.com/v1/models",
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

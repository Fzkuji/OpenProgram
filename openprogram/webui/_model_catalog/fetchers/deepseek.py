"""DeepSeek (``api.deepseek.com``) fetcher.

DeepSeek's ``/v1/models`` is OpenAI-compatible but returns
``{id, owned_by}``-only rows — no context window, no display name, no
pricing, no reasoning hint. The generic OpenAI-compatible fetcher
maps that directly into empty rows, so a user asks "什么都没有吗?"
the moment they click Fetch.

This module just hits the same endpoint for the live id list and
emits ids; metadata (context, pricing, reasoning capability, etc.) is
filled in by the generic ``sources.enrich`` step inside
``fetch_models_remote``, which consults ``models.dev``'s community
catalogue. No hand-curated per-model table here — when DeepSeek
launches a new family the rows show up the moment models.dev picks
them up (or as soon as the user manually edits the row).

Kept as a dedicated fetcher rather than relying on the generic
OpenAI-compatible one only because of the Bearer + ``api.deepseek.com``
hostname + the empty-list guard at the bottom.
"""
from __future__ import annotations

from typing import Any


def _fetch_deepseek(provider_id: str, timeout: float) -> Any:
    import httpx
    from openprogram.providers.env_api_keys import get_env_api_key

    from ..storage import _resolve_api_key, _resolve_base_url

    api_key = _resolve_api_key(provider_id) or get_env_api_key(provider_id)
    if not api_key:
        return {"error": "No API key set (DEEPSEEK_API_KEY)"}

    base = (_resolve_base_url(provider_id) or "https://api.deepseek.com/v1").rstrip("/")
    try:
        r = httpx.get(
            base + "/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    out: list[dict[str, Any]] = []
    for raw in (data.get("data") or data.get("models") or []):
        if isinstance(raw, str):
            mid = raw
        elif isinstance(raw, dict):
            mid = raw.get("id") or raw.get("name") or ""
        else:
            continue
        mid = (mid or "").strip()
        if mid:
            out.append({"id": mid})
    if not out:
        return {"error": "DeepSeek returned an empty model list"}
    return out

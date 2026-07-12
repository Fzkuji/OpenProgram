"""DeepSeek model list — ``/v1/models`` (id-only rows, enriched later).

Convention module (see ``openai_codex/list_models.py`` for the pattern): the
dispatcher loads ``fetch(provider_id, timeout)`` by directory name.

DeepSeek's ``/v1/models`` is OpenAI-compatible but returns ``{id, owned_by}``-
only rows — no context window, no display name, no pricing, no reasoning hint.
We just emit the live id list; metadata (context, pricing, reasoning) is filled
in by the generic ``sources.enrich`` step in the dispatcher, from models.dev.
No hand-curated per-model table — a new DeepSeek family shows up the moment
models.dev picks it up (or the user edits the row). Kept as a dedicated module
rather than the generic OpenAI-compatible fetcher only for the Bearer +
``api.deepseek.com`` host + the empty-list guard.

Contract: success → ``list[dict]``, failure → ``{"error": ...}``.
"""
from __future__ import annotations

from typing import Any


def fetch(provider_id: str, timeout: float) -> Any:
    import httpx
    from openprogram.providers.env_api_keys import resolve_provider_key
    from openprogram.webui._model_listing.storage import (
        _resolve_api_key,
        _resolve_base_url,
    )

    api_key = _resolve_api_key(provider_id) or resolve_provider_key(provider_id)
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

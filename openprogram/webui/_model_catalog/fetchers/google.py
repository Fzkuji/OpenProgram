"""Google AI Studio (``generativelanguage.googleapis.com``) ``/v1beta/models``
fetcher. API-key query param style (not Bearer); response shape uses
``name = "models/<id>"`` rather than the bare id, and ``displayName``
+ ``inputTokenLimit`` for the friendly fields. We filter to chat-style
models (``generateContent`` in ``supportedGenerationMethods``) so
embeddings and AQA-only entries don't pollute the picker."""
from __future__ import annotations

from typing import Any


def _fetch_google(provider_id: str, timeout: float) -> Any:
    import httpx

    from ..storage import _resolve_api_key

    api_key = _resolve_api_key(provider_id)
    if not api_key:
        return {"error": "No GOOGLE_GENERATIVE_AI_API_KEY set"}
    try:
        r = httpx.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    out = []
    for it in (data.get("models") or []):
        # name field is "models/gemini-2.5-flash" — strip prefix.
        raw = it.get("name") or ""
        mid = raw.split("/", 1)[1] if "/" in raw else raw
        if not mid:
            continue
        methods = it.get("supportedGenerationMethods") or []
        # Filter to chat-style models; skip embeddings, AQA, etc.
        if methods and "generateContent" not in methods:
            continue
        entry = {
            "id": mid,
            "name": it.get("displayName") or mid,
        }
        ctx = it.get("inputTokenLimit")
        if ctx:
            entry["context_window"] = int(ctx)
        out.append(entry)
    return out

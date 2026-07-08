"""GitHub Copilot ``/v1/models`` fetcher.

Needs the per-session bearer token from the github-copilot provider's
token cache. Live fetch only works if a chat session has been opened
recently (token in cache). The capabilities envelope is richer than
the OpenAI shape — we surface ``vision`` support and
``max_context_window_tokens`` when present."""
from __future__ import annotations

from typing import Any


def _fetch_github_copilot(provider_id: str, timeout: float) -> Any:
    import httpx
    try:
        from openprogram.providers.github_copilot.token_cache import (
            get_cached_token,
        )
        token = get_cached_token()
    except Exception:
        token = None
    if not token:
        return {"error": (
            "Copilot needs a live session token. Open a chat with any "
            "Copilot model once so the token cache populates, then retry."
        )}
    try:
        r = httpx.get(
            "https://api.githubcopilot.com/models",
            headers={
                "Authorization": f"Bearer {token}",
                "Copilot-Integration-Id": "vscode-chat",
            },
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
        caps = it.get("capabilities", {}) or {}
        entry = {
            "id": mid,
            "name": it.get("name") or mid,
        }
        if caps.get("supports", {}).get("vision"):
            entry["vision"] = True
        ctx = caps.get("limits", {}).get("max_context_window_tokens")
        if ctx:
            entry["context_window"] = int(ctx)
        out.append(entry)
    return out

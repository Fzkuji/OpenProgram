"""GitHub Copilot model list — ``/v1/models`` with a session bearer.

Convention module (see ``openai_codex/list_models.py`` for the pattern): the
dispatcher loads ``fetch(provider_id, timeout)`` by directory name.

Needs the per-session bearer token from this provider's token cache, so a live
fetch only works once a chat session has populated it. The capabilities
envelope is richer than the OpenAI shape — we surface ``vision`` support and
``max_context_window_tokens`` when present.

Contract: success → ``list[dict]``, failure → ``{"error": ...}``.
"""
from __future__ import annotations

from typing import Any


def fetch(provider_id: str, timeout: float) -> Any:
    import httpx
    from openprogram.models.auth.resolver import resolve_api_key_sync
    github_token = resolve_api_key_sync("github-copilot")
    if not github_token:
        return {"error": (
            "Copilot is not signed in. Run `openprogram providers keys "
            "github-copilot add`, then fetch again."
        )}
    try:
        from .token_cache import get_copilot_api_token
        token = get_copilot_api_token(github_token)
    except Exception as e:
        return {"error": f"Copilot did not issue a session token: {e}"}
    try:
        from openprogram.host.security.safe_http import safe_client

        with safe_client("provider.fixed_api") as client:
            r = client.get(
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
        return {"error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"error": type(e).__name__}
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

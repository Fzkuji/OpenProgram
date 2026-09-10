"""Grok subscription model list — CLI chat proxy ``/v1/models``.

Convention module (see ``openai_codex/list_models.py``): the dispatcher
loads ``fetch(provider_id, timeout)`` by directory name. Shipping this
file is also what turns on the Settings Fetch button
(``supports_fetch`` via ``_load_fetcher``).

A SuperGrok / X Premium+ bearer is not an ``XAI_API_KEY``. The public
``api.x.ai/v1/models`` probe rejects it; the official CLI lists against
``cli-chat-proxy.grok.com`` with first-party identity headers.

Contract: success → ``list[dict]`` (each row has at least id), failure →
``{"error": ...}``.
"""
from __future__ import annotations

from typing import Any

from .headers import CLI_CHAT_PROXY_BASE_URL, grok_cli_headers


_KNOWN_MODEL_METADATA: dict[str, dict[str, Any]] = {
    "grok-4.6": {
        "name": "Grok 4.6",
        "input": ["text", "image"],
        "context_window": 500_000,
        "max_tokens": 500_000,
        "reasoning": True,
        "thinking_levels": ["low", "medium", "high", "xhigh"],
        "default_thinking_level": "high",
    },
    "grok-4.5": {
        "name": "Grok 4.5",
        "input": ["text", "image"],
        "context_window": 500_000,
        "max_tokens": 500_000,
        "reasoning": True,
    },
}


def _token(provider_id: str) -> str:
    from openprogram.providers.env_api_keys import resolve_api_key_with_auth_store

    tok = resolve_api_key_with_auth_store(provider_id)
    if tok:
        return tok
    try:
        from openprogram.auth.credential_provider import get_credential_provider

        cred = get_credential_provider().acquire_sync(provider_id)
        payload = getattr(cred, "payload", None)
        return getattr(payload, "auth_value", None) or ""
    except Exception:
        return ""


def fetch(provider_id: str, timeout: float) -> Any:
    import httpx
    from openprogram.security.safe_http import safe_client

    token = _token(provider_id)
    if not token:
        return {"error": (
            "not signed in to Grok Subscription — use Sign in with Grok "
            "or Import from ~/.grok/auth.json, then Fetch again."
        )}

    url = CLI_CHAT_PROXY_BASE_URL.rstrip("/") + "/models"
    headers = {
        "Authorization": f"Bearer {token}",
        **grok_cli_headers("grok-4.5"),
    }
    try:
        with safe_client("provider.fixed_api") as client:
            r = client.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"error": type(e).__name__}

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
            row = {"id": mid, "name": mid}
            row.update(_KNOWN_MODEL_METADATA.get(mid, {}))
            if isinstance(raw, dict):
                context = raw.get("context_window") or raw.get("context_length")
                maximum = raw.get("max_tokens") or raw.get("max_output_tokens")
                if context:
                    row["context_window"] = int(context)
                if maximum:
                    row["max_tokens"] = int(maximum)
            out.append(row)
    if not out:
        return {"error": "Grok subscription returned an empty model list"}
    return out

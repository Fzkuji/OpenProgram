"""Claude Code proxy daemon (Meridian / claude-max-api-proxy) fetcher.

The proxy speaks the OpenAI Completions protocol and exposes the
Claude models available through the user's Claude Code session — no
API key needed; the proxy reuses the OAuth credentials in the Claude
Code keychain. ``CLAUDE_MAX_PROXY_URL`` overrides the default
``http://localhost:3456`` when the user runs the daemon on a non-
standard port."""
from __future__ import annotations

import os
from typing import Any


def _fetch_claude_code(provider_id: str, timeout: float) -> Any:
    import httpx
    base = (
        os.environ.get("CLAUDE_MAX_PROXY_URL") or "http://localhost:3456"
    ).rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    try:
        r = httpx.get(base + "/v1/models", timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": (
            f"Proxy not reachable at {base}. Is `claude-max-api` running? ({e})"
        )}
    items = data.get("data") or data.get("models") or []
    return items if isinstance(items, list) else {"error": "unexpected response shape"}

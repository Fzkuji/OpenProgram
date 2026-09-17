"""Headers the Grok CLI chat proxy requires for a SuperGrok / X Premium+ token.

``api.x.ai`` is the developer (API-key) surface and rejects a subscription
OAuth bearer as "Incorrect API key". The official CLI sends the same
bearer to ``cli-chat-proxy.grok.com`` with first-party identity headers so
the request is billed as Grok Build, not grok.com Chat.
"""
from __future__ import annotations

import json
from pathlib import Path

CLI_CHAT_PROXY_BASE_URL = "https://cli-chat-proxy.grok.com/v1"

_FALLBACK_CLI_VERSION = "1.0.30"


def grok_cli_version() -> str:
    path = Path.home() / ".grok" / "version.json"
    try:
        version = str(
            json.loads(path.read_text(encoding="utf-8")).get("version") or ""
        ).strip()
    except (OSError, TypeError, ValueError):
        version = ""
    return version or _FALLBACK_CLI_VERSION


def grok_build_route(model_id: str) -> str:
    """The proxy routes by this header, not the JSON ``model`` field."""
    mid = (model_id or "").strip().lower()
    if mid in {"grok-build", "grok-4.6", "grok-4.6-build", "grok-4.5", "grok-4.5-build"}:
        return "grok-build"
    return mid or "grok-build"


def grok_cli_headers(model_id: str) -> dict[str, str]:
    version = grok_cli_version()
    return {
        "X-XAI-Token-Auth": "xai-grok-cli",
        "x-grok-client-identifier": "grok-shell",
        "x-grok-client-version": version,
        "User-Agent": f"xai-grok-cli/{version}",
        "x-grok-model-override": grok_build_route(model_id),
    }

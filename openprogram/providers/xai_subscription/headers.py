"""Headers the Grok CLI chat proxy requires for a SuperGrok / X Premium+ token.

``api.x.ai`` is the developer (API-key) surface and rejects a subscription
OAuth bearer as "Incorrect API key". The official CLI sends the same
bearer to ``cli-chat-proxy.grok.com`` with first-party identity headers.
"""
from __future__ import annotations

CLI_CHAT_PROXY_BASE_URL = "https://cli-chat-proxy.grok.com/v1"

# Recent public Grok Build CLI version (x.ai/build/changelog, Aug 2026).
# The proxy 426s callers that omit a version or send a stale one.
_CLI_VERSION = "1.0.5"


def grok_cli_headers(model_id: str) -> dict[str, str]:
    return {
        "X-XAI-Token-Auth": "xai-grok-cli",
        "x-grok-client-identifier": "grok-shell",
        "x-grok-client-version": _CLI_VERSION,
        "User-Agent": f"xai-grok-cli/{_CLI_VERSION}",
        "x-grok-model-override": model_id,
    }

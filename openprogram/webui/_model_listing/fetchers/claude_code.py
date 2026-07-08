"""Claude Code direct anthropic subscription fetcher.

claude-code runs direct on the anthropic subscription — its model list IS
anthropic's Claude catalog. No Meridian daemon or proxy is queried; models
are pulled from the already-registered static ENABLED_MODELS registry."""
from __future__ import annotations

from typing import Any


def _fetch_claude_code(provider_id: str, timeout: float) -> Any:
    # claude-code runs DIRECT on the anthropic subscription — its model list
    # IS anthropic's Claude catalog (no Meridian daemon to query).
    from openprogram.providers.enabled_models import ENABLED_MODELS
    items = []
    for m in ENABLED_MODELS.values():
        if m.provider == "anthropic" and m.id.startswith("claude"):
            items.append({"id": m.id})
    return items

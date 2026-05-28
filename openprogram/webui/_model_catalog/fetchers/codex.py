"""OpenAI Codex (``openai-codex``) static fetcher.

The ChatGPT backend has no public list-models API — ``/models`` 403s
behind Cloudflare. As a substitute the "Fetch models" button re-
emits the curated catalogue we ship in ``providers/models_generated.py``,
filtered to the Codex provider. That way users who pulled a new
OpenProgram release see fresh model rows after one click, without
having to delete-and-recreate the provider."""
from __future__ import annotations

from typing import Any


def _fetch_codex_static(provider_id: str, timeout: float) -> Any:
    from openprogram.providers.models_generated import MODELS
    out = []
    for v in MODELS.values():
        if v.provider != "openai-codex":
            continue
        out.append({
            "id": v.id,
            "name": v.name,
            "context_window": v.context_window,
            "vision": "image" in (v.input or []),
            "reasoning": bool(v.reasoning),
        })
    if not out:
        return {"error": "No Codex models in registry"}
    return out

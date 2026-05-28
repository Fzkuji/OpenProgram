"""``models.dev`` enricher.

Pulls the public JSON catalogue at https://models.dev/api.json (the
same data OpenCode and several other AI-tooling projects use) and
normalises it into the schema described in
``_model_catalog.sources.__init__``.

Single-process in-memory cache with a 1-hour TTL — the catalogue
itself only updates daily, and we don't want every Fetch click to add
a ~300KB GET on top of the per-provider ``/v1/models`` call.
"""
from __future__ import annotations

import threading
import time
from typing import Any


_CATALOGUE_URL = "https://models.dev/api.json"
_TTL_SECONDS = 3600  # 1 hour

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"data": None, "fetched_at": 0.0}


def _load() -> dict[str, Any]:
    """Return the parsed catalogue, fetching it (or refreshing on TTL
    expiry) if needed. Failures cache an empty dict for the same TTL
    so a transient network blip doesn't wedge a re-fetch on every call.
    """
    with _cache_lock:
        if (
            _cache["data"] is not None
            and time.time() - _cache["fetched_at"] < _TTL_SECONDS
        ):
            return _cache["data"]
        import httpx
        try:
            r = httpx.get(_CATALOGUE_URL, timeout=10)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        _cache["data"] = data
        _cache["fetched_at"] = time.time()
        return data


def _normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """Map the models.dev row shape onto our internal schema."""
    out: dict[str, Any] = {}
    name = raw.get("name")
    if name:
        out["name"] = name
    limit = raw.get("limit") or {}
    if limit.get("context"):
        try: out["context_window"] = int(limit["context"])
        except Exception: pass
    if limit.get("output"):
        try: out["max_tokens"] = int(limit["output"])
        except Exception: pass
    if raw.get("reasoning") is not None:
        out["reasoning"] = bool(raw["reasoning"])
    modalities = (raw.get("modalities") or {}).get("input") or []
    if "image" in modalities:
        out["vision"] = True
    if raw.get("tool_call") is not None:
        out["tools"] = bool(raw["tool_call"])
    cost = raw.get("cost") or {}
    for src_key, dst_key in (
        ("input", "input_cost"),
        ("output", "output_cost"),
        ("cache_read", "cache_read_cost"),
    ):
        if cost.get(src_key) is not None:
            try: out[dst_key] = float(cost[src_key])
            except Exception: pass
    return out


def lookup(provider_id: str, model_id: str) -> dict[str, Any] | None:
    """Resolve ``(provider_id, model_id)`` in the cached catalogue.
    Returns the normalised metadata dict, or ``None`` when not
    present.

    models.dev uses the same lowercase short provider ids we do for
    most cases (``deepseek``, ``openai``, ``anthropic``, ``groq``,
    ``cerebras``, ``openrouter``, …); ``_PROVIDER_ID_ALIASES`` covers
    the few that differ.
    """
    catalogue = _load()
    if not catalogue:
        return None
    pid = _PROVIDER_ID_ALIASES.get(provider_id, provider_id)
    provider = catalogue.get(pid)
    if not isinstance(provider, dict):
        return None
    models = provider.get("models")
    if not isinstance(models, dict):
        return None
    raw = models.get(model_id)
    if not isinstance(raw, dict):
        return None
    return _normalise(raw) or None


# Provider id mapping for the few cases where our id differs from the
# models.dev key. Empty for now — DeepSeek / OpenAI / Anthropic /
# Groq / Cerebras / OpenRouter / Mistral / HuggingFace all match
# verbatim. Add entries here if a future provider does need
# translation.
_PROVIDER_ID_ALIASES: dict[str, str] = {
    # Our id            : models.dev id
    "openai-codex": "openai",        # models.dev tracks one OpenAI catalogue
    "claude-code":  "anthropic",     # Meridian proxy serves Anthropic models
    "gemini-subscription": "google",  # CodeAssist surfaces the same Gemini set
}

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
    """Map the models.dev row shape onto our internal schema.

    Captures every field the catalogue exposes that the UI can do
    something useful with — capabilities (tool_call, reasoning,
    structured_output, attachment), full modality lists, all limit
    components (context / input cap / output cap), full pricing
    surface (input / output / cache_read / cache_write), and the
    metadata block (family, knowledge cutoff, release / update
    dates, open-weights flag).

    Anything missing from the upstream row is just omitted from the
    output — the React side checks for ``!= undefined`` and hides the
    corresponding line in the expanded panel.
    """
    out: dict[str, Any] = {}
    # ── identity / metadata ───────────────────────────────────────
    for src_key, dst_key in (
        ("name", "name"),
        ("family", "family"),
        ("knowledge", "knowledge_cutoff"),
        ("release_date", "release_date"),
        ("last_updated", "last_updated"),
    ):
        v = raw.get(src_key)
        if v is not None and v != "":
            out[dst_key] = v
    if raw.get("open_weights") is not None:
        out["open_weights"] = bool(raw["open_weights"])

    # ── capabilities ──────────────────────────────────────────────
    if raw.get("reasoning") is not None:
        out["reasoning"] = bool(raw["reasoning"])
    if raw.get("tool_call") is not None:
        out["tools"] = bool(raw["tool_call"])
    if raw.get("structured_output") is not None:
        out["structured_output"] = bool(raw["structured_output"])
    if raw.get("attachment") is not None:
        out["attachment"] = bool(raw["attachment"])
    if raw.get("temperature") is not None:
        out["temperature_param"] = bool(raw["temperature"])

    # ── modalities ─────────────────────────────────────────────────
    modalities = raw.get("modalities") or {}
    in_mods = modalities.get("input") or []
    out_mods = modalities.get("output") or []
    if in_mods:
        out["input_modalities"] = list(in_mods)
    if out_mods:
        out["output_modalities"] = list(out_mods)
    # Legacy flat booleans the existing UI consumes for badge rendering.
    if "image" in in_mods:
        out["vision"] = True
    if "video" in in_mods:
        out["video"] = True
    if "audio" in in_mods:
        out["audio"] = True

    # ── limits ─────────────────────────────────────────────────────
    limit = raw.get("limit") or {}
    if limit.get("context"):
        try: out["context_window"] = int(limit["context"])
        except Exception: pass
    if limit.get("input"):
        try: out["input_limit"] = int(limit["input"])
        except Exception: pass
    if limit.get("output"):
        try: out["max_tokens"] = int(limit["output"])
        except Exception: pass

    # ── pricing (USD / 1M tokens) ──────────────────────────────────
    cost = raw.get("cost") or {}
    for src_key, dst_key in (
        ("input", "input_cost"),
        ("output", "output_cost"),
        ("cache_read", "cache_read_cost"),
        ("cache_write", "cache_write_cost"),
    ):
        if cost.get(src_key) is not None:
            try: out[dst_key] = float(cost[src_key])
            except Exception: pass
    # Tiered pricing (e.g. OpenAI's >200K context surcharge). Pass
    # through verbatim — the UI just renders it as JSON in the
    # expanded panel for now.
    if cost.get("tiers"):
        out["cost_tiers"] = cost["tiers"]
    if cost.get("context_over_200k"):
        out["cost_context_over_200k"] = cost["context_over_200k"]

    # Speed / priority modes (``experimental.modes`` — e.g. OpenAI's
    # "fast" = service_tier:priority). Normalised into a small list the
    # composer's speed pill consumes: ``[{id, service_tier, cost}]``.
    # When a model has none, the pill simply doesn't render for it.
    modes = (raw.get("experimental") or {}).get("modes") or {}
    speed_modes: list[dict[str, Any]] = []
    if isinstance(modes, dict):
        for mode_id, spec in modes.items():
            if not isinstance(spec, dict):
                continue
            body = ((spec.get("provider") or {}).get("body") or {})
            tier = body.get("service_tier")
            entry: dict[str, Any] = {"id": mode_id}
            if tier:
                entry["service_tier"] = tier
            if isinstance(spec.get("cost"), dict):
                entry["cost"] = spec["cost"]
            speed_modes.append(entry)
    if speed_modes:
        out["speed_modes"] = speed_modes
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


def list_models(provider_id: str) -> dict[str, dict[str, Any]]:
    """Every model the catalogue knows for ``provider_id`` (alias-aware),
    as ``{model_id: normalised_dict}``.

    Honours the same ``_PROVIDER_ID_ALIASES`` map as :func:`lookup`, so e.g.
    ``openai-codex`` resolves to the upstream ``openai`` catalogue. Returns
    ``{}`` on a cache miss / unknown provider. This is the live source a
    no-list-endpoint provider (Codex) can fetch from instead of shipping a
    hand-maintained list."""
    catalogue = _load()
    if not catalogue:
        return {}
    pid = _PROVIDER_ID_ALIASES.get(provider_id, provider_id)
    provider = catalogue.get(pid)
    if not isinstance(provider, dict):
        return {}
    models = provider.get("models")
    if not isinstance(models, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for mid, raw in models.items():
        if isinstance(raw, dict):
            out[mid] = _normalise(raw)
    return out


# ---------------------------------------------------------------------------
# Provider-level (catalog-wide) accessors
# ---------------------------------------------------------------------------

def _normalise_provider(pid: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Pick the fields we care about out of a models.dev provider row.

    Shape returned matches what ``providers.py`` consumes:

      * ``label`` — display name (str)
      * ``env_var`` — primary env var holding the API key (str | None)
      * ``base_url`` — default API base URL (str | None)
      * ``doc_url`` — link to provider docs (str | None)
      * ``npm`` — vendor SDK on npm (str | None) — informational
      * ``model_ids`` — full id list of models in the catalogue (list)
    """
    env = raw.get("env")
    env_var: str | None = None
    if isinstance(env, list) and env:
        env_var = str(env[0]) if env[0] else None
    elif isinstance(env, str) and env:
        env_var = env

    models = raw.get("models") or {}
    model_ids = list(models.keys()) if isinstance(models, dict) else []

    return {
        "id": pid,
        "label": raw.get("name") or pid,
        "env_var": env_var,
        "base_url": raw.get("api") or None,
        "doc_url": raw.get("doc") or None,
        "npm": raw.get("npm") or None,
        "model_ids": model_ids,
    }


def provider_info(provider_id: str) -> dict[str, Any] | None:
    """Look up provider-level metadata. Honours the same id alias map
    as ``lookup()``, so e.g. ``openai-codex`` falls back to the
    ``openai`` row when no Codex-specific entry exists in the
    catalogue."""
    catalogue = _load()
    if not catalogue:
        return None
    # First try the verbatim id (a few providers like ``openai-codex``
    # genuinely have a distinct entry).
    raw = catalogue.get(provider_id)
    if not isinstance(raw, dict):
        # Fall back to alias mapping for "shares the same upstream
        # catalogue" cases.
        aliased = _PROVIDER_ID_ALIASES.get(provider_id)
        if aliased:
            raw = catalogue.get(aliased)
    if not isinstance(raw, dict):
        return None
    return _normalise_provider(provider_id, raw)


def list_providers() -> list[dict[str, Any]]:
    """Every provider in the cached catalogue, normalised. Used by the
    listing layer to surface community-known providers we haven't yet
    hard-coded in ``providers._PROVIDER_LABELS`` etc."""
    catalogue = _load()
    if not catalogue:
        return []
    return [
        _normalise_provider(pid, raw)
        for pid, raw in catalogue.items()
        if isinstance(raw, dict) and isinstance(raw.get("models"), dict)
    ]


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

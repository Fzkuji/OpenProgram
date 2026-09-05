"""``models.dev`` enricher.

Pulls the public JSON catalogue at https://models.dev/api.json (the
same data OpenCode and several other AI-tooling projects use) and
normalises it into the schema described in ``sources.__init__``.

Single-process stale-while-revalidate cache with a 1-hour fresh TTL.
Expired memory or disk data is served immediately while one background
thread refreshes it; only a first start with no cache waits for the network.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from openprogram.security import safe_http


_CATALOGUE_URL = "https://models.dev/api.json"
_TTL_SECONDS = 3600  # 1 hour on success
_FAIL_TTL_SECONDS = 60  # short retry window on failure/empty

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {
    "data": None,
    "fetched_at": 0.0,
    "last_attempt_at": 0.0,
    "refreshing": False,
}
_logger = logging.getLogger(__name__)


def _fetch_catalogue(timeout: float) -> dict[str, Any]:
    try:
        with safe_http.safe_client("webui.model_listing.fixed") as client:
            response = client.get(_CATALOGUE_URL, timeout=timeout)
            response.raise_for_status()
            safe_http.require_json_mime(response)
            data = response.json()
        if not isinstance(data, dict):
            return {}
        if data:
            _write_disk_cache(data)
        return data
    except Exception:
        return {}


def _finish_refresh(data: dict[str, Any]) -> None:
    now = time.time()
    with _cache_lock:
        if data:
            _cache.update(
                data=data,
                fetched_at=now,
                last_attempt_at=now,
                refreshing=False,
            )
        else:
            # Preserve stale success data. With no cache, remember the empty
            # failure briefly so a cold-start burst does not retry immediately.
            if not _cache["data"]:
                _cache.update(data={}, fetched_at=now)
            _cache.update(last_attempt_at=now, refreshing=False)


def _refresh_cache() -> None:
    _finish_refresh(_fetch_catalogue(timeout=3))


def _start_background_refresh() -> None:
    try:
        threading.Thread(
            target=_refresh_cache,
            name="models-dev-refresh",
            daemon=True,
        ).start()
    except Exception:
        with _cache_lock:
            _cache.update(last_attempt_at=time.time(), refreshing=False)


def _load() -> dict[str, Any]:
    """Return fresh catalogue data, or stale data while it revalidates."""
    now = time.time()
    start_refresh = False
    with _cache_lock:
        data = _cache["data"]
        if data and now - _cache["fetched_at"] < _TTL_SECONDS:
            return data
        if data:
            if (
                not _cache["refreshing"]
                and now - _cache["last_attempt_at"] >= _FAIL_TTL_SECONDS
            ):
                _cache.update(refreshing=True, last_attempt_at=now)
                start_refresh = True
            stale = data
        else:
            stale = None
            failed_recently = (
                data is not None
                and now - _cache["fetched_at"] < _FAIL_TTL_SECONDS
            )

    if stale is not None:
        if start_refresh:
            _start_background_refresh()
        return stale

    # Disk data is usable stale data regardless of its age. Reading it before
    # the cold network path is what keeps session hydration non-blocking.
    disk_data = _read_disk_cache()
    if disk_data:
        with _cache_lock:
            current = _cache["data"]
            if current:
                return current
            _cache.update(data=disk_data, fetched_at=0.0)
            if not _cache["refreshing"]:
                _cache.update(refreshing=True, last_attempt_at=now)
                start_refresh = True
        if start_refresh:
            _start_background_refresh()
        return disk_data

    if failed_recently:
        return {}

    # A first start with no memory or disk cache is the only synchronous
    # network path. Coordinate concurrent callers so only one request starts.
    with _cache_lock:
        data = _cache["data"]
        if data:
            return data
        if data is not None and time.time() - _cache["fetched_at"] < _FAIL_TTL_SECONDS:
            return data
        if _cache["refreshing"]:
            return {}
        _cache.update(refreshing=True, last_attempt_at=time.time())

    data = _fetch_catalogue(timeout=3)
    _finish_refresh(data)
    return data


def _disk_cache_path():
    from openprogram.paths import get_state_dir

    d = get_state_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "cache" / "models_dev.json"


def _write_disk_cache(data: dict[str, Any]) -> None:
    import json

    try:
        path = _disk_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _read_disk_cache() -> dict[str, Any]:
    import json

    path = None
    try:
        path = _disk_cache_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("models.dev cache must be an object")
        return data
    except FileNotFoundError:
        return {}
    except (OSError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        _logger.warning(
            "provider metadata load failed",
            extra={
                "source": "models_dev_cache",
                "path": str(path) if path is not None else "<unresolved>",
                "error_type": type(exc).__name__,
            },
        )
        return {}


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
    # identity / metadata
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

    # capabilities
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

    # modalities
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

    # limits
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

    # pricing (USD / 1M tokens)
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


def conservative_limits(model_id: str) -> dict[str, int] | None:
    """Return conservative token limits for an exact model id.

    Custom OpenAI-compatible gateways often expose only ``id`` from their
    ``/models`` endpoint.  When that id also exists in models.dev, use the
    smallest positive limits advertised by any provider carrying the exact
    id.  Unknown ids remain unknown so governed execution still fails closed.
    """
    catalogue = _load()
    contexts: list[int] = []
    outputs: list[int] = []
    for provider in catalogue.values():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        raw = models.get(model_id) if isinstance(models, dict) else None
        if not isinstance(raw, dict):
            continue
        row = _normalise(raw)
        context = row.get("context_window")
        output = row.get("max_tokens")
        if isinstance(context, int) and context > 0:
            contexts.append(context)
        if isinstance(output, int) and output > 0:
            outputs.append(output)
    if not contexts:
        return None
    result = {"context_window": min(contexts)}
    if outputs:
        result["max_tokens"] = min(outputs)
    return result


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

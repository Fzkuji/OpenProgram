"""Catalog listing — turn provider + model registry + config state into
the JSON the UI consumes.

Three public listing functions:

* ``list_providers`` — left-hand provider sidebar (LLM Providers
  settings page). Each row carries enable / configured / model-count
  / setup hint state.
* ``list_models_for_provider`` — right-hand model table for a single
  provider. A LIVE query: the provider's official /v1/models list (when
  credentialed) ⊕ models.dev, merged in memory and never persisted, with
  the enabled flag read off the config spec rows. See ``_browse_models``.
* ``list_enabled_models`` — flat picker for the chat composer. Reshapes
  the runtime registry (``ENABLED_MODELS`` = the enabled config spec rows)
  directly — no browse, no network.

Plus the small ``_model_to_dict`` helper that produces the per-model
JSON shape both the per-provider table and the flat picker emit.
"""
from __future__ import annotations

import threading
import time
from typing import Any


# Short-TTL in-memory cache for the live-browse rows, keyed by provider id.
# Opening the LLM-Providers settings page repeatedly (or several models
# expanding their thinking menu) must not hammer each provider's /v1/models.
# force_refresh=True (the Fetch button) bypasses it.
_BROWSE_TTL_SECONDS = 600  # 10 minutes
_browse_lock = threading.Lock()
_browse_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _reset_browse_cache() -> None:
    """Test hook — drop every cached browse result."""
    with _browse_lock:
        _browse_cache.clear()


def _enabled_ids(pcfg: dict[str, Any]) -> set[str]:
    """Ids the user has enabled for a provider.

    Spec rows (``providers.<p>.models``) are the source of truth; the legacy
    ``enabled_models`` id list is a fallback for a not-yet-migrated config.
    """
    spec_ids = {r.get("id") for r in (pcfg.get("models") or []) if r.get("id")}
    return spec_ids or set(pcfg.get("enabled_models") or [])


def _browse_models(provider_id: str, force_refresh: bool = False) -> list[dict[str, Any]]:
    """Live model list for a provider: official-API list (when credentialed)
    ⊕ models.dev rows, merged in memory — NEVER persisted.

    Degradation chain (never raises):
      1. Provider has a credential → hit the official API (``fetch_and_normalize``).
         models.dev fills the price/capability fields the API omits.
      2. No key, or the official API errored → models.dev's full list for
         the provider.
      3. models.dev also unavailable → empty list (caller still layers in
         config manual rows / enabled specs on top).

    Cached for ``_BROWSE_TTL_SECONDS`` per provider unless ``force_refresh``.
    """
    if not force_refresh:
        with _browse_lock:
            hit = _browse_cache.get(provider_id)
            if hit and (time.time() - hit[0]) < _BROWSE_TTL_SECONDS:
                return [dict(r) for r in hit[1]]

    from .provider_models import _models_dev_for
    from .providers import _is_configured
    from .fetchers import fetch_and_normalize

    md = _models_dev_for(provider_id)  # {id: normalised row} — {} on failure

    official: list[dict[str, Any]] = []
    if _is_configured(provider_id):
        try:
            res = fetch_and_normalize(provider_id)
        except Exception:
            res = {"error": "fetch raised"}
        if isinstance(res, dict) and isinstance(res.get("models"), list):
            official = res["models"]

    rows: list[dict[str, Any]]
    if official:
        # Official API is authoritative on WHICH models + context; models.dev
        # fills price / capability fields it doesn't return.
        rows = []
        for m in official:
            mid = m.get("id")
            if not mid:
                continue
            row = dict(md.get(mid, {}))
            row.update({k: v for k, v in m.items() if v is not None})
            row["id"] = mid
            rows.append(row)
    else:
        # No key or official API failed → models.dev's full list (or []).
        rows = [{**row, "id": mid} for mid, row in md.items()]

    with _browse_lock:
        _browse_cache[provider_id] = (time.time(), [dict(r) for r in rows])
    return rows


def _model_to_dict(model: Any, enabled: bool) -> dict[str, Any]:
    inputs = list(getattr(model, "input", []) or [])
    return {
        "id": model.id,
        "name": getattr(model, "name", model.id),
        "api": model.api,
        "context_window": getattr(model, "context_window", 0) or 0,
        "max_tokens": getattr(model, "max_tokens", 0) or 0,
        "vision": "image" in inputs,
        "video": "video" in inputs,
        "audio": "audio" in inputs,
        "reasoning": bool(getattr(model, "reasoning", False)),
        # Thinking UX capability (see providers/thinking_spec.py).
        # Empty `thinking_levels` → UI hides the menu for this model.
        "thinking_levels": list(getattr(model, "thinking_levels", []) or []),
        "default_thinking_level": getattr(model, "default_thinking_level", None),
        "thinking_variant": getattr(model, "thinking_variant", None),
        "tools": True,  # all HTTP providers route tool_calls
        "enabled": enabled,
    }


def list_providers() -> list[dict[str, Any]]:
    """Unified provider list with enable/configure status and model
    counts.

    Two source-of-truth tiers merged:

      1. **Static registry** — providers with at least one ``Model``
         row in ``providers/enabled_models.py``. These have a baked-
         in ``Model.base_url`` and known model ids the runtime can
         dispatch against on day one.

      2. **Community catalogue** (``sources.models_dev``) — every
         provider models.dev knows about, including ones we don't
         have a static-registry entry for. The user can enable +
         configure these too; clicking Fetch will hit the upstream
         API and surface real models. Lets the user reach for e.g.
         ``fireworks`` or ``together`` without us shipping a code
         change first.

    Static-registry entries take precedence on id collisions (so e.g.
    our ``openai-codex`` keeps its OpenProgram-specific routing rather
    than getting overwritten by models.dev's ``openai`` row).
    """
    from openprogram.providers import get_providers, get_models
    from openprogram.auth.login_methods import login_methods as _login_methods

    from .providers import (
        _CLI_PROVIDERS,
        _FETCH_MODELS_PROVIDERS,
        _env_var_for,
        _is_configured,
        _label,
    )
    from .setup_hints import _setup_hint
    from .sources import models_dev
    from .storage import _read_providers_cfg
    from .fetchers import _FETCHERS

    cfg = _read_providers_cfg()
    result: list[dict[str, Any]] = []

    # Tier 1: HTTP providers from the static registry
    seen: set[str] = set()
    for pid in get_providers():
        seen.add(pid)
        pcfg = cfg.get(pid, {})
        models = get_models(pid)
        custom = pcfg.get("custom_models") or []
        enabled_ids = _enabled_ids(pcfg)
        all_ids = {m.id for m in models} | {c.get("id") for c in custom if c.get("id")}
        default_base = models[0].base_url if models and models[0].base_url else ""
        entry = {
            "id": pid,
            "label": _label(pid),
            "kind": "api",
            "enabled": bool(pcfg.get("enabled", False)),
            "configured": _is_configured(pid),
            "api_key_env": _env_var_for(pid),
            "default_base_url": default_base,
            "base_url": pcfg.get("base_url") or "",
            "use_responses_api": bool(pcfg.get("use_responses_api", False)),
            "supports_fetch": (pid in _FETCH_MODELS_PROVIDERS) or (pid in _FETCHERS),
            "model_count": len(models) + len(custom),
            "enabled_model_count": sum(1 for mid in all_ids if mid in enabled_ids),
        }
        hint = _setup_hint(pid)
        if hint:
            entry["setup_hint"] = hint
        # Native login methods (OAuth / device-code / import-from-CLI) the web
        # can drive — excluding plain api_key, which the ApiKey field already
        # handles. Empty for key-only providers, so the UI only renders a
        # "Sign in" panel where there's a real native flow. Single source of
        # truth: openprogram/auth/login_methods.py (import hoisted above).
        native = [
            {"id": mid, "label": label}
            for mid, label in _login_methods(pid)
            if mid != "api_key"
        ]
        if native:
            entry["login_methods"] = native
        result.append(entry)

    # Tier 2: community-catalogue providers we don't have a static
    # entry for. Configurable (paste key + Fetch); ``model_count`` is
    # what models.dev says is available pre-fetch, so the user sees
    # "OpenRouter has 233 models" without enabling anything first.
    for md_prov in models_dev.list_providers():
        pid = md_prov.get("id")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        pcfg = cfg.get(pid, {})
        custom = pcfg.get("custom_models") or []
        enabled_ids = _enabled_ids(pcfg)
        community_ids = set(md_prov.get("model_ids") or [])
        custom_ids = {c.get("id") for c in custom if c.get("id")}
        result.append({
            "id": pid,
            # Route through _label so manual overrides (_PROVIDER_LABELS)
            # win for community-tier providers too — e.g. relabel the
            # MiniMax Token-Plan rows by region word instead of bare
            # domain. Falls back to the models.dev label, then the id.
            "label": _label(pid),
            "kind": "api",
            "enabled": bool(pcfg.get("enabled", False)),
            "configured": _is_configured(pid),
            "api_key_env": _env_var_for(pid),
            "default_base_url": md_prov.get("base_url") or "",
            "base_url": pcfg.get("base_url") or "",
            "use_responses_api": bool(pcfg.get("use_responses_api", False)),
            # Every OpenAI-compatible provider models.dev knows about
            # is fetch-able by default; the dispatcher in
            # ``fetchers.fetch_models_remote`` falls through to
            # ``_fetch_openai_compat`` when there's no explicit
            # ``_FETCHERS`` entry.
            "supports_fetch": True,
            "model_count": len(community_ids | custom_ids),
            "enabled_model_count": sum(
                1 for mid in (community_ids | custom_ids) if mid in enabled_ids
            ),
            "doc_url": md_prov.get("doc_url"),
            "community_source": "models.dev",
        })

    # CLI-backed providers (currently empty, kept for forward-compat)
    for cli in _CLI_PROVIDERS:
        if cli["id"] in seen:
            continue
        result.append({
            "id": cli["id"],
            "label": _label(cli["id"]),
            "kind": "cli",
            "cli_binary": cli.get("cli_binary"),
            "enabled": bool(cfg.get(cli["id"], {}).get("enabled", False)),
            "configured": _is_configured(cli["id"]),
            "api_key_env": None,
            "model_count": 0,
            "enabled_model_count": 0,
        })

    result.sort(key=lambda x: x["label"].lower())
    return result


def list_models_for_provider(
    provider_id: str, force_refresh: bool = False
) -> list[dict[str, Any]]:
    """All models for a provider + their enabled flag — a LIVE query,
    never a persisted snapshot.

    Sources merged in memory (see ``_browse_models``):
      - the provider's official /v1/models list, when it has a credential;
      - models.dev (fallback with no key, + field enrichment);
      - config manual rows (``providers.<p>.models`` with source="manual",
        plus legacy ``custom_models``) not present in the live result.

    The ``enabled`` flag per row comes from the config spec rows
    (``providers.<p>.models`` ids), falling back to the legacy
    ``enabled_models`` id list. ``force_refresh`` bypasses the browse TTL
    cache (the Fetch button).
    """
    from openprogram.providers.thinking_spec import derive_thinking_fields

    from .providers import _default_api_for
    from .storage import _read_providers_cfg

    cfg = _read_providers_cfg()
    pcfg = cfg.get(provider_id, {})
    # Enabled = ids with a stored spec row (new source of truth); fall back to
    # the legacy id whitelist so a not-yet-migrated config still reads right.
    enabled_ids = _enabled_ids(pcfg)
    default_api = _default_api_for(provider_id) or "openai-completions"

    out: list[dict[str, Any]] = []
    all_rows = _browse_models(provider_id, force_refresh=force_refresh)
    present = {r.get("id") for r in all_rows}
    # Manual rows the user typed by hand live only in config, never in the
    # live browse result — layer them in. Both the new spec rows tagged
    # source="manual" and the legacy custom_models key.
    manual_sources = [
        r for r in (pcfg.get("models") or []) if r.get("source") == "manual"
    ] + list(pcfg.get("custom_models") or [])
    for cm in manual_sources:
        cmid = cm.get("id") or ""
        if cmid and cmid not in present:
            all_rows = all_rows + [cm]
            present.add(cmid)
    for raw in all_rows:
        mid = raw.get("id") or ""
        if not mid:
            continue
        reasoning = bool(raw.get("reasoning", False))
        # Priority: thinking.json model_overrides (freshest, from probe)
        # > Fetch data thinking_levels > thinking.json provider level
        # > catalog fallback.
        levels, default_lv, variant = derive_thinking_fields(
            provider_id, mid, reasoning, bool(raw.get("supports_xhigh", False))
        )
        if not levels and raw.get("thinking_levels"):
            levels = list(raw["thinking_levels"])
            default_lv = raw.get("default_thinking_level")
            variant = raw.get("thinking_variant")
        entry: dict[str, Any] = {
            k: v for k, v in raw.items() if not k.startswith("_")
        }
        entry.update({
            "id": mid,
            "name": raw.get("name", mid),
            "api": raw.get("api") or default_api,
            "context_window": int(raw.get("context_window", 0)) or 0,
            "max_tokens": int(raw.get("max_tokens", 0)) or 0,
            "vision": bool(raw.get("vision", False)),
            "reasoning": reasoning,
            "thinking_levels": levels,
            "default_thinking_level": default_lv,
            "thinking_variant": variant,
            "tools": bool(raw.get("tools", True)),
            "enabled": mid in enabled_ids,
        })
        out.append(entry)

    return out


def list_enabled_models() -> list[dict[str, Any]]:
    """Flat list of all enabled models across enabled providers — used
    by the chat page model picker.

    Reads the runtime registry (``ENABLED_MODELS`` = the config spec rows
    the user enabled) DIRECTLY — no live browse, no network. The registry
    is already exactly "the enabled models", so the picker just reshapes
    each ``Model`` into the row dict the composer consumes and stamps the
    provider label. Providers whose toggle is off are excluded.
    """
    from openprogram.providers.enabled_models import ENABLED_MODELS

    from .providers import _label
    from .storage import _read_providers_cfg

    cfg = _read_providers_cfg()
    out: list[dict[str, Any]] = []
    for key, model in ENABLED_MODELS.items():
        provider = getattr(model, "provider", None) or (
            key.split("/", 1)[0] if "/" in key else key
        )
        pcfg = cfg.get(provider, {})
        if not pcfg.get("enabled"):
            continue
        row = _model_to_dict(model, enabled=True)
        row["provider"] = provider
        row["provider_label"] = _label(provider)
        out.append(row)
    return out

"""Catalog listing — turn provider + model registry + config state into
the JSON the UI consumes.

Three public listing functions:

* ``list_providers`` — left-hand provider sidebar (LLM Providers
  settings page). Each row carries enable / configured / model-count
  / setup hint state.
* ``list_models_for_provider`` — right-hand model table for a single
  provider. Unions the static registry with ``custom_models``;
  respects the "fetched is authoritative" flag so legacy aliases hide
  after Fetch.
* ``list_enabled_models`` — flat picker for the chat composer.
  Iterates enabled providers + enabled models; surfaces both builtin
  and custom rows.

Plus the small ``_model_to_dict`` helper that produces the per-model
JSON shape both the per-provider table and the flat picker emit.
"""
from __future__ import annotations

from typing import Any


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
        # Thinking UX capability (see providers/thinking_catalog.py).
        # Empty `thinking_levels` → UI hides the menu for this model.
        "thinking_levels": list(getattr(model, "thinking_levels", []) or []),
        "default_thinking_level": getattr(model, "default_thinking_level", None),
        "thinking_variant": getattr(model, "thinking_variant", None),
        "tools": True,  # all HTTP providers route tool_calls
        "enabled": enabled,
    }


def list_providers() -> list[dict[str, Any]]:
    """Unified provider list with enable/configure status and model counts."""
    from openprogram.providers import get_providers, get_models

    from .providers import (
        _CLI_PROVIDERS,
        _ENV_API_KEYS,
        _FETCH_MODELS_PROVIDERS,
        _is_configured,
        _label,
    )
    from .setup_hints import _setup_hint
    from .storage import _read_providers_cfg
    from .fetchers import _FETCHERS

    cfg = _read_providers_cfg()
    result: list[dict[str, Any]] = []

    # HTTP providers from registry
    seen: set[str] = set()
    for pid in get_providers():
        seen.add(pid)
        pcfg = cfg.get(pid, {})
        models = get_models(pid)
        custom = pcfg.get("custom_models") or []
        enabled_ids = set(pcfg.get("enabled_models") or [])
        all_ids = {m.id for m in models} | {c.get("id") for c in custom if c.get("id")}
        default_base = models[0].base_url if models and models[0].base_url else ""
        entry = {
            "id": pid,
            "label": _label(pid),
            "kind": "api",
            "enabled": bool(pcfg.get("enabled", False)),
            "configured": _is_configured(pid),
            "api_key_env": _ENV_API_KEYS.get(pid),
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
            # Note: we no longer force ``api_key_env = None`` when a
            # hint is present. That override was originally there for
            # claude-code (whose Meridian proxy doesn't take an API
            # key), but it also clobbered ``anthropic``'s hint — which
            # IS API-key based and needs both the hint AND the paste
            # field. The ``_ENV_API_KEYS`` dict already carries the
            # correct null/non-null state per provider, so trust it.
        result.append(entry)

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


def list_models_for_provider(provider_id: str) -> list[dict[str, Any]]:
    """All models for a provider + their enabled flag (from config).

    Sources merged:
      - Static registry (from ``openprogram.providers``)
      - Dynamic custom_models the user pulled via Fetch Models or added
        by hand (stored under ``config.providers[<name>].custom_models``).

    Once the user has clicked Fetch Models successfully
    (``pcfg["models_fetched"] = True``), we treat upstream as
    authoritative — builtin-registry rows that aren't in the
    fetched-or-manual set are hidden. That's what makes a Fetch click
    feel like "replace" instead of "append".
    """
    from openprogram.providers import get_models
    from openprogram.providers.thinking_catalog import derive_thinking_fields

    from .providers import _PROVIDER_DEFAULT_API
    from .storage import _read_providers_cfg

    cfg = _read_providers_cfg()
    pcfg = cfg.get(provider_id, {})
    enabled_ids = set(pcfg.get("enabled_models") or [])
    fetched_only = bool(pcfg.get("models_fetched"))
    custom_ids: set[str] = {
        m.get("id") for m in (pcfg.get("custom_models") or []) if m.get("id")
    }

    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for m in get_models(provider_id):
        if fetched_only and m.id not in custom_ids:
            continue
        seen.add(m.id)
        out.append(_model_to_dict(m, m.id in enabled_ids))

    # Default API to dispatch through for this provider — see
    # ``providers._PROVIDER_DEFAULT_API`` for the rationale. Falls
    # back to ``"custom"`` (the legacy unrouteable sentinel) for
    # providers not in the map so we don't silently mis-route an
    # unknown one.
    default_api = _PROVIDER_DEFAULT_API.get(provider_id, "custom")
    for raw in pcfg.get("custom_models", []):
        mid = raw.get("id") or ""
        if not mid or mid in seen:
            continue
        reasoning = bool(raw.get("reasoning", False))
        levels, default_lv, variant = derive_thinking_fields(
            provider_id, mid, reasoning, bool(raw.get("supports_xhigh", False))
        )
        out.append({
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
            "custom": True,
        })

    return out


def list_enabled_models() -> list[dict[str, Any]]:
    """Flat list of all enabled models across enabled providers — used
    by the chat page model picker.

    Walks two sources to find enabled models:

    1. The static registry (``get_models(pid)``) — the canonical
       builtin catalogue from ``providers/models_generated.py``.
    2. The ``custom_models`` list under each provider's config entry —
       rows the user pulled via Fetch Models or added by hand. Without
       this second pass, a freshly-fetched id like
       ``claude-sonnet-4-6`` (which doesn't exist in the static
       registry) gets toggled enabled, persists to config, but
       silently never appears in the chat picker.
    """
    from openprogram.providers import get_providers, get_models
    from openprogram.providers.thinking_catalog import derive_thinking_fields

    from .providers import _PROVIDER_DEFAULT_API, _is_configured, _label
    from .storage import _read_providers_cfg

    cfg = _read_providers_cfg()
    out: list[dict[str, Any]] = []
    for pid in get_providers():
        pcfg = cfg.get(pid, {})
        if not pcfg.get("enabled"):
            continue
        enabled_ids = set(pcfg.get("enabled_models") or [])
        if not enabled_ids:
            continue
        if not _is_configured(pid):
            continue
        emitted_ids: set[str] = set()
        fetched_only = bool(pcfg.get("models_fetched"))
        custom_ids = {
            m.get("id") for m in (pcfg.get("custom_models") or []) if m.get("id")
        }
        for m in get_models(pid):
            if m.id not in enabled_ids:
                continue
            # After a Fetch, the user's upstream answer takes precedence
            # over the static catalogue — hide builtin rows that the
            # fetch didn't reaffirm (matches list_models_for_provider).
            if fetched_only and m.id not in custom_ids:
                continue
            entry = _model_to_dict(m, True)
            entry["provider"] = pid
            entry["provider_label"] = _label(pid)
            out.append(entry)
            emitted_ids.add(m.id)

        # Now the second pass: custom_models that the registry doesn't
        # know about. Build a minimal ``Model``-shaped dict that the
        # chat dispatcher accepts via ``api: <default_api>``.
        default_api = _PROVIDER_DEFAULT_API.get(pid, "custom")
        for raw in (pcfg.get("custom_models") or []):
            mid = raw.get("id") or ""
            if not mid or mid not in enabled_ids or mid in emitted_ids:
                continue
            reasoning = bool(raw.get("reasoning", False))
            levels, default_lv, variant = derive_thinking_fields(
                pid, mid, reasoning, bool(raw.get("supports_xhigh", False))
            )
            entry = {
                "id": mid,
                "name": raw.get("name", mid),
                "api": raw.get("api") or default_api,
                "context_window": int(raw.get("context_window", 0)) or 0,
                "max_tokens": int(raw.get("max_tokens", 0)) or 0,
                "vision": bool(raw.get("vision", False)),
                "video": False,
                "audio": False,
                "reasoning": reasoning,
                "thinking_levels": levels,
                "default_thinking_level": default_lv,
                "thinking_variant": variant,
                "tools": bool(raw.get("tools", True)),
                "enabled": True,
                "provider": pid,
                "provider_label": _label(pid),
                "custom": True,
            }
            out.append(entry)
    return out

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
    """Unified provider list with enable/configure status and model
    counts.

    Two source-of-truth tiers merged:

      1. **Static registry** — providers with at least one ``Model``
         row in ``providers/models_generated.py``. These have a baked-
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
        enabled_ids = set(pcfg.get("enabled_models") or [])
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
            # Which Meridian account (profile) claude-code is pinned to —
            # surfaced so the settings UI can show/edit it. Empty for every
            # other provider. See docs/design/claude-code-meridian-profile.md.
            "meridian_profile": pcfg.get("meridian_profile") or "",
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
        enabled_ids = set(pcfg.get("enabled_models") or [])
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
    from openprogram.providers.thinking_catalog import derive_thinking_fields

    from .providers import _default_api_for
    from .storage import _read_providers_cfg
    from .provider_models import combined_models

    cfg = _read_providers_cfg()
    pcfg = cfg.get(provider_id, {})
    enabled_ids = set(pcfg.get("enabled_models") or [])
    default_api = _default_api_for(provider_id) or "openai-completions"

    # Merge combined_models (fetched + models.dev) with custom_models from
    # config (community providers store everything in custom_models).
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    all_rows = list(combined_models(provider_id))
    # custom_models may contain entries not in combined_models (e.g. community
    # providers that only exist in config, never fetched to _catalog/fetched/).
    for cm in (pcfg.get("custom_models") or []):
        cmid = cm.get("id") or ""
        if cmid and cmid not in {r.get("id") for r in all_rows}:
            all_rows.append(cm)
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

    Delegates to ``list_models_for_provider`` so thinking_levels and all
    other fields are computed through the same path — no divergence.
    """
    from openprogram.providers import get_providers

    from .providers import _is_configured, _label
    from .storage import _read_providers_cfg

    cfg = _read_providers_cfg()
    out: list[dict[str, Any]] = []

    from openprogram.auth.aliases import resolve as _canonical_provider
    static_pids = list(get_providers())
    static_set = set(static_pids)
    community_pids = [
        pid for pid, pc in cfg.items()
        if pid not in static_set
        and pc.get("enabled")
        and (pc.get("enabled_models"))
        and _canonical_provider(pid) == pid
    ]
    for pid in [*static_pids, *community_pids]:
        pcfg = cfg.get(pid, {})
        if not pcfg.get("enabled"):
            continue
        enabled_ids = set(pcfg.get("enabled_models") or [])
        if not enabled_ids:
            continue
        if not _is_configured(pid):
            continue
        for m in list_models_for_provider(pid):
            if m.get("id") not in enabled_ids:
                continue
            m["enabled"] = True
            m["provider"] = pid
            m["provider_label"] = _label(pid)
            out.append(m)
    return out

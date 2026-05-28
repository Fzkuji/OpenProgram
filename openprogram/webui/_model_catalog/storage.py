"""Persistence layer for the model catalog.

All reads and writes against ``~/.agentic/config.json``'s ``providers``
sub-tree, plus the small helpers that resolve per-provider API keys and
base URLs from the same store.

The interesting domain logic lives here:

* ``add_custom_models`` — union / merge semantics. Kept for the few
  call sites that legitimately want to *add* a row without rotating
  others (manual additions from the UI).

* ``replace_fetched_models`` — rotate-and-replace semantics. Used by
  the Fetch Models flow. Rows tagged ``_source: "fetched"`` are owned
  by this function and rotate on each fetch; rows the user added by
  hand are left alone. Also prunes ``enabled_models`` of dead ids on
  rotation so the picker doesn't try to instantiate a model the
  runtime can no longer find.

The module-level ``_cache_lock`` serialises all read-modify-write
sequences. Same lock the legacy monolith used; sharing one process-
wide lock is fine because the config file is tiny.
"""
from __future__ import annotations

import threading
from typing import Any


_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# config.json IO
# ---------------------------------------------------------------------------

def _read_providers_cfg() -> dict[str, dict[str, Any]]:
    from openprogram.webui.server import _load_config
    return _load_config().get("providers", {})


def _write_providers_cfg(providers_cfg: dict[str, dict[str, Any]]) -> None:
    from openprogram.webui.server import _load_config, _save_config
    cfg = _load_config()
    cfg["providers"] = providers_cfg
    _save_config(cfg)


# ---------------------------------------------------------------------------
# Per-provider config (base URL override, use_responses_api toggle)
# ---------------------------------------------------------------------------

def get_provider_config(provider_id: str) -> dict[str, Any]:
    """Expose per-provider user config (base_url override, toggles)."""
    cfg = _read_providers_cfg()
    pcfg = cfg.get(provider_id, {})
    return {
        "base_url": pcfg.get("base_url") or "",
        "use_responses_api": bool(pcfg.get("use_responses_api", False)),
    }


def set_provider_config(provider_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.setdefault(provider_id, {})
        if "base_url" in patch:
            bu = (patch.get("base_url") or "").strip()
            if bu:
                pcfg["base_url"] = bu
            else:
                pcfg.pop("base_url", None)
        if "use_responses_api" in patch:
            pcfg["use_responses_api"] = bool(patch.get("use_responses_api"))
        _write_providers_cfg(cfg)
    return get_provider_config(provider_id)


# ---------------------------------------------------------------------------
# Custom models CRUD
# ---------------------------------------------------------------------------

def add_custom_models(provider_id: str, models: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge a list of model descriptors into custom_models (dedup by id).

    Union semantics — preserves every existing row, only adds new ones.
    Use ``replace_fetched_models`` for the Fetch Models flow instead;
    this entry point is for manual ad-hoc additions where merge is the
    right behavior.
    """
    if not models:
        return {"provider": provider_id, "added": 0, "total": 0}
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.setdefault(provider_id, {})
        existing = {m.get("id"): m for m in pcfg.get("custom_models", []) if m.get("id")}
        added = 0
        for raw in models:
            mid = (raw.get("id") or "").strip()
            if not mid:
                continue
            if mid not in existing:
                existing[mid] = raw
                added += 1
            else:
                # Shallow merge new hints into the existing entry.
                existing[mid].update({k: v for k, v in raw.items() if v is not None})
        pcfg["custom_models"] = list(existing.values())
        _write_providers_cfg(cfg)
    return {"provider": provider_id, "added": added, "total": len(existing)}


def replace_fetched_models(provider_id: str, models: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace the fetched-from-upstream model set for a provider,
    leaving any manually-added rows alone.

    "Fetch models" is the user saying "tell me what the upstream
    provider actually serves right now". When upstream's answer drifts
    (rename, new family, dropped variant) the previous answer is wrong
    — we shouldn't leave stale rows hanging around. ``add_custom_models``
    is union / merge semantics; this is rotate-and-replace.

    Rows marked ``_source: "fetched"`` are owned by this function and
    rotate on each fetch. Anything without that marker is a manual
    addition and we leave it untouched. Also flips
    ``pcfg["models_fetched"] = True`` so the list endpoint knows to hide
    builtin-registry rows that upstream's fresh answer doesn't endorse
    — otherwise the legacy ``claude-opus-4`` row keeps showing up even
    after a successful fetch, since it comes from
    ``providers/models_generated.py`` not config.

    Side-effect on ``enabled_models``: ids that no longer correspond to
    a visible row are pruned. After a rename like
    ``claude-opus-4`` → ``claude-opus-4-7`` the old id is dead — leaving
    it in ``enabled_models`` means the picker tries to instantiate a
    model the runtime can't resolve.
    """
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.setdefault(provider_id, {})
        prior = pcfg.get("custom_models", []) or []
        # Keep manual entries; rotate out anything we previously fetched.
        kept_manual = [m for m in prior if m.get("_source") != "fetched"]
        kept_ids = {m.get("id") for m in kept_manual if m.get("id")}
        new_rows: list[dict[str, Any]] = []
        for m in models:
            mid = (m.get("id") or "").strip()
            if not mid or mid in kept_ids:
                # Manual override of an upstream id wins — don't overwrite
                # what the user typed in by hand.
                continue
            row = dict(m)
            row["_source"] = "fetched"
            new_rows.append(row)
        pcfg["custom_models"] = kept_manual + new_rows
        visible_ids = {r["id"] for r in (new_rows + kept_manual) if r.get("id")}
        prior_enabled = list(pcfg.get("enabled_models") or [])
        pcfg["enabled_models"] = [mid for mid in prior_enabled if mid in visible_ids]
        dropped_enabled = [mid for mid in prior_enabled if mid not in visible_ids]
        pcfg["models_fetched"] = True
        _write_providers_cfg(cfg)
        return {
            "provider": provider_id,
            "added": len(new_rows),
            "removed": len(prior) - len(kept_manual),  # rotated-out fetched rows
            "total": len(pcfg["custom_models"]),
            "dropped_enabled": dropped_enabled,
        }


def remove_custom_model(provider_id: str, model_id: str) -> dict[str, Any]:
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.setdefault(provider_id, {})
        pcfg["custom_models"] = [
            m for m in pcfg.get("custom_models", []) if m.get("id") != model_id
        ]
        # Also drop from enabled list, if present.
        if "enabled_models" in pcfg:
            pcfg["enabled_models"] = [
                mid for mid in pcfg["enabled_models"] if mid != model_id
            ]
        _write_providers_cfg(cfg)
    return {"provider": provider_id, "model": model_id, "removed": True}


# ---------------------------------------------------------------------------
# Base URL + API key resolution (used by fetchers + test_provider)
# ---------------------------------------------------------------------------

def _resolve_base_url(provider_id: str) -> str | None:
    """Resolved base URL across four sources, in order:

      1. User override saved at ``config.providers.<pid>.base_url``.
      2. First model's ``Model.base_url`` from the static registry.
      3. models.dev's ``api`` field for this provider id.
      4. ``None`` — caller is expected to surface a "No base URL
         resolvable" error rather than proceed with an empty string.

    Source 3 is what makes a community-only provider (no static
    registry entry yet) still able to fetch + test out of the box —
    the user just pastes the env var and goes.
    """
    cfg = _read_providers_cfg()
    pcfg = cfg.get(provider_id, {})
    if pcfg.get("base_url"):
        return pcfg["base_url"].rstrip("/")
    # Static registry baked-in base URL.
    from openprogram.providers import get_models
    ms = get_models(provider_id)
    if ms and ms[0].base_url:
        return ms[0].base_url.rstrip("/")
    # Community catalogue.
    from .providers import _default_base_url_for
    md_base = _default_base_url_for(provider_id)
    if md_base:
        return md_base.rstrip("/")
    return None


def _resolve_api_key(provider_id: str) -> str | None:
    """Resolved API key for a provider (env var > config api_keys).

    Looks up the provider's standard env var via
    ``providers._env_var_for`` (manual override → models.dev community
    catalogue) and falls back to the ``api_keys`` section of
    ``~/.agentic/config.json``. Returns ``None`` for providers that
    have no standard env var (OAuth / daemon providers like
    ``openai-codex``, ``claude-code``, ``github-copilot``) — those
    need their own resolution path (e.g. AuthManager.acquire_sync,
    daemon HEAD probe).
    """
    from .providers import _env_var_for
    env = _env_var_for(provider_id)
    if env:
        import os
        val = os.environ.get(env)
        if val:
            return val
        # Fall back to ~/.agentic/config.json api_keys
        from openprogram.webui.server import _load_config
        return _load_config().get("api_keys", {}).get(env) or None
    return None

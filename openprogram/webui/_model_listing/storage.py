"""Persistence layer for the model catalog.

All reads and writes against ``~/.openprogram/config.json``'s ``providers``
sub-tree, plus the small helpers that resolve per-provider API keys and
base URLs from the same store.

The interesting domain logic lives here:

* ``add_custom_models`` — union / merge semantics. Kept for the few
  call sites that legitimately want to *add* a row without rotating
  others (manual additions from the UI).

* ``replace_fetched_models`` — rotate-and-replace semantics. Used by
  the Fetch Models flow. Rows tagged ``_source: "fetched"`` are owned
  by this function and rotate on each fetch; rows the user added by
  hand are left alone. Also prunes enabled spec rows of dead ids on
  rotation so the picker doesn't try to instantiate a model the
  runtime can no longer find.

The module-level ``_cache_lock`` serialises all read-modify-write
sequences. Same lock the legacy monolith used; sharing one process-
wide lock is fine because the config file is tiny.
"""
from __future__ import annotations

import logging
import threading
from typing import Any


_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# config.json IO
# ---------------------------------------------------------------------------

def _read_providers_cfg() -> dict[str, dict[str, Any]]:
    from openprogram.webui.server import _load_config
    providers = _load_config().get("providers", {})
    # Migrate legacy configs from ANY reader, not just the settings UI:
    #   * why any-reader — a headless agent user with a legacy config never
    #     opens the settings page, so it must migrate on whatever reads config
    #     first (registry build, chat picker, connectivity check);
    #   * why safe to run this eagerly — the migration is idempotent (guarded
    #     to run once per process), reentrancy-guarded (its own reads don't
    #     recurse), and offline-capable (C2: builds minimal spec rows from
    #     local provider.json when live browse is unreachable), so it needs no
    #     network and can't loop or corrupt.
    _run_spec_migration_once(providers)
    return providers


def save_default_model(provider: str | None, model: str | None) -> None:
    """Persist the global model choice as ``default_provider``/``default_model``.

    The single write path for "the user switched the model in the top bar" —
    REST, the agent-settings exec branch and the ws action all route here, so
    the choice survives a restart (``_init_providers`` reads it back first).
    ``model`` is stored bare (no ``provider:`` prefix). Falsy args clear.
    Shares ``_cache_lock`` with the providers read-modify-write sequences.
    """
    from openprogram.webui.server import _load_config, _save_config
    if isinstance(model, str) and provider and model.startswith(f"{provider}:"):
        model = model[len(provider) + 1:]
    with _cache_lock:
        cfg = _load_config()
        if provider:
            cfg["default_provider"] = provider
        else:
            cfg.pop("default_provider", None)
        if model:
            cfg["default_model"] = model
        else:
            cfg.pop("default_model", None)
        _save_config(cfg)


def _write_providers_cfg(providers_cfg: dict[str, dict[str, Any]]) -> None:
    from openprogram.webui.server import _load_config, _save_config
    cfg = _load_config()
    cfg["providers"] = providers_cfg
    _save_config(cfg)
    # Every mutation of ``providers.<p>.models`` (toggle enable/disable, custom-
    # model add/remove, fetch replace, migration backfill) routes through here.
    # Rebuild the in-memory registry in place so the chat picker
    # (``list_enabled_models`` → ``ENABLED_MODELS``) reflects the change without
    # a process restart. ``reload()`` reads config from disk directly (not via
    # ``_read_providers_cfg``), so it never re-enters the migration guard.
    from openprogram.providers import enabled_models as _mg
    _mg.reload()


# ---------------------------------------------------------------------------
# Full-spec model rows (config.providers.<p>.models) — write path + migration
#
# The target design persists the FULL spec of each user-enabled model under
# ``providers.<p>.models`` (list[dict]). ``spec_row_for`` is the single source
# of truth for that shape: whatever ``list_models_for_provider`` produces,
# minus the UI-only ``enabled`` flag. Nested ``cost`` (and headers/compat/
# key_prefix) ride along untouched — do NOT flatten cost.
#
# Spec rows are the single source of truth: ``toggle_model`` no longer writes
# the legacy ``enabled_models`` id list. The one-time migration below still
# READS legacy ``enabled_models``/``custom_models`` to backfill spec rows for
# existing configs (read-side compat).
# ---------------------------------------------------------------------------

# Model-schema Literal for input modalities — anything outside this set (e.g.
# models.dev's "pdf") is not a real ``Model.input`` value and would fail schema
# validation, so it's filtered out when normalizing.
_MODEL_INPUT_MODALITIES = frozenset({"text", "image", "video", "audio"})


def _normalize_spec_row(row: dict[str, Any]) -> dict[str, Any]:
    """Add Model-schema keys (``input``, nested ``cost``) to a display-shape
    spec row, derived from the models.dev flat keys the UI emits.

    A spec row is BOTH the UI-display row (flat ``input_modalities``/
    ``input_cost``/… keys the settings table renders) AND the runtime
    ``Model`` shape (nested ``cost``, ``input`` list). Those diverge:
    ``Model.model_validate`` ignores unknown keys, so a row carrying only the
    flat display keys validates as text-only with zero cost. This bridges the
    two by stamping the schema keys the runtime reads. Flat display keys stay
    (Pydantic ignores them). Idempotent — an already-``input`` row is untouched.
    Returns a shallow copy; never mutates the input.
    """
    out = dict(row)
    if "input" not in out and "input_modalities" in out:
        mods = [m for m in (out.get("input_modalities") or []) if m in _MODEL_INPUT_MODALITIES]
        out["input"] = mods or ["text"]
    if "cost" not in out and any(
        k in out for k in ("input_cost", "output_cost", "cache_read_cost", "cache_write_cost")
    ):
        out["cost"] = {
            "input": float(out.get("input_cost", 0) or 0),
            "output": float(out.get("output_cost", 0) or 0),
            "cache_read": float(out.get("cache_read_cost", 0) or 0),
            "cache_write": float(out.get("cache_write_cost", 0) or 0),
        }
    return out


def spec_row_for(provider_id: str, model_id: str) -> dict[str, Any] | None:
    """Full spec row for one model, as ``providers.<p>.models`` stores it.

    Copied from ``list_models_for_provider`` (the canonical row shape) with the
    UI-only ``enabled`` flag stripped, then normalized so the row carries the
    Model-schema keys (``input``, nested ``cost``) the runtime reads — not just
    the models.dev flat display keys. Returns ``None`` if the provider/listing
    can't resolve the id.
    """
    # Lazy import: listing imports storage, so a module-level import cycles.
    from .listing import list_models_for_provider
    for row in list_models_for_provider(provider_id):
        if row.get("id") == model_id:
            spec = {k: v for k, v in row.items() if k != "enabled"}
            return _normalize_spec_row(spec)
    return None


def _upsert_spec_row(pcfg: dict[str, Any], spec: dict[str, Any]) -> None:
    # Every persisted spec row routes through here (toggle enable, login-enable
    # defaults, manual add, migration backfill). Normalize once at the choke
    # point so a row built from models.dev flat keys carries the Model-schema
    # ``input``/``cost`` the runtime reads. Idempotent for already-normalized rows.
    spec = _normalize_spec_row(spec)
    rows = pcfg.setdefault("models", [])
    mid = spec.get("id")
    for i, r in enumerate(rows):
        if r.get("id") == mid:
            rows[i] = spec
            return
    rows.append(spec)


def _remove_spec_row(pcfg: dict[str, Any], model_id: str) -> None:
    if "models" in pcfg:
        pcfg["models"] = [r for r in pcfg["models"] if r.get("id") != model_id]


# One-time storage migration ------------------------------------------------
# Runs on the first ``_read_providers_cfg`` of the process. Reentrancy guard:
# the migration calls ``list_models_for_provider`` (→ ``_read_providers_cfg``),
# so without the guard it would recurse infinitely. ``_reset_spec_migration``
# is a test hook.
_spec_migration_done = False
_spec_migration_running = False


def _reset_spec_migration() -> None:
    global _spec_migration_done, _spec_migration_running
    _spec_migration_done = False
    _spec_migration_running = False


# Versioned second pass (config marker ``spec_migration_version``). Bump this
# whenever a new one-shot repair of already-migrated configs is needed; the
# pass runs once per machine (persisted marker), not once per process.
_SPEC_MIGRATION_VERSION = 3


def _run_spec_migration_once(providers: dict[str, dict[str, Any]]) -> None:
    global _spec_migration_done, _spec_migration_running
    if _spec_migration_done or _spec_migration_running:
        return
    _spec_migration_running = True
    try:
        changed = _migrate_specs(providers)
        repaired = _repair_over_merged_specs(providers)
        repaired = _repair_modality_cost_specs(providers) or repaired
    finally:
        _spec_migration_running = False
        _spec_migration_done = True
    if changed or repaired:
        # Persist the backfill + repair. ``_write_*`` re-reads the whole config;
        # the guard above keeps that read from re-entering migration. The repair
        # bumps the persisted ``spec_migration_version`` marker so it's one-shot
        # per machine (see ``_repair_over_merged_specs``).
        if repaired:
            _bump_spec_migration_version()
        _write_providers_cfg(providers)


def _spec_migration_version() -> int:
    from openprogram.webui.server import _load_config
    try:
        return int(_load_config().get("spec_migration_version", 0) or 0)
    except Exception:
        return 0


def _bump_spec_migration_version() -> None:
    from openprogram.webui.server import _load_config, _save_config
    cfg = _load_config()
    cfg["spec_migration_version"] = _SPEC_MIGRATION_VERSION
    _save_config(cfg)


def _repair_over_merged_specs(providers: dict[str, dict[str, Any]]) -> bool:
    """One-shot repair of configs the v1 bulk-merge over-populated.

    The v1 ``_migrate_specs`` merged EVERY ``custom_models`` row into
    ``providers.<p>.models`` tagged ``source: "manual"``. But for community
    providers ``custom_models`` was the whole upstream catalogue (an
    availability cache), not the user's enabled set — so the enabled-only
    registry/picker ballooned (openrouter: 399 rows for 3 enabled).

    Precise reversal: for each provider, DROP every row with
    ``source == "manual"`` whose id is NOT in that provider's legacy
    ``enabled_models``. This is exact because the only writer of a manual-
    tagged row is the v1 merge (``toggle_model`` enable writes a spec row with
    NO ``source`` key — see ``spec_row_for``, which strips only ``enabled``).
    So a manual row not in ``enabled_models`` can only be a bulk-merge artefact,
    never a genuine user action. Rows without ``source`` (toggled since
    migration) and rows with id in ``enabled_models`` stay untouched. Rows
    tagged ``source: "migration-minimal"`` keep their semantics (id is in
    ``enabled_models`` by construction, so this pass never touches them).

    Runs once per machine, guarded by the persisted ``spec_migration_version``
    marker. Returns True if it pruned anything.
    """
    if _spec_migration_version() >= _SPEC_MIGRATION_VERSION:
        return False
    repaired = False
    for pcfg in providers.values():
        if not isinstance(pcfg, dict):
            continue
        rows = pcfg.get("models") or []
        if not rows:
            continue
        enabled = set(pcfg.get("enabled_models") or [])
        kept = [
            r for r in rows
            if r.get("source") != "manual" or r.get("id") in enabled
        ]
        if len(kept) != len(rows):
            pcfg["models"] = kept
            repaired = True
    return repaired


def _repair_modality_cost_specs(providers: dict[str, dict[str, Any]]) -> bool:
    """v3 one-shot repair: rewrite pre-v3 spec rows that stored modalities/cost
    under models.dev flat keys the ``Model`` schema doesn't read.

    Rows written before ``_normalize_spec_row`` existed carry ``input_modalities``
    but no ``input`` (so the runtime saw text-only → image chats failed the
    modality validator) and flat ``input_cost``/``output_cost``/… but no nested
    ``cost`` (so cost read as zero). This converts each such row to the schema
    shape, filtering ``input`` to the allowed Literal values (drops "pdf"), and
    DROPS the now-redundant flat keys so the persisted row is clean.

    Runs once per machine, guarded by the persisted ``spec_migration_version``
    marker (shared with the other v3 pass). Returns True if it rewrote anything.
    """
    if _spec_migration_version() >= _SPEC_MIGRATION_VERSION:
        return False
    _FLAT_COST_KEYS = ("input_cost", "output_cost", "cache_read_cost", "cache_write_cost")
    repaired = False
    for pcfg in providers.values():
        if not isinstance(pcfg, dict):
            continue
        for row in (pcfg.get("models") or []):
            if not isinstance(row, dict):
                continue
            touched = False
            norm = _normalize_spec_row(row)  # adds input/cost from the flat keys
            for k in ("input", "cost"):
                if k not in row and k in norm:
                    row[k] = norm[k]
                    touched = True
            # Drop the now-redundant flat display keys so the row is the clean
            # schema shape. (The settings table rebuilds display fields live
            # from browse rows; it never reads these off the persisted spec.)
            for k in ("input_modalities", "output_modalities", *_FLAT_COST_KEYS):
                if row.pop(k, None) is not None:
                    touched = True
            repaired = repaired or touched
    return repaired


def _minimal_spec_row(pid: str, mid: str, pcfg: dict[str, Any]) -> dict[str, Any]:
    """Offline-buildable spec row for a legacy enabled id ``spec_row_for``
    couldn't resolve (no live browse — offline / no key).

    Built from what IS local: the provider's ``provider.json`` default
    endpoint (api/base_url), falling back to the user's saved ``base_url``
    override and ``openai-completions`` for a community provider with no
    dir. Thinking fields derive from ``thinking.json``. Tagged
    ``source: "migration-minimal"`` so the next online Refresh overwrites it
    (Refresh upserts by id regardless of source). Cost/context stay unset —
    they heal on that Refresh. This keeps existing users' enabled models
    resolvable offline after the enabled-only-registry migration instead of
    silently dropping to ``get_model → None``.
    """
    from openprogram.providers._provider_meta import provider_endpoints
    from openprogram.providers.thinking_spec import derive_thinking_fields

    ep = provider_endpoints(pid).get("default") or {}
    api = ep.get("api") or "openai-completions"
    base_url = ep.get("base_url") or (pcfg.get("base_url") or "")
    row: dict[str, Any] = {
        "id": mid,
        "name": mid,
        "api": api,
        "base_url": base_url,
        "source": "migration-minimal",
    }
    levels, default_lv, variant = derive_thinking_fields(pid, mid, False)
    if levels:
        row["thinking_levels"] = levels
        if default_lv:
            row["default_thinking_level"] = default_lv
        if variant:
            row["thinking_variant"] = variant
    return row


def _migrate_specs(providers: dict[str, dict[str, Any]]) -> bool:
    """Backfill ``providers.<p>.models`` from existing config, once.

    For each provider:
      * every ``enabled_models`` id missing a spec row gets one — from the
        live listing (``spec_row_for``) when reachable, else a minimal
        offline row (``_minimal_spec_row``) so the id still resolves without
        network; only ids for which even a minimal row can't be built stay in
        ``enabled_models`` (never dropped) with a warning;
      * a ``custom_models`` row is merged in tagged ``source: "manual"`` ONLY
        if its id is in this provider's legacy ``enabled_models``. The old
        Fetch flow cached a community provider's ENTIRE upstream catalogue in
        ``custom_models`` (openrouter: 399 rows for 3 enabled) — that key was
        an availability cache, never a "user enabled these" list, so merging
        all of it floods the enabled-only registry. Non-enabled custom rows
        stay put in the untouched ``custom_models`` key (rediscoverable via
        live browse); nothing is lost.

    Returns True if anything changed (caller persists).
    """
    changed = False
    for pid, pcfg in providers.items():
        if not isinstance(pcfg, dict):
            continue
        rows = pcfg.get("models") or []
        have = {r.get("id") for r in rows if r.get("id")}
        new_rows = list(rows)
        enabled = set(pcfg.get("enabled_models") or [])

        for mid in enabled:
            if mid in have:
                continue
            spec = spec_row_for(pid, mid)
            if spec is None:
                # Offline / no key: live browse returned nothing. Build a
                # minimal row from local provider.json so the id still
                # resolves (heals on the next online Refresh).
                try:
                    spec = _minimal_spec_row(pid, mid, pcfg)
                except Exception:
                    spec = None
            if spec is None:
                logging.getLogger(__name__).warning(
                    "spec migration: provider %s enabled model %r has no "
                    "resolvable spec — kept in enabled_models, no spec row",
                    pid, mid,
                )
                continue
            new_rows.append(spec)
            have.add(mid)
            changed = True

        for cm in (pcfg.get("custom_models") or []):
            cmid = cm.get("id")
            # Only genuinely-enabled custom rows enter the registry. The rest
            # are an availability cache, not user enablement — leave them in
            # custom_models.
            if not cmid or cmid in have or cmid not in enabled:
                continue
            new_rows.append({**cm, "source": "manual"})
            have.add(cmid)
            changed = True

        if changed and new_rows != rows:
            pcfg["models"] = new_rows
    return changed


# ---------------------------------------------------------------------------
# Custom (user-added) providers
# ---------------------------------------------------------------------------
#
# A "custom" provider is a config-only key the user creates from the settings
# page for an OpenAI-compatible endpoint we don't ship a dir/models.dev entry
# for. It's marked ``source: "custom"`` so the listing can surface it as a
# tier-3 sidebar row and the delete route can refuse to touch anything else.
# The runtime already builds Models from the spec rows under
# ``providers.<pid>.models`` (enabled_models.py), so once the row exists the
# provider works at runtime with no further code.

import re as _re

# kebab-case slug: lowercase alnum groups joined by single hyphens.
_SLUG_RE = _re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _slugify(text: str) -> str:
    """Derive a kebab-case slug id from a free-form name.

    lowercase → spaces/underscores to hyphens → drop anything outside
    [a-z0-9-] → collapse hyphen runs → trim leading/trailing hyphens.
    Returns "" when nothing survives (e.g. a CJK/emoji-only name).
    """
    s = text.strip().lower()
    s = _re.sub(r"[\s_]+", "-", s)
    s = _re.sub(r"[^a-z0-9-]", "", s)
    s = _re.sub(r"-+", "-", s)
    return s.strip("-")


def _normalize_label(text: str) -> str:
    """Trim, collapse internal space runs, and title-case all-lowercase words.

    Mixed-case words the user typed deliberately ("OpenAI", "vLLM") are left
    untouched — only a word that is entirely lowercase gets capitalized.
    """
    words = text.strip().split()
    return " ".join(w.capitalize() if w.islower() else w for w in words)


def _known_provider_ids() -> set[str]:
    """Tier-1 (static registry) + tier-2 (models.dev catalogue) provider ids."""
    from openprogram.providers import get_providers
    known = set(get_providers())
    try:
        from .sources import models_dev
        known |= {p.get("id") for p in models_dev.list_providers() if p.get("id")}
    except Exception:
        pass
    return known


def _is_known_provider(provider_id: str) -> bool:
    return provider_id in _known_provider_ids()


def _id_taken(pid: str, cfg: dict[str, Any]) -> bool:
    """True when ``pid`` collides with a reserved alias, a known tier-1/tier-2
    provider id, or ANY existing config key.

    Used only by the derived-id auto-suffix loop, so an existing custom key
    counts as taken too — we suffix past it rather than clobber a provider the
    user already created. (The explicit-id path keeps its own overwrite-custom
    semantics inline.)"""
    from openprogram.auth.aliases import resolve as _resolve_alias
    if _resolve_alias(pid) != pid:
        return True
    if pid in _known_provider_ids():
        return True
    return pid in cfg


def create_custom_provider(
    provider_id: str, label: str, base_url: str
) -> dict[str, Any]:
    """Create a config-only custom provider. Returns ``{ok, ...}``.

    ``provider_id`` is optional. When blank the id is derived by slugifying
    ``label``; on collision the derived id gets a ``-2``/``-3``/… suffix until
    free (auto-resolve). An explicitly-passed id keeps the strict behavior:
    bad slug or collision → 400 (API compatibility). Writes the marker config
    ``providers.<id> = {enabled, source:"custom", label, base_url, models:[]}``
    through ``_write_providers_cfg`` (which reloads the runtime registry).
    """
    from .providers import _prettify

    explicit_id = (provider_id or "").strip().lower()
    label = _normalize_label(label or "")
    base_url = (base_url or "").strip()
    if not base_url:
        return {"ok": False, "error": "base_url is required"}

    with _cache_lock:
        cfg = _read_providers_cfg()
        if explicit_id:
            pid = explicit_id
            if not _SLUG_RE.match(pid):
                return {"ok": False, "error": "id must be a kebab-case slug (a-z, 0-9, hyphens)"}
            from openprogram.auth.aliases import resolve as _resolve_alias
            if _resolve_alias(pid) != pid:
                return {"ok": False, "error": f"{pid!r} is a reserved alias — pick another id"}
            if pid in _known_provider_ids() or (
                isinstance(cfg.get(pid), dict) and cfg[pid].get("source") != "custom"
            ):
                return {"ok": False, "error": f"provider {pid!r} already exists"}
        else:
            base_pid = _slugify(label)
            if not base_pid:
                return {"ok": False, "error": "name must contain letters or digits (a-z, 0-9)"}
            pid = base_pid
            n = 2
            while _id_taken(pid, cfg):
                pid = f"{base_pid}-{n}"
                n += 1
        cfg[pid] = {
            "enabled": True,
            "source": "custom",
            "label": label or _prettify(pid),
            "base_url": base_url,
            "models": [],
        }
        _write_providers_cfg(cfg)
    return {"ok": True, "id": pid, "label": cfg[pid]["label"], "base_url": base_url}


def delete_custom_provider(provider_id: str) -> dict[str, Any]:
    """Delete a custom provider's config key. Refuses non-custom providers.

    Leaves the AuthStore credential pool on disk (don't silently drop the
    user's key) — only the config marker + spec rows go.
    """
    pid = (provider_id or "").strip().lower()
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.get(pid)
        if not isinstance(pcfg, dict) or pcfg.get("source") != "custom":
            return {"ok": False, "error": f"{pid!r} is not a custom provider"}
        del cfg[pid]
        _write_providers_cfg(cfg)
    return {"ok": True, "id": pid, "removed": True}


def _is_custom_provider(provider_id: str) -> bool:
    """True when ``provider_id`` is a user-added custom provider
    (``providers.<pid>.source == "custom"``)."""
    pcfg = _read_providers_cfg().get(provider_id) or {}
    return isinstance(pcfg, dict) and pcfg.get("source") == "custom"


def add_manual_model(provider_id: str, model_id: str, name: str | None = None) -> dict[str, Any]:
    """Add a manually-typed model id as an ENABLED spec row for a provider.

    For custom / dir-less providers whose ``/models`` endpoint is unavailable,
    the user types a model id by hand. This writes a minimal spec row into
    ``providers.<pid>.models`` (the single source of truth the runtime reads)
    tagged ``source: "manual"``, with ``api`` and ``base_url`` derived from the
    provider config, so the model is immediately usable in chat after reload.

    Idempotent by id (upsert). Returns ``{ok, ...}``.
    """
    from .providers import _default_api_for
    mid = (model_id or "").strip()
    if not mid:
        return {"ok": False, "error": "model id is required"}
    with _cache_lock:
        cfg = _read_providers_cfg()
        # Reject an unknown provider id: creating a row for one would write an
        # ENABLED_MODELS entry with an empty base_url that can't dispatch. Known
        # = a tier-1/tier-2 provider (static registry or models.dev catalogue),
        # or an existing custom config key.
        if not _is_known_provider(provider_id) and (
            cfg.get(provider_id, {}).get("source") != "custom"
        ):
            return {"ok": False, "error": f"unknown provider {provider_id!r}"}
        pcfg = cfg.setdefault(provider_id, {})
        api = pcfg.get("api") or _default_api_for(provider_id) or "openai-completions"
        base_url = pcfg.get("base_url") or _resolve_base_url(provider_id) or ""
        spec = {
            "id": mid,
            "name": (name or "").strip() or mid,
            "api": api,
            "base_url": base_url,
            "source": "manual",
        }
        _upsert_spec_row(pcfg, spec)
        _write_providers_cfg(cfg)
    return {"ok": True, "provider": provider_id, "model": mid}


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
    ``providers/enabled_models.py`` not config.

    Side-effect on the enabled spec rows: ids that no longer correspond to
    a visible row are pruned. After a rename like
    ``claude-opus-4`` → ``claude-opus-4-7`` the old id is dead — leaving its
    spec row means the picker tries to instantiate a model the runtime can't
    resolve.
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
        # Drop any enabled spec row whose id is now dead (gone from the fresh
        # fetch and not a kept manual row).
        enabled_spec_ids = [r.get("id") for r in (pcfg.get("models") or []) if r.get("id")]
        dropped_enabled = [mid for mid in enabled_spec_ids if mid not in visible_ids]
        for mid in dropped_enabled:
            _remove_spec_row(pcfg, mid)
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
        # Drop the enabled spec row (the single source of truth).
        _remove_spec_row(pcfg, model_id)
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
    from .providers import _default_api_for, _default_base_url_for
    base = None
    if pcfg.get("base_url"):
        base = pcfg["base_url"]
    else:
        # Static registry baked-in base URL. Prefer the self-contained
        # providers/<p>/provider.json metadata (no ENABLED_MODELS read, breaks the
        # providers<->webui circular dep); fall back to the legacy
        # enabled_models-backed get_models() while both sources coexist.
        from openprogram.providers._provider_meta import provider_base_url
        meta_base = provider_base_url(provider_id)
        if meta_base:
            base = meta_base
        else:
            from openprogram.providers import get_models
            ms = get_models(provider_id)
            if ms and ms[0].base_url:
                base = ms[0].base_url
            else:
                # Community catalogue.
                base = _default_base_url_for(provider_id)
    if not base:
        return None
    base = base.rstrip("/")
    # Anthropic-wire normalisation: the anthropic-messages layer appends
    # /v1/messages and /v1/models itself, so the base must NOT carry its
    # own /v1. models.dev ships some Anthropic endpoints as
    # ``…/anthropic/v1`` — strip the trailing /v1 so the path doesn't
    # double (a general rule replacing the old per-provider base override;
    # scoped to anthropic-messages so it never touches an OpenAI ``…/v1``).
    if base.endswith("/v1") and _default_api_for(provider_id) == "anthropic-messages":
        base = base[: -len("/v1")].rstrip("/")
    return base


def _resolve_api_key(provider_id: str) -> str | None:
    """Resolved API key for a provider (AuthStore > env var).

    Looks up the provider's standard env var via
    ``providers._env_var_for`` (manual override → models.dev community
    catalogue). Returns ``None`` for providers that
    have no standard env var (OAuth / daemon providers like
    ``openai-codex``, ``claude-code``, ``github-copilot``) — those
    need their own resolution path (e.g. AuthManager.acquire_sync,
    daemon HEAD probe).
    """
    # AuthStore FIRST: the account manager ("Add key" in the web form) saves
    # credentials into the AuthStore (keyed by provider/profile). Without
    # this, the connectivity check and Fetch-models resolved only env vars
    # and reported "not set" for a key the per-account validate had already
    # proved VALID.
    try:
        from openprogram.auth.resolver import resolve_api_key_sync
        tok = resolve_api_key_sync(provider_id)
        if tok:
            return tok
    except Exception:
        pass
    # Known providers delegate to the canonical resolver (env vars; all
    # special cases + the historical-name reconciliation live there now —
    # see docs/design/providers/auth/api-key-resolution-unification.md).
    from openprogram.providers.env_api_keys import env_vars_for, resolve_api_key
    if env_vars_for(provider_id):
        key = resolve_api_key(provider_id)
        if key:
            return key
    # Community / models.dev providers: their saved key lives in the
    # AuthStore too (checked above) — nothing else to consult.
    return None

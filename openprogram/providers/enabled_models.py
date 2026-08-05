"""Runtime model registry — built from the user's enabled models in config.

``ENABLED_MODELS`` holds ONLY the models the user has enabled (the full spec
rows persisted under config ``providers.<p>.models``) plus anything registered
dynamically at runtime (custom-model side-effect registration in the webui,
and the codex runtime-registration helper). Subscription providers no longer
seed rows at import — their default set is written to config as an enable at
login (``openprogram.auth.login_enable``). ``_load()`` reads those config spec
rows, fills
each row's missing api/base_url from the provider's ``providers/<p>/provider.json``
endpoints (row values win), and keys them ``"<key_prefix or provider>/<id>"``.

The dict object is MUTABLE and shared: dynamic writers do
``ENABLED_MODELS[k] = m`` in place. Public interface:
``from openprogram.providers.enabled_models import ENABLED_MODELS``.

An empty/missing config is a legal fresh-install state → empty registry.
"""
from __future__ import annotations

from .types import Model


# 高速档声明表（用户裁决 2026-07-12：支持的写出来，其他的不写）。
# 只有两个家族有 fast：GPT 5.4/5.5/5.6 系（service_tier="priority"）
# 和 Claude Opus 4.6/4.7/4.8（speed:"fast" + beta 头）。按模型 id 判，
# 与线路无关——同一个模型经网关转售照样有高速档。
def default_fast(model_id: str) -> bool:
    mid = (model_id or "").lower().split("/")[-1]
    if mid.startswith(("gpt-5.4", "gpt-5.5", "gpt-5.6")):
        return True
    return any(t in mid for t in (
        "opus-4-6", "opus-4-7", "opus-4-8",
        "opus-4.6", "opus-4.7", "opus-4.8",
    ))


def _build_model_from_row(row: dict, provider_id: str, endpoints: dict) -> Model:
    """A config spec row → Model. The row already carries most fields
    (incl. nested cost/headers/compat and usually ``api``); the provider's
    endpoint only fills api/base_url the row omits. Row values always win."""
    ep = endpoints.get(row.get("endpoint", "default")) or endpoints.get("default") or {}
    data = dict(row)
    data["provider"] = provider_id
    # Reader tolerance for configs that never pass through the webui spec
    # migration (pure-CLI users): map the legacy models.dev flat keys onto the
    # Model schema. ``input_modalities`` → ``input`` (filtered to the schema's
    # allowed values — drops "pdf" etc.); flat ``*_cost`` → nested ``cost``.
    # A row that already carries ``input``/``cost`` (webui-normalized) wins.
    if "input" not in data and "input_modalities" in data:
        _allowed = {"text", "image", "video", "audio"}
        mods = [m for m in (data.get("input_modalities") or []) if m in _allowed]
        data["input"] = mods or ["text"]
    if "cost" not in data and any(
        k in data for k in ("input_cost", "output_cost", "cache_read_cost", "cache_write_cost")
    ):
        data["cost"] = {
            "input": float(data.get("input_cost", 0) or 0),
            "output": float(data.get("output_cost", 0) or 0),
            "cache_read": float(data.get("cache_read_cost", 0) or 0),
            "cache_write": float(data.get("cache_write_cost", 0) or 0),
        }
    if not data.get("api"):
        data["api"] = ep.get("api", "openai-completions")
    if not data.get("base_url"):
        data["base_url"] = ep.get("base_url", "")
    # 高速档：配置行显式写了 fast 就听它的，没写的按声明表回填。
    if "fast" not in data:
        data["fast"] = default_fast(str(data.get("id") or ""))
    return Model.model_validate(data)


def _load() -> dict[str, Model]:
    from ._config_read import read_providers_config
    from .metadata import provider_endpoints
    from openprogram.auth.aliases import resolve

    merged: dict[str, Model] = {}
    try:
        providers_cfg = read_providers_config()
    except Exception:
        return merged
    cfg = providers_cfg or {}
    for provider_id, pcfg in cfg.items():
        if not isinstance(pcfg, dict):
            continue
        # An alias config key (e.g. legacy ``chatgpt-subscription``) whose
        # canonical id is ALSO a config key would produce duplicate registry
        # rows — same model twice in the picker, twice in the sidebar. Skip
        # the alias's rows; the canonical key owns them. A lone alias key
        # (canonical absent) still loads and routes via its resolved
        # endpoints — old configs keep working. get_model's alias fallback
        # keeps ``chatgpt-subscription/...`` lookups resolving either way.
        canon = resolve(provider_id)
        if canon != provider_id and canon in cfg:
            continue
        endpoints = provider_endpoints(provider_id)
        for row in (pcfg.get("models") or []):
            if not isinstance(row, dict):
                continue
            # A disabled manual row (kept in config so the user's hand-typed
            # id survives the toggle) must not enter the runtime registry.
            if row.get("enabled") is False:
                continue
            try:
                m = _build_model_from_row(row, provider_id, endpoints)
            except Exception:
                continue
            prefix = row.get("key_prefix") or provider_id
            merged[f"{prefix}/{m.id}"] = m
    return merged


ENABLED_MODELS: dict[str, Model] = _load()


def reload() -> dict[str, Model]:
    """Rebuild the registry from the current config spec rows, in place.

    Clears and repopulates the SAME ``ENABLED_MODELS`` dict object (never
    rebinds the name) so every module that did
    ``from ...enabled_models import ENABLED_MODELS`` sees the update —
    and dynamic writers' entries survive only if config still carries them.
    Called after a config write that changes enabled model specs (e.g. the
    Fetch/Refresh button).

    Returns the same dict for convenience.
    """
    fresh = _load()
    ENABLED_MODELS.clear()
    ENABLED_MODELS.update(fresh)
    return ENABLED_MODELS


def register_model_from_config(provider: str, model_id: str) -> bool:
    """Look ``model_id`` up in the user's config rows for ``provider`` and,
    if present, insert a ``Model`` row into ``ENABLED_MODELS`` so
    ``providers.get_model`` finds it. Reads the enabled spec rows
    (``providers.<p>.models``) first, falling back to the legacy
    ``custom_models`` key for a not-yet-migrated config.

    Side-effect on the module-level registry — deliberate. The
    alternative is plumbing custom-model metadata through every
    callsite that goes "look up Model row → read context_window /
    cost / modalities". Registering once at runtime-construction
    time is the smallest change that makes the registry the single
    source of truth.

    Returns ``True`` when a registration happened, ``False`` when the
    model wasn't found (genuine unknown id → caller re-raises).
    """
    try:
        from .metadata import default_api_for, provider_endpoints
        from .storage import _read_providers_cfg, _resolve_base_url
        from .types import ModelCost
    except Exception:
        return False

    cfg_pcfg = _read_providers_cfg().get(provider, {})

    # Enabled spec rows are full Model specs (same shape ``_load`` consumes) —
    # validate one directly if present.
    spec = next(
        (r for r in (cfg_pcfg.get("models") or []) if r.get("id") == model_id),
        None,
    )
    if spec is not None:
        try:
            m = _build_model_from_row(spec, provider, provider_endpoints(provider))
        except Exception:
            return False
        prefix = spec.get("key_prefix") or provider
        ENABLED_MODELS[f"{prefix}/{m.id}"] = m
        return True

    # Legacy custom_models (flat shape) — fallback for a not-yet-migrated config.
    raw = next(
        (c for c in (cfg_pcfg.get("custom_models") or []) if c.get("id") == model_id),
        None,
    )
    if not raw:
        return False

    api = raw.get("api") or default_api_for(provider) or "openai-completions"
    inputs: list[str] = list(raw.get("input_modalities") or ["text"])
    # Cost is optional — only stamp the fields the row actually has,
    # default 0.0 for missing keys so ModelCost validates.
    cost = ModelCost(
        input=float(raw.get("input_cost", 0) or 0),
        output=float(raw.get("output_cost", 0) or 0),
        cache_read=float(raw.get("cache_read_cost", 0) or 0),
        cache_write=float(raw.get("cache_write_cost", 0) or 0),
    )
    # Resolve through ``storage._resolve_base_url`` (user config → static
    # registry → models.dev) so the row gets the SAME normalised base the
    # rest of the pipeline uses — crucially the Anthropic-wire /v1 strip.
    # Reading models.dev raw here gave Anthropic providers a
    # ``…/anthropic/v1`` base, which the anthropic-messages layer then
    # doubled into ``…/v1/v1/messages`` → 404, so the model registered but
    # was unusable.
    base_url = _resolve_base_url(provider) or ""

    try:
        m = Model(
            id=model_id,
            name=str(raw.get("name") or model_id),
            api=api,
            provider=provider,
            base_url=base_url,
            reasoning=bool(raw.get("reasoning", False)),
            input=inputs,
            cost=cost,
            context_window=int(raw.get("context_window", 0) or 0),
            max_tokens=int(raw.get("max_tokens", 0) or 0),
        )
    except Exception:
        return False
    ENABLED_MODELS[f"{provider}/{model_id}"] = m
    return True

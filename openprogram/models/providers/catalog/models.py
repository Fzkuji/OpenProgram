"""
Model registry and utilities — mirrors packages/ai/src/models.ts
"""
from __future__ import annotations

from .enabled_models import ENABLED_MODELS
from ..types import Model, Usage
import logging

_log = logging.getLogger(__name__)


def get_model(provider: str, model_id: str) -> Model | None:
    """Get a model by provider and model ID. Returns None if not found.

    Falls back to alias-equivalent provider names. The model registry
    keys are historical (e.g. ``openai-codex/gpt-5.5``) while the
    canonical provider id from the runtime side is different (e.g.
    ``openai-codex``). Alias-aware lookup keeps both spellings
    working without duplicating thousands of ENABLED_MODELS rows.
    """
    key = f"{provider}/{model_id}"
    m = ENABLED_MODELS.get(key)
    if m is not None:
        return m
    # Try alias-equivalent provider names: anything that resolves to
    # `provider` via the alias table, plus the canonical form `provider`
    # itself maps to.
    try:
        from openprogram.models.auth.account.aliases import known_aliases
        from openprogram.models.auth.account.aliases import resolve
        candidates: set[str] = set()
        canon = resolve(provider)
        if canon != provider:
            candidates.add(canon)
        for alias, target in known_aliases().items():
            if target == provider or target == canon:
                if alias != provider:
                    candidates.add(alias)
        for alt in candidates:
            m = ENABLED_MODELS.get(f"{alt}/{model_id}")
            if m is not None:
                return m
    except Exception:
        # Alias resolution is a fallback; not found is the honest answer.
        _log.debug("alias model lookup failed for %s/%s", provider, model_id, exc_info=True)
    return None


def split_model_ref(
    value: str | None, provider: str | None = None,
) -> tuple[str | None, str | None]:
    """``(provider, model id)`` named by a stored model reference.

    Sessions and Agents store ``provider/model`` (``override_string``); a
    runtime id is ``provider:model``. The reference splits at whichever of
    the two comes first, and everything after it is the id, which may hold
    ``/`` or ``:`` itself (``openrouter/anthropic/claude-x``,
    ``ollama:llama3:8b``). A reference with neither is a bare id under
    ``provider``, and so is one that ``provider`` registers whole
    (``llama3:8b`` or ``meta-llama/llama-3:free`` stored with their
    provider beside them).
    """
    value = (value or "").strip()
    if not value:
        return (provider or None), None
    cut = min((i for i in (value.find("/"), value.find(":")) if i > 0), default=-1)
    if cut > 0 and value[cut + 1:]:
        if provider and value[:cut] != provider and get_model(provider, value) is not None:
            return provider, value
        return value[:cut], value[cut + 1:]
    return (provider or None), value


def find_model_ref(value: str | None, provider: str | None = None) -> Model | None:
    """The registry Model a stored reference names, or ``None``.

    A bare id whose own text holds a separator (``llama3:8b`` under
    ``ollama``) is also tried whole under ``provider``.
    """
    prov, model_id = split_model_ref(value, provider)
    found = get_model(prov, model_id) if prov and model_id else None
    whole = (value or "").strip()
    if found is None and provider and whole and (prov, model_id) != (provider, whole):
        found = get_model(provider, whole)
    return found


def get_providers() -> list[str]:
    """Return list of all registered providers."""
    seen: set[str] = set()
    result: list[str] = []
    for model in ENABLED_MODELS.values():
        if model.provider not in seen:
            seen.add(model.provider)
            result.append(model.provider)
    return sorted(result)


def get_models(provider: str | None = None) -> list[Model]:
    """Return all models, optionally filtered by provider."""
    models = list(ENABLED_MODELS.values())
    if provider is not None:
        models = [m for m in models if m.provider == provider]
    return models


def calculate_cost(model: Model, usage: Usage | dict) -> float:
    """Calculate total cost in USD from usage and model pricing. Also mutates usage.cost."""
    from openprogram.models.providers.types import UsageCost

    if isinstance(usage, dict):
        # Bedrock and Gemini CLI normalize dictionary-shaped streamed output.
        normalized = Usage.model_validate(usage)
        total = calculate_cost(model, normalized)
        usage["cost"] = normalized.cost.model_dump()
        return total

    input_cost = usage.input / 1_000_000 * (model.cost.input or 0.0)
    output_cost = usage.output / 1_000_000 * (model.cost.output or 0.0)
    cache_read_cost = usage.cache_read / 1_000_000 * (model.cost.cache_read or 0.0)
    cache_write_cost = usage.cache_write / 1_000_000 * (model.cost.cache_write or 0.0)
    total = input_cost + output_cost + cache_read_cost + cache_write_cost
    usage.cost = UsageCost(
        input=input_cost,
        output=output_cost,
        cache_read=cache_read_cost,
        cache_write=cache_write_cost,
        total=total,
    )
    return total


def supports_xhigh(model: Model) -> bool:
    """Check if a model supports xhigh reasoning."""
    if any(tag in model.id
           for tag in ("gpt-5.2", "gpt-5.3", "gpt-5.4", "gpt-5.5")):
        return True
    if model.api == "anthropic-messages":
        return "opus-4-6" in model.id or "opus-4.6" in model.id
    return False


def models_are_equal(a: Model | None, b: Model | None) -> bool:
    """Check if two models are equal by comparing both id and provider."""
    if a is None or b is None:
        return False
    return a.id == b.id and a.provider == b.provider


# Populate thinking_levels / default_thinking_level / thinking_variant on every
# Model from the overrides + default rules. Done once at import so downstream
# consumers (API endpoints, Runtime.thinking_level defaulting) see accurate
# capability data without re-computing per request.
from ..settings.thinking_spec import apply_thinking_fields as _apply_thinking_fields  # noqa: E402
_apply_thinking_fields(ENABLED_MODELS)

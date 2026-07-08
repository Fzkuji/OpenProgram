"""Load and query per-provider thinking.json specs.

Each provider directory contains a ``thinking.json`` that declares how
its API handles reasoning/effort parameters. This module loads those
files once and exposes helpers the rest of the codebase uses:

  get_thinking_spec(provider_id)  → the parsed dict for one provider
  translate_reasoning(model, level) → the API-ready value
  derive_thinking_levels(model, provider_id) → list of UI picker levels
"""
from __future__ import annotations

import json
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

_PROVIDERS_DIR = Path(__file__).parent


def _load_folded(dir_name: str, key: str, legacy_file: str) -> Optional[dict[str, Any]]:
    """Read provider.json's ``key`` block; fall back to the standalone
    ``legacy_file`` (with a DeprecationWarning) for un-migrated / out-of-tree
    provider dirs. Returns None when neither source has data."""
    prov = _PROVIDERS_DIR / dir_name / "provider.json"
    if prov.is_file():
        try:
            block = json.loads(prov.read_text(encoding="utf-8")).get(key)
            if block is not None:
                return block
        except (OSError, json.JSONDecodeError):
            pass
    legacy = _PROVIDERS_DIR / dir_name / legacy_file
    if legacy.is_file():
        warnings.warn(
            f"{dir_name}/{legacy_file} is deprecated; move it under the "
            f"'{key}' key of provider.json.",
            DeprecationWarning,
            stacklevel=3,
        )
        try:
            return json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None

# Fallback for providers without thinking.json — most OpenAI-compatible
# providers accept reasoning_effort as a pass-through string.
# Providers that share another provider's thinking config (same API).
_THINKING_ALIASES: dict[str, str] = {
    "claude-code": "anthropic",
}

_OPENAI_COMPAT_FALLBACK: dict[str, Any] = {
    "wire_format": "effort_string",
    "effort_map": {
        "low": "low",
        "medium": "medium",
        "high": "high",
    },
    "default_effort": "medium",
    "_fallback": True,
}


@lru_cache(maxsize=None)
def get_thinking_spec(provider_id: str) -> dict[str, Any]:
    """Load thinking.json for a provider.

    If no thinking.json exists, returns a generic OpenAI-compatible
    fallback (low/medium/high effort_string) so that community providers
    (groq, mistral, openrouter, etc.) work without hand-written configs.

    A missing / empty provider_id (e.g. the current provider couldn't be
    resolved because credentials expired) returns the same fallback
    rather than raising — agent_settings stays a 200 with a usable
    config instead of 500ing the whole panel.
    """
    if not provider_id:
        return _OPENAI_COMPAT_FALLBACK
    resolved = _THINKING_ALIASES.get(provider_id, provider_id)
    for dir_name in (resolved, resolved.replace("-", "_")):
        spec = _load_folded(dir_name, "thinking", "thinking.json")
        if spec is not None:
            return spec
    return _OPENAI_COMPAT_FALLBACK


def translate_reasoning(
    provider_id: str,
    model_id: str,
    level: str,
) -> Any:
    """Translate a framework ThinkingLevel to the provider's API value.

    Returns a string (effort_string) or int (budget_tokens), or None
    if the provider has no thinking support.
    """
    spec = get_thinking_spec(provider_id)
    wire = spec.get("wire_format")
    if not wire or wire == "none":
        return None

    # Check model-level override first
    override = spec.get("model_overrides", {}).get(model_id)
    if override:
        emap = override.get("effort_map", override.get("budget_map"))
        if emap is not None:
            if not emap:
                return None
            if level in emap:
                return emap[level]

    if wire == "effort_string":
        emap = spec.get("effort_map", {})
        return emap.get(level, spec.get("default_effort", "medium"))

    if wire == "budget_tokens":
        bmap = spec.get("budget_map", {})
        return bmap.get(level, 8192)

    return None


def get_model_variant(provider_id: str, model_id: str) -> Optional[str]:
    """Return the thinking_variant for a model, or None."""
    spec = get_thinking_spec(provider_id)
    override = spec.get("model_overrides", {}).get(model_id)
    return override.get("variant") if override else None


def derive_thinking_levels(
    provider_id: str,
    model_id: str,
    reasoning: bool,
) -> list[str]:
    """Derive the UI picker levels for a model from its provider's thinking.json.

    Returns the list of framework ThinkingLevel strings the model supports.
    Empty list means no thinking (UI hides the picker).
    """
    if not reasoning:
        return []

    spec = get_thinking_spec(provider_id)
    wire = spec.get("wire_format")
    if not wire or wire == "none":
        return []

    # Model override narrows the levels (empty map = no effort control)
    override = spec.get("model_overrides", {}).get(model_id)
    if override:
        emap = override.get("effort_map", override.get("budget_map"))
        if emap is not None:
            return list(emap.keys())

    # Provider-level map
    emap = spec.get("effort_map") or spec.get("budget_map")
    return list(emap.keys()) if emap else []


def get_default_effort(provider_id: str) -> Optional[str]:
    """Return the provider's default effort level, or None."""
    spec = get_thinking_spec(provider_id)
    return spec.get("default_effort")


def invalidate_cache() -> None:
    """Clear the cached specs (for tests or hot reload)."""
    get_thinking_spec.cache_clear()


def supports_minimal_effort(model_id: str) -> bool:
    """Whether a model accepts the ``minimal`` reasoning-effort level."""
    return "gpt-5.5" not in model_id


def derive_thinking_fields(
    provider_id: str,
    model_id: str,
    reasoning: bool,
    supports_xhigh: bool = False,
) -> tuple[list[str], str | None, str | None]:
    """Compute (thinking_levels, default_thinking_level, thinking_variant).

    Primary source: thinking.json via the spec helpers above. Falls back to
    the old hardcoded logic only if thinking.json yields nothing.
    """
    levels = derive_thinking_levels(provider_id, model_id, reasoning)
    if levels:
        return levels, get_default_effort(provider_id), get_model_variant(provider_id, model_id)

    # Fallback: old logic for providers without thinking.json
    if not reasoning:
        return [], None, None

    minimal = ["minimal"] if supports_minimal_effort(model_id) else []
    if supports_xhigh:
        levels = minimal + ["low", "medium", "high", "xhigh", "max"]
    else:
        levels = minimal + ["low", "medium", "high", "max"]

    default = "xhigh" if "xhigh" in levels else (
        "medium" if "medium" in levels else levels[len(levels) // 2]
    )
    return levels, default, None


def apply_thinking_fields(models: dict) -> None:
    """Fill thinking_levels / default_thinking_level / thinking_variant on each
    Model in `models`. Called once at module load (see models.py).

    Respects existing thinking_levels: if a model already declared exact
    levels (e.g. DeepSeek only 4), don't overwrite them with auto-generated
    defaults.
    """
    from .models import supports_xhigh

    for key, model in list(models.items()):
        if getattr(model, "thinking_levels", None):
            continue  # already declared exact levels
        levels, default, variant = derive_thinking_fields(
            model.provider, model.id, model.reasoning, supports_xhigh(model)
        )
        models[key] = model.model_copy(update={
            "thinking_levels": levels,
            "default_thinking_level": default,
            "thinking_variant": variant,
        })

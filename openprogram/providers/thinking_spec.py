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
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

_PROVIDERS_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def get_thinking_spec(provider_id: str) -> dict[str, Any]:
    """Load thinking.json for a provider. Returns empty dict if missing."""
    # provider_id uses hyphens (e.g. "openai-codex") but directory names
    # use underscores (e.g. "openai_codex"). Try both.
    for dir_name in (provider_id, provider_id.replace("-", "_")):
        path = _PROVIDERS_DIR / dir_name / "thinking.json"
        if path.is_file():
            try:
                with path.open(encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                return {}
    return {}


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
        emap = override.get("effort_map") or override.get("budget_map")
        if emap and level in emap:
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

    # Model override narrows the levels
    override = spec.get("model_overrides", {}).get(model_id)
    if override:
        emap = override.get("effort_map") or override.get("budget_map")
        if emap:
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

"""Thinking-capability derivation for Model objects.

Reads thinking.json via ``thinking_spec`` to determine each model's
thinking_levels / default_thinking_level / thinking_variant. Called at
module load by ``models.py`` and by dynamic fetchers.

Legacy: ``THINKING_OVERRIDES`` is kept empty for backward compatibility
with callers that import it. All override data now lives in each
provider's ``thinking.json`` under ``model_overrides``.
"""
from __future__ import annotations

# Empty — all data now in providers/<name>/thinking.json
THINKING_OVERRIDES: dict[str, dict] = {}


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

    Primary source: thinking.json via thinking_spec. Falls back to the
    old hardcoded logic only if thinking.json yields nothing.
    """
    from .thinking_spec import derive_thinking_levels, get_default_effort, get_model_variant

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


def apply_thinking_catalog(models: dict) -> None:
    """Fill thinking_levels / default_thinking_level / thinking_variant on each
    Model in `models`. Called once at module load (see models.py).

    Respects existing thinking_levels: if the catalog JSON already
    declared exact levels for a model (e.g. DeepSeek only 4), don't
    overwrite them with auto-generated defaults.
    """
    from .models import supports_xhigh

    for key, model in list(models.items()):
        if getattr(model, "thinking_levels", None):
            continue  # catalog already declared exact levels
        levels, default, variant = derive_thinking_fields(
            model.provider, model.id, model.reasoning, supports_xhigh(model)
        )
        models[key] = model.model_copy(update={
            "thinking_levels": levels,
            "default_thinking_level": default,
            "thinking_variant": variant,
        })

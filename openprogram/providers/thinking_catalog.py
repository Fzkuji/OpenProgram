"""
Thinking-capability overrides.

`models_generated.py` is a mirror of pi-ai's catalog and stays verbatim. The
overrides here layer our own thinking UX metadata on top:

  - thinking_levels        — which levels this model's picker should show
  - default_thinking_level — what "/think" resets to on model switch
  - thinking_variant       — LobeChat-style tag for models whose UX or
                             request body diverges from the default path

Only list models whose capability deviates from the defaults applied by
`apply_thinking_catalog()`:

  * reasoning=False         → no thinking menu
  * reasoning=True, xhigh   → ["minimal","low","medium","high","xhigh"], default "xhigh"
  * reasoning=True, other   → ["minimal","low","medium","high"], default "medium"

Key format matches `MODELS` keys: "{provider}/{model_id}".
"""
from __future__ import annotations

THINKING_OVERRIDES: dict[str, dict] = {
    # Anthropic Opus 4.7 — uses output_config.effort on the wire instead of
    # the usual thinking.budget_tokens path (Claude 4.6 guidance).
    "anthropic/claude-opus-4-7": {
        "thinking_levels": ["low", "medium", "high"],
        "default_thinking_level": "medium",
        "thinking_variant": "opus47",
    },
}


def derive_thinking_fields(
    provider_id: str,
    model_id: str,
    reasoning: bool,
    supports_xhigh: bool = False,
) -> tuple[list[str], str | None, str | None]:
    """Compute (thinking_levels, default_thinking_level, thinking_variant) for
    an arbitrary (provider, model_id) pair. Used for dynamically fetched
    models (/api/providers/<name>/fetch-models) that don't sit in the static
    `MODELS` registry. Applies the same overrides + defaults as
    `apply_thinking_catalog`.
    """
    key = f"{provider_id}/{model_id}"
    override = THINKING_OVERRIDES.get(key, {})
    levels = override.get("thinking_levels")
    default = override.get("default_thinking_level")
    variant = override.get("thinking_variant")

    if levels is None and reasoning:
        if supports_xhigh:
            levels = ["minimal", "low", "medium", "high", "xhigh"]
        else:
            levels = ["minimal", "low", "medium", "high"]
    if levels is None:
        levels = []
    if default is None and levels:
        default = "xhigh" if "xhigh" in levels else (
            "medium" if "medium" in levels else levels[len(levels) // 2]
        )
    return levels, default, variant


def apply_thinking_catalog(models: dict) -> None:
    """Fill thinking_levels / default_thinking_level / thinking_variant on each
    Model in `models`. Called once at module load (see models.py)."""
    from .models import supports_xhigh  # local import to avoid cycle

    for key, model in list(models.items()):
        levels, default, variant = derive_thinking_fields(
            model.provider, model.id, model.reasoning, supports_xhigh(model)
        )
        models[key] = model.model_copy(update={
            "thinking_levels": levels,
            "default_thinking_level": default,
            "thinking_variant": variant,
        })

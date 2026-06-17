"""Thinking / reasoning-effort picker config + runtime apply helpers.

Factored out of server.py so the provider-specific effort tables,
model-aware lookup, and runtime-apply shim can be reasoned about in
one place. server.py re-exports these for existing call sites.

The picker config drives the dropdown in the UI:
    GET /api/providers/models → { thinking: { label, options, default, variant } }
built from `_get_thinking_config_for_model(provider, model_id)`.

Runtime-side, after the UI sends an effort back via the WS chat
payload, we call `apply_thinking_effort(runtime, effort)` which
normalises the value (falls back to provider default if empty) and
threads it into either the subprocess flag path (Claude Code,
Codex CLI) or the unified API-level `runtime.thinking_level` knob.
"""
from __future__ import annotations


# Per-user defaults (explicit; do not revert without asking):
#   - Claude (claude-max, anthropic): auto / max available — adaptive thinking
#   - GPT (codex, openai): maximum effort — the user wants the strongest setting
THINKING_CONFIGS = {
    # claude-code now goes direct to api.anthropic.com (no Meridian proxy),
    # so reasoning_effort is honoured. Same levels as the anthropic provider.
    "claude-code": {
        "label": "thinking",
        "options": [
            {"value": "off", "desc": "No reasoning"},
            {"value": "minimal", "desc": "Brief reasoning"},
            {"value": "low", "desc": "Light reasoning"},
            {"value": "medium", "desc": "Standard reasoning"},
            {"value": "high", "desc": "Deep reasoning"},
            {"value": "xhigh", "desc": "Extended effort"},
            {"value": "max", "desc": "Maximum effort"},
        ],
        "default": "high",
    },
    "openai-codex": {
        "label": "reasoning effort",
        "options": [
            {"value": "off", "desc": "No reasoning"},
            {"value": "minimal", "desc": "Minimal reasoning"},
            {"value": "low", "desc": "Quick reasoning"},
            {"value": "medium", "desc": "Balanced"},
            {"value": "high", "desc": "Deep reasoning"},
            {"value": "xhigh", "desc": "Extended effort"},
            {"value": "max", "desc": "Maximum effort"},
        ],
        "default": "xhigh",
    },
    "anthropic": {
        "label": "thinking",
        "options": [
            {"value": "off", "desc": "No extended thinking"},
            {"value": "minimal", "desc": "Minimal thinking"},
            {"value": "low", "desc": "Brief thinking"},
            {"value": "medium", "desc": "Balanced"},
            {"value": "high", "desc": "Extended thinking"},
            {"value": "xhigh", "desc": "Extended effort"},
            {"value": "max", "desc": "Maximum effort"},
        ],
        "default": "high",
    },
    "openai": {
        "label": "reasoning effort",
        "options": [
            {"value": "off", "desc": "No reasoning"},
            {"value": "minimal", "desc": "Minimal reasoning"},
            {"value": "low", "desc": "Quick reasoning"},
            {"value": "medium", "desc": "Balanced"},
            {"value": "high", "desc": "Deep reasoning"},
            {"value": "xhigh", "desc": "Extended effort"},
            {"value": "max", "desc": "Maximum effort"},
        ],
        "default": "xhigh",
    },
    "gemini": {
        "label": "thinking",
        "options": [
            {"value": "off", "desc": "No thinking"},
            {"value": "minimal", "desc": "Minimal thinking"},
            {"value": "low", "desc": "Brief thinking"},
            {"value": "medium", "desc": "Balanced"},
            {"value": "high", "desc": "Extended thinking"},
            {"value": "auto", "desc": "Dynamic"},
        ],
        "default": "auto",
    },
}


# Short descriptions reused across providers when we build a per-model
# config from `Model.thinking_levels`.
_LEVEL_DESC = {
    "minimal": "Minimal reasoning",
    "low": "Quick reasoning",
    "medium": "Balanced",
    "high": "Deep reasoning",
    "xhigh": "Extended effort",
    "max": "Maximum effort",
}


# Runtime class name → provider id. Used to resolve a provider default
# without needing the live runtime's own provider attribute (which some
# runtime classes don't expose cleanly).
_RUNTIME_PROVIDER = {
    "ClaudeCodeRuntime": "claude-code",
    "OpenAICodexRuntime": "openai-codex",
    "AnthropicRuntime": "anthropic",
    "OpenAIRuntime": "openai",
    "GeminiRuntime": "gemini",
    "GeminiCLIRuntime": "gemini-subscription",
}


def get_thinking_config(provider: str) -> dict:
    """Static config for a provider. Falls back to openai-codex."""
    return THINKING_CONFIGS.get(provider, THINKING_CONFIGS.get("openai-codex"))


def get_thinking_config_for_model(provider: str, model_id: str | None) -> dict:
    """Build the thinking picker config for a specific model.

    Primary source: thinking.json via thinking_spec. Falls back to the
    static THINKING_CONFIGS for providers not yet migrated.
    """
    from openprogram.providers import get_model
    from openprogram.providers.thinking_spec import (
        derive_thinking_levels,
        get_default_effort,
        get_model_variant,
    )

    label = get_thinking_config(provider).get("label", "thinking")

    def _build(levels: list[str], default: str | None, variant: str | None) -> dict:
        values = ["off", *levels]
        return {
            "label": label,
            "options": [
                {"value": v, "desc": _LEVEL_DESC.get(v, "No reasoning" if v == "off" else v)}
                for v in values
            ],
            "default": default or (levels[len(levels) // 2] if levels else None),
            "variant": variant,
        }

    if model_id:
        # Check if model exists and whether it supports reasoning
        model = get_model(provider, model_id)
        reasoning = getattr(model, "reasoning", False) if model else False

        # Derive levels from thinking.json
        levels = derive_thinking_levels(provider, model_id, reasoning)
        if levels:
            return _build(
                levels,
                get_default_effort(provider),
                get_model_variant(provider, model_id),
            )

        # Model exists with reasoning=True but thinking.json yielded
        # nothing (provider has no thinking.json). Try Model object.
        if model is not None and getattr(model, "thinking_levels", None):
            return _build(
                list(model.thinking_levels),
                model.default_thinking_level,
                model.thinking_variant,
            )

        # Model found, reasoning=False → hide menu
        if model is not None and not reasoning:
            return {"label": label, "options": [], "default": None, "variant": None}

    return get_thinking_config(provider)


def default_effort_for(runtime) -> str:
    """Provider default thinking effort for a runtime class.

    Reads from thinking.json via thinking_spec. Falls back to the static
    THINKING_CONFIGS for providers not yet migrated to thinking.json.
    """
    provider = _RUNTIME_PROVIDER.get(type(runtime).__name__, "openai-codex")
    from openprogram.providers.thinking_spec import get_default_effort
    result = get_default_effort(provider)
    if result:
        return result
    return THINKING_CONFIGS.get(provider, {}).get("default")


def resolve_effort(effort, runtime) -> str:
    """Return ``effort`` if truthy, else the runtime's provider default."""
    return effort or default_effort_for(runtime)


def apply_thinking_effort(runtime, effort: str) -> None:
    """Push a normalized effort onto a live runtime.

    API-backed runtimes share the unified ``runtime.thinking_level``
    attribute (pi-ai ThinkingLevel: off/minimal/low/medium/high/xhigh), which
    flows into the provider's SimpleStreamOptions.reasoning — same
    abstraction opencode / pi-ai use. CLI subprocess runtimes still
    need provider-specific plumbing because their knobs are
    command-line flags, not request fields.
    """
    rt_type = type(runtime).__name__
    effort = resolve_effort(effort, runtime)

    # OpenAI Codex CLI subprocess runtime also reads _reasoning_effort
    # directly from the subclass attribute to build its
    # --reasoning-effort flag. Keep that plumbing for the subprocess
    # path.
    if rt_type in ("OpenAICodexRuntime", "OpenAICodexRuntime"):
        runtime._reasoning_effort = effort

    # Every Runtime (API + CLI subclasses) exposes the unified knob.
    # Setting it makes AgentSession-based API paths send
    # `reasoning=<level>` straight through to the provider.
    runtime.thinking_level = effort or "off"

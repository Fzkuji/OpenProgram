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
        ],
        "default": "medium",
    },
    "openai-codex": {
        "label": "reasoning effort",
        "options": [
            {"value": "off", "desc": "No reasoning"},
            {"value": "minimal", "desc": "Minimal reasoning"},
            {"value": "low", "desc": "Quick reasoning"},
            {"value": "medium", "desc": "Balanced"},
            {"value": "high", "desc": "Deep reasoning"},
            {"value": "xhigh", "desc": "Maximum effort"},
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
            {"value": "xhigh", "desc": "Maximum effort"},
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
    "xhigh": "Maximum effort",
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
    """Prefer the model's own ``thinking_levels`` if declared, else fall
    back to the provider's static config. Lets different models under
    the same provider expose different pickers (gpt-4o hides the menu,
    gpt-5 shows minimal/low/medium/high, Codex Max adds xhigh).
    """
    from openprogram.providers import get_model

    # claude-code hits api.anthropic.com directly (no proxy); its models
    # are anthropic models under a different provider key. Look up the
    # anthropic twin so thinking_levels from the anthropic catalog apply.
    if provider == "claude-code" and model_id:
        twin = get_model("anthropic", model_id)
        if twin and getattr(twin, "thinking_levels", None):
            return _build(
                list(twin.thinking_levels),
                twin.default_thinking_level,
                twin.thinking_variant,
            )

    def _build(levels: list[str], default: str | None, variant: str | None) -> dict:
        values = ["off", *levels]
        return {
            "label": get_thinking_config(provider).get("label", "thinking"),
            "options": [
                {"value": v, "desc": _LEVEL_DESC.get(v, "No reasoning" if v == "off" else v)}
                for v in values
            ],
            "default": default or (levels[len(levels) // 2] if levels else None),
            "variant": variant,
        }

    if model_id:
        model = get_model(provider, model_id)
        if model is not None and getattr(model, "thinking_levels", None):
            return _build(
                list(model.thinking_levels),
                model.default_thinking_level,
                model.thinking_variant,
            )
        # Model found but declares no thinking_levels → hide menu.
        if model is not None:
            return {
                "label": get_thinking_config(provider).get("label", "thinking"),
                "options": [],
                "default": None,
                "variant": None,
            }
        # Model NOT in the catalog (proxy / custom models like
        # `anthropic/claude-opus-4-7`, which only ever live in
        # `THINKING_OVERRIDES`). The static provider fallback would
        # drop the override's level set, default AND `thinking_variant`
        # — and a lost variant means the runtime picks the wrong wire
        # format. Consult THINKING_OVERRIDES directly so those models
        # still get their declared picker.
        from openprogram.providers.thinking_catalog import THINKING_OVERRIDES
        override = THINKING_OVERRIDES.get(f"{provider}/{model_id}")
        if override and override.get("thinking_levels"):
            return _build(
                list(override["thinking_levels"]),
                override.get("default_thinking_level"),
                override.get("thinking_variant"),
            )
    return get_thinking_config(provider)


def default_effort_for(runtime) -> str:
    """Provider default thinking effort for a runtime class.

    Used everywhere the legacy code used to hardcode "medium" as a
    fallback. Matches class name rather than duck-typing the provider
    because runtime instances don't uniformly expose it.
    """
    provider = _RUNTIME_PROVIDER.get(type(runtime).__name__, "openai-codex")
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

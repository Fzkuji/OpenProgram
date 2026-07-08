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


# UI label per provider — what the thinking picker is called in the UI.
# Everything else (options, default, mapping) comes from thinking.json.
THINKING_CONFIGS = {
    "claude-code": {"label": "thinking"},
    "openai-codex": {"label": "reasoning effort"},
    "anthropic": {"label": "thinking"},
    "openai": {"label": "reasoning effort"},
    "gemini": {"label": "thinking"},
    "google": {"label": "thinking"},
    "amazon-bedrock": {"label": "thinking"},
    "azure-openai-responses": {"label": "reasoning effort"},
    "github-copilot": {"label": "thinking"},
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

    Single source of truth: listing.list_models_for_provider, which
    already merges Fetch data, models.dev, thinking.json, and catalog
    into one consistent result. This function just reformats it for
    the UI picker.
    """
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
        # Enabled models: read thinking levels straight off the stored config
        # spec row — no live browse / network. Only fall through to the live
        # browse path for a model that isn't an enabled spec row (browse
        # context, where derivation stays as-is).
        from openprogram.webui._model_catalog.storage import _read_providers_cfg
        pcfg = _read_providers_cfg().get(provider, {})
        for row in (pcfg.get("models") or []):
            if row.get("id") == model_id:
                levels = row.get("thinking_levels") or []
                if levels:
                    return _build(
                        levels,
                        row.get("default_thinking_level"),
                        row.get("thinking_variant"),
                    )
                return {"label": label, "options": [], "default": None, "variant": None}

        from openprogram.webui._model_catalog.listing import list_models_for_provider
        for m in list_models_for_provider(provider):
            if m.get("id") == model_id:
                levels = m.get("thinking_levels") or []
                if levels:
                    return _build(
                        levels,
                        m.get("default_thinking_level"),
                        m.get("thinking_variant"),
                    )
                return {"label": label, "options": [], "default": None, "variant": None}

    # model_id not given or not found — provider-level fallback
    from openprogram.providers.thinking_spec import get_thinking_spec, get_default_effort
    spec = get_thinking_spec(provider)
    emap = spec.get("effort_map") or spec.get("budget_map")
    if emap:
        return _build(list(emap.keys()), get_default_effort(provider), None)
    return {"label": label, "options": [], "default": None, "variant": None}


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

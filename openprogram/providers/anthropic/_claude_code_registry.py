"""Register Claude Code CLI models into the global MODELS registry.

ClaudeCodeRuntime is a CLI-backed runtime — it doesn't ship with
catalog entries the way HTTP providers do. But the webui's model
picker reads from MODELS (via get_providers / get_models), and
list_enabled_models() only iterates HTTP-registered providers. Without
this registration the CLI provider is invisible in the dropdown even
though the runtime itself works fine.

Mirrors the pattern in openai_codex/runtime.py: inject provider-scoped
Model objects into the registry at import time. The authoritative
model list comes from claude_models.json (via the legacy seed in
openprogram.legacy_providers.claude_models).
"""
from __future__ import annotations


def _augment_registry_with_claude_code_models() -> None:
    from openprogram.providers.models_generated import MODELS
    from openprogram.providers.types import Model, ModelCost
    from openprogram.legacy_providers.claude_models import load_claude_models

    try:
        data = load_claude_models()
    except Exception:
        return

    for m in data.get("models", []):
        mid = m.get("id")
        if not mid:
            continue
        key = f"claude-code/{mid}"
        if key in MODELS:
            continue
        # Opus / Sonnet support extended thinking via the CLI; Haiku
        # does not. The CLI translates thinking_effort -> the right
        # flags, so we mark the capability here for the UI picker.
        family = (m.get("family") or "").lower()
        reasoning = family in ("opus", "sonnet")
        MODELS[key] = Model(
            id=mid,
            name=m.get("display") or mid,
            api="claude-code-cli",
            provider="claude-code",
            base_url="",
            context_window=int(m.get("context_window") or 200000),
            max_tokens=int(m.get("max_output") or 32000),
            input=["text", "image"],
            reasoning=reasoning,
            cost=ModelCost(),
        )


_augment_registry_with_claude_code_models()

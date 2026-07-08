"""Seed the claude-code provider into the global ENABLED_MODELS registry.

claude-code connects DIRECT to api.anthropic.com on a Claude subscription
(OAuth). It has no list-models call baked in like HTTP providers, but the
provider must still appear in the registry — ``get_providers()`` derives the
provider list from ENABLED_MODELS, so with zero entries the provider would vanish
from the settings UI entirely.

So we register a small, CURRENT seed (the live model list comes from a Fetch
against Anthropic's /v1/models, which replaces these in ``custom_models``).
The seed uses the SAME wire as the anthropic provider — ``anthropic-messages``
+ ``https://api.anthropic.com`` — NOT the retired Meridian proxy
(openai-completions / localhost:3456); the runtime maps a claude-code model
onto ``anthropic:<id>`` and the wire handles OAuth + 1M via beta headers.
"""
from __future__ import annotations


# (id, display, context_window, max_output, reasoning). Current主力 models;
# a Fetch refreshes/extends this from the live API.
_SEED = [
    ("claude-opus-4-8", "Claude Opus 4.8", 1_000_000, 128_000, True),
    ("claude-sonnet-4-6", "Claude Sonnet 4.6", 1_000_000, 128_000, True),
    ("claude-haiku-4-5", "Claude Haiku 4.5", 200_000, 64_000, False),
]


def _seed_claude_code_models() -> None:
    from openprogram.providers.enabled_models import ENABLED_MODELS
    from openprogram.providers.types import Model, ModelCost

    for mid, display, ctx, max_out, reasoning in _SEED:
        key = f"claude-code/{mid}"
        if key in ENABLED_MODELS:
            continue
        ENABLED_MODELS[key] = Model(
            id=mid,
            name=display,
            api="anthropic-messages",
            provider="claude-code",
            base_url="https://api.anthropic.com",
            context_window=ctx,
            max_tokens=max_out,
            input=["text", "image"],
            reasoning=reasoning,
            cost=ModelCost(),
        )


_seed_claude_code_models()

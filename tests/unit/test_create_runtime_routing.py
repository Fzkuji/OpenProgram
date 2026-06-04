"""create_runtime() supports api-routed providers, not just the 6 named ones.

The recurring agent confusion: someone greps for "where are providers
registered", lands on registry.PROVIDERS (6 entries), sees no minimax /
deepseek / etc., and concludes they're unsupported. PROVIDERS is only the
backends that need a bespoke Runtime CLASS (OAuth/CLI). Everything else is
supported via its model's wire `api` + the api_registry — the same path
chat uses. create_runtime now falls through to that, so it matches chat
coverage instead of raising "Unknown provider".
"""
from __future__ import annotations

import pytest

from openprogram.providers.registry import PROVIDERS, create_runtime


def test_providers_table_is_only_the_bespoke_runtime_classes():
    # If this set grows, that's fine — but it is NOT the list of supported
    # providers, which is models_generated + the api_registry.
    assert set(PROVIDERS) == {
        "claude-code", "openai-codex", "gemini-cli",
        "anthropic", "openai", "gemini",
    }


@pytest.mark.parametrize("provider,model,expected_prefix", [
    ("minimax-cn", "MiniMax-M2.5", "minimax-cn:MiniMax-M2.5"),  # anthropic-wire
    ("deepseek", "deepseek-chat", "deepseek:deepseek-chat"),    # openai-wire
])
def test_api_routed_provider_builds_runtime_not_error(provider, model, expected_prefix):
    # Must NOT raise "Unknown provider" — these aren't in PROVIDERS but are
    # supported through their model's api.
    rt = create_runtime(provider=provider, model=model)
    assert str(getattr(rt, "model", "")) == expected_prefix


def test_api_routed_provider_defaults_to_a_known_model():
    rt = create_runtime(provider="minimax-cn")
    assert str(getattr(rt, "model", "")).startswith("minimax-cn:")

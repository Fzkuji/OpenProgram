"""An unknown model id is answered with the nearest registered ones."""
from __future__ import annotations

import pytest

from openprogram.models.providers.catalog import suggestions
from openprogram.models.providers.types import Model


def _model(provider: str, model_id: str, name: str = "") -> Model:
    return Model(id=model_id, name=name or model_id, api="completion", provider=provider,
                 base_url="https://x.invalid")


CATALOG = [
    _model("openai-codex", "gpt-6-luna", "GPT-6 Luna"),
    _model("openai-codex", "gpt-6.1-sol", "GPT-6.1 Sol"),
    _model("anthropic", "claude-opus-5-5", "Claude Opus 5.5"),
    _model("anthropic", "claude-sonnet-5-5", "Claude Sonnet 5.5"),
    _model("deepseek", "deepseek-v4-pro", "DeepSeek V4 Pro"),
]


@pytest.fixture(autouse=True)
def _catalog(monkeypatch):
    monkeypatch.setattr(suggestions, "get_models", lambda: list(CATALOG))


@pytest.mark.parametrize("asked, provider, first", [
    ("gpt6 sol", "openai-codex", "openai-codex:gpt-6.1-sol"),
    ("opus 5.5", "", "anthropic:claude-opus-5-5"),
    ("Claude Sonnet", "anthropic", "anthropic:claude-sonnet-5-5"),
    ("deepseek-v4", "deepseek", "deepseek:deepseek-v4-pro"),
])
def test_the_nearest_comes_first(asked, provider, first):
    assert suggestions.closest_models(asked, provider=provider)[0] == first


def test_a_shared_version_or_provider_alone_is_no_match():
    assert suggestions.closest_models("5.5") == []
    assert suggestions.closest_models("anthropic") == []


def test_the_message_keeps_its_prefix_and_names_the_suggestions():
    message = suggestions.unknown_model_message("openai-codex", "gpt6-luna")
    assert message.startswith("Unknown model 'openai-codex':'gpt6-luna'; did you mean openai-codex:gpt-6-luna")
    assert suggestions.unknown_model_message("x", "zzz") == "Unknown model 'x':'zzz'"

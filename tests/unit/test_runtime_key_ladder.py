"""Legacy Runtime subclasses must resolve keys through the full ladder.

AnthropicRuntime / OpenAIRuntime / GeminiRuntime historically did a bare
``os.environ.get(...)`` in ``__init__``. A key pasted in Settings lives
in the AuthStore, so a "pure Settings" user (zero env vars) saw the
Settings page report VALID while constructing the runtime raised
"API key is required". These tests pin the ladder
(AuthStore → env → config.json) as the resolution path.
"""
from __future__ import annotations

import pytest


_ENV_VARS = [
    "ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN", "OPENAI_API_KEY",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY",
]


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # Blank the config.json leg too — only the (mocked) AuthStore counts.
    from openprogram.providers import env_api_keys
    monkeypatch.setattr(env_api_keys, "_config_api_keys", lambda: {})


def _store_returns(monkeypatch, value):
    import openprogram.auth.resolver as _resolver
    monkeypatch.setattr(
        _resolver, "resolve_store_api_key_sync", lambda *a, **k: value
    )


@pytest.mark.parametrize("cls_path", [
    ("openprogram.providers.anthropic.runtime", "AnthropicRuntime"),
    ("openprogram.providers.openai_responses.runtime", "OpenAIRuntime"),
    ("openprogram.providers.google.runtime", "GeminiRuntime"),
])
def test_runtime_constructs_from_settings_only_key(monkeypatch, cls_path):
    _store_returns(monkeypatch, "sk-from-settings")
    module, name = cls_path
    cls = getattr(__import__(module, fromlist=[name]), name)
    rt = cls()
    assert rt.api_key == "sk-from-settings"


@pytest.mark.parametrize("cls_path", [
    ("openprogram.providers.anthropic.runtime", "AnthropicRuntime"),
    ("openprogram.providers.openai_responses.runtime", "OpenAIRuntime"),
    ("openprogram.providers.google.runtime", "GeminiRuntime"),
])
def test_runtime_error_mentions_settings_when_no_key_anywhere(monkeypatch, cls_path):
    _store_returns(monkeypatch, None)
    module, name = cls_path
    cls = getattr(__import__(module, fromlist=[name]), name)
    with pytest.raises(ValueError, match="Settings"):
        cls()


def test_detect_provider_sees_settings_only_key(monkeypatch):
    """Step 5 of detect_provider (API-key probing) must use the ladder,
    so a Settings-pasted key makes its provider auto-detectable."""
    _store_returns(monkeypatch, "sk-from-settings")
    monkeypatch.setenv("AGENTIC_PROVIDER", "")
    from openprogram.providers import registry
    monkeypatch.setattr(registry, "_load_provider_config", lambda: None)
    monkeypatch.setattr(registry, "_detect_caller_env", lambda: None)
    monkeypatch.setattr(registry.shutil, "which", lambda _: None)
    provider, model = registry.detect_provider()
    assert provider == "anthropic"

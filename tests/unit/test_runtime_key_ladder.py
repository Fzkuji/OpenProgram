"""Legacy Runtime subclasses must resolve keys from the AuthStore.

AnthropicRuntime / OpenAIRuntime / GeminiRuntime historically did a bare
``os.environ.get(...)`` in ``__init__``. Keys live in the AuthStore only
now (settings UI / `openprogram providers login`), so a "pure Settings" user
must construct these runtimes fine, and env vars must stay inert.
"""
from __future__ import annotations

import importlib

import pytest


_ENV_VARS = [
    "ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN", "OPENAI_API_KEY",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY",
]

_RUNTIMES = [
    ("openprogram.providers.anthropic.runtime", "AnthropicRuntime"),
    ("openprogram.providers.openai_responses.runtime", "OpenAIRuntime"),
    ("openprogram.providers.google.runtime", "GeminiRuntime"),
]


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _enable_default_models(monkeypatch):
    # Each legacy Runtime subclass resolves its DEFAULT model from the (now
    # enabled-only) registry at construction. Enable each provider's default
    # so these AuthStore-key tests construct all three runtimes regardless of
    # the machine's real config.
    import openprogram.providers._config_read as cr
    import openprogram.providers.models as pm
    import openprogram.providers.enabled_models as mg
    monkeypatch.setattr(cr, "read_providers_config", lambda: {
        "google": {"models": [
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
        ]},
        "openai": {"models": [
            {"id": "gpt-4o", "name": "GPT-4o", "api": "openai-responses"},
        ]},
        "anthropic": {"models": [
            {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
        ]},
    })
    reg = mg._load()
    monkeypatch.setattr(mg, "ENABLED_MODELS", reg)
    monkeypatch.setattr(pm, "ENABLED_MODELS", reg)


def _store_returns(monkeypatch, value):
    # Two resolution entry points: ``resolve_store_api_key_sync`` (api-key
    # only — OpenAI/Gemini runtimes) and ``resolve_api_key_sync`` (unified,
    # includes subscription OAuth — AnthropicRuntime, so a Claude plan login
    # constructs the runtime too). Stub both so every parametrized runtime
    # sees the same "store has / hasn't a credential" outcome.
    import openprogram.auth.resolver as _resolver
    monkeypatch.setattr(
        _resolver, "resolve_store_api_key_sync", lambda *a, **k: value
    )
    monkeypatch.setattr(
        _resolver, "resolve_api_key_sync", lambda *a, **k: value
    )


@pytest.mark.parametrize("mod,cls", _RUNTIMES)
def test_settings_only_key_constructs_the_runtime(monkeypatch, mod, cls):
    """Key in the AuthStore, zero env vars → constructor succeeds."""
    _store_returns(monkeypatch, "sk-store-key")
    runtime_cls = getattr(importlib.import_module(mod), cls)
    rt = runtime_cls()
    assert rt is not None


@pytest.mark.parametrize("mod,cls", _RUNTIMES)
def test_no_store_key_raises_with_guidance(monkeypatch, mod, cls):
    """No key anywhere → a clear error pointing at Settings / the CLI."""
    _store_returns(monkeypatch, None)
    runtime_cls = getattr(importlib.import_module(mod), cls)
    with pytest.raises(ValueError, match="Settings"):
        runtime_cls()


@pytest.mark.parametrize("mod,cls", _RUNTIMES)
def test_env_var_alone_does_not_construct(monkeypatch, mod, cls):
    """Env keys are inert — store empty + env set must still raise."""
    _store_returns(monkeypatch, None)
    for var in _ENV_VARS:
        monkeypatch.setenv(var, "sk-env-key")
    runtime_cls = getattr(importlib.import_module(mod), cls)
    with pytest.raises(ValueError):
        runtime_cls()

"""The HTTP API-key providers must resolve keys from the AuthStore.

The retired per-provider Runtime shells historically did a bare
``os.environ.get(...)`` in ``__init__``. Keys live in the AuthStore only
now (settings UI / `openprogram providers login`), so a "pure Settings"
user must get a working runtime from ``create_runtime``, and env vars
must stay inert.
"""
from __future__ import annotations

import pytest

from openprogram.providers.registry import create_runtime


_ENV_VARS = [
    "ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN", "OPENAI_API_KEY",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY",
]

# (provider id, model namespace the built runtime streams under)
_HTTP_PROVIDERS = [
    ("anthropic", "anthropic"),
    ("openai", "openai"),
    ("gemini", "google"),
]


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _enable_default_models(monkeypatch):
    # create_runtime resolves each provider's DEFAULT model from the (now
    # enabled-only) registry at construction. Enable each provider's default
    # so these AuthStore-key tests build all three runtimes regardless of
    # the machine's real config.
    import openprogram.providers._config_read as cr
    import openprogram.providers.models as pm
    import openprogram.providers.enabled_models as mg
    monkeypatch.setattr(cr, "read_providers_config", lambda: {
        "google": {"models": [
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
        ]},
        "openai": {"models": [
            {"id": "gpt-4.1", "name": "GPT-4.1", "api": "openai-responses"},
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
    # only — the openai/google pools) and ``resolve_api_key_sync`` (unified,
    # includes subscription OAuth — the anthropic pool, so a Claude plan
    # login builds the runtime too). Stub both so every parametrized
    # provider sees the same "store has / hasn't a credential" outcome.
    import openprogram.auth.resolver as _resolver
    monkeypatch.setattr(
        _resolver, "resolve_store_api_key_sync", lambda *a, **k: value
    )
    monkeypatch.setattr(
        _resolver, "resolve_api_key_sync", lambda *a, **k: value
    )


@pytest.mark.parametrize("provider,namespace", _HTTP_PROVIDERS)
def test_settings_only_key_constructs_the_runtime(monkeypatch, provider, namespace):
    """Key in the AuthStore, zero env vars → construction succeeds."""
    _store_returns(monkeypatch, "sk-store-key")
    rt = create_runtime(provider=provider)
    assert rt is not None
    assert rt.api_key == "sk-store-key"
    assert rt.provider_id == namespace


@pytest.mark.parametrize("provider,namespace", _HTTP_PROVIDERS)
def test_no_store_key_raises_with_guidance(monkeypatch, provider, namespace):
    """No key anywhere → a clear error pointing at Settings / the CLI."""
    _store_returns(monkeypatch, None)
    with pytest.raises(ValueError, match="Settings"):
        create_runtime(provider=provider)


@pytest.mark.parametrize("provider,namespace", _HTTP_PROVIDERS)
def test_env_var_alone_does_not_construct(monkeypatch, provider, namespace):
    """Env keys are inert — store empty + env set must still raise."""
    _store_returns(monkeypatch, None)
    for var in _ENV_VARS:
        monkeypatch.setenv(var, "sk-env-key")
    with pytest.raises(ValueError):
        create_runtime(provider=provider)

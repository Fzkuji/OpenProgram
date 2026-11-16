"""Unit tests for provider key resolution in
``openprogram/models/providers/settings/env_api_keys.py``.

A provider's keys are its credentials: the keys added in Settings or with
``openprogram providers keys <provider> add``, and a read-only row for each of
the provider's own environment variables that is set. config.json is never
consulted. Bedrock/Vertex authenticate through the cloud SDK's chain (AWS
SigV4 / GCP ADC), so a satisfied chain yields the ``"<authenticated>"``
sentinel.
"""
from __future__ import annotations

import pytest

from openprogram.models.providers.settings import env_api_keys as ek

_AWS = [
    "AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    "AWS_BEARER_TOKEN_BEDROCK", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_WEB_IDENTITY_TOKEN_FILE",
]
_VERTEX = [
    "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_APPLICATION_CREDENTIALS",
]
def _all_key_names() -> list[str]:
    from openprogram.models.auth.login.login_method_registry import env_vars, listed_providers
    return sorted({n for p in listed_providers() for n in env_vars(p)} | {"ANTHROPIC_OAUTH_TOKEN"})


@pytest.fixture(autouse=True)
def _no_auth_store(monkeypatch):
    """Default the AuthStore step to empty so the developer's real
    ~/.openprogram/auth credentials can't leak into assertions. Tests
    that want a stored key monkeypatch resolve_store_api_key_sync
    themselves."""
    import openprogram.models.auth.resolver as _resolver
    monkeypatch.setattr(
        _resolver, "resolve_store_api_key_sync", lambda *a, **k: None
    )


@pytest.fixture
def env(monkeypatch):
    """Clear every key variable a provider reads."""
    for n in _all_key_names() + _AWS + _VERTEX:
        monkeypatch.delenv(n, raising=False)
    return monkeypatch


# env_vars_for (display labels / identifiers only)

def test_env_vars_for(monkeypatch):
    # This unit test checks the static labels. Keep the unknown-provider
    # fallback offline so a stale models.dev cache cannot start its refresh
    # thread during the unit runtime-boundary fixture.
    monkeypatch.setattr(
        "openprogram.models.providers.catalog.metadata._models_dev_info",
        lambda _provider_id: {},
    )
    assert ek.env_vars_for("google") == ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"]
    assert ek.env_vars_for("anthropic") == ["ANTHROPIC_API_KEY"]
    assert ek.env_vars_for("minimax-cn") == ["MINIMAX_CN_API_KEY", "MINIMAX_API_KEY"]
    assert ek.env_vars_for("kimi-coding") == ["KIMI_API_KEY", "MOONSHOT_API_KEY"]
    assert ek.env_vars_for("deepseek") == ["DEEPSEEK_API_KEY"]
    assert ek.env_vars_for("atlascloud") == ["ATLASCLOUD_API_KEY"]
    assert ek.env_vars_for("totally-unknown") == []


# resolve_provider_key: the provider's credentials

def test_store_key_resolves(env, monkeypatch):
    import openprogram.models.auth.resolver as _resolver
    monkeypatch.setattr(
        _resolver, "resolve_store_api_key_sync",
        lambda provider, *a, **k: "storekey" if provider == "deepseek" else None,
    )
    assert ek.resolve_provider_key("deepseek") == "storekey"
    assert ek.resolve_provider_key("openrouter") is None


@pytest.fixture
def real_store(tmp_path, monkeypatch):
    """The real resolver over an empty store (the autouse stub undone)."""
    from openprogram.models.auth.store import AuthStore, set_store_for_testing
    monkeypatch.undo()
    for n in _all_key_names() + _AWS + _VERTEX:
        monkeypatch.delenv(n, raising=False)
    set_store_for_testing(AuthStore(root=tmp_path / "state"))
    yield monkeypatch
    set_store_for_testing(None)


def test_a_set_variable_is_one_of_the_provider_keys(real_store):
    real_store.setenv("DEEPSEEK_API_KEY", "sk-env-key-00001")
    assert ek.resolve_provider_key("deepseek") == "sk-env-key-00001"
    assert ek.is_configured("deepseek") is True


def test_a_sign_in_token_variable_is_not_an_anthropic_key(real_store):
    real_store.setenv("ANTHROPIC_OAUTH_TOKEN", "oauth")
    assert ek.resolve_provider_key("anthropic") is None


def test_no_store_no_key(env):
    assert ek.resolve_provider_key("deepseek") is None
    assert ek.resolve_provider_key("openai") is None


# is_configured

def test_is_configured_follows_the_store(env, monkeypatch):
    assert ek.is_configured("deepseek") is False
    import openprogram.models.auth.resolver as _resolver
    monkeypatch.setattr(
        _resolver, "resolve_store_api_key_sync", lambda *a, **k: "k"
    )
    assert ek.is_configured("deepseek") is True


# Bedrock / Vertex cloud-credential chains

def test_bedrock_sentinel(env):
    assert ek.resolve_provider_key("amazon-bedrock") is None
    env.setenv("AWS_ACCESS_KEY_ID", "x")
    env.setenv("AWS_SECRET_ACCESS_KEY", "y")
    assert ek.resolve_provider_key("amazon-bedrock") == "<authenticated>"
    assert ek.is_configured("amazon-bedrock") is True


def test_vertex_needs_project_location_and_adc(env, tmp_path):
    assert ek.is_configured("google-vertex") is False
    env.setenv("GOOGLE_CLOUD_PROJECT", "p")
    env.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    adc = tmp_path / "adc.json"
    adc.write_text("{}")
    env.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(adc))
    assert ek.is_configured("google-vertex") is True


# provider_id_for_env_var (reverse label lookup)

@pytest.mark.parametrize("env_var,pid", [
    ("GEMINI_API_KEY", "google"),
    ("GOOGLE_API_KEY", "google"),
    ("GOOGLE_GENERATIVE_AI_API_KEY", "google"),
    ("ANTHROPIC_API_KEY", "anthropic"),
    ("ANTHROPIC_OAUTH_TOKEN", None),
    ("KIMI_API_KEY", "kimi"),
    ("OPENROUTER_API_KEY", "openrouter"),
    ("BRAVE_API_KEY", None),
    ("TOTALLY_UNKNOWN", None),
])
def test_provider_id_for_env_var(env_var, pid):
    assert ek.provider_id_for_env_var(env_var) == pid

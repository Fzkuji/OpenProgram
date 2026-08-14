"""Unit tests for provider key resolution in
``openprogram/providers/env_api_keys.py``.

LLM provider keys live in the AuthStore ONLY (settings UI "add a key" /
``openprogram providers login <provider> --api-key``). Environment variables
and config.json are never consulted. The single exception is the
Bedrock/Vertex cloud-credential chain (AWS SigV4 / GCP ADC) — no bearer
key exists for those, so a satisfied chain yields the
``"<authenticated>"`` sentinel.
"""
from __future__ import annotations

import pytest

from openprogram.providers import env_api_keys as ek

_AWS = [
    "AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    "AWS_BEARER_TOKEN_BEDROCK", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_WEB_IDENTITY_TOKEN_FILE",
]
_VERTEX = [
    "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_APPLICATION_CREDENTIALS",
]
_ALL_KEY_NAMES = sorted({n for names in ek._PROVIDER_ENV_VARS.values() for n in names})


@pytest.fixture(autouse=True)
def _no_auth_store(monkeypatch):
    """Default the AuthStore step to empty so the developer's real
    ~/.openprogram/auth credentials can't leak into assertions. Tests
    that want a stored key monkeypatch resolve_store_api_key_sync
    themselves."""
    import openprogram.auth.resolver as _resolver
    monkeypatch.setattr(
        _resolver, "resolve_store_api_key_sync", lambda *a, **k: None
    )


@pytest.fixture
def env(monkeypatch):
    """Clear every key-shaped env name we know — proves they are inert."""
    for n in _ALL_KEY_NAMES + _AWS + _VERTEX:
        monkeypatch.delenv(n, raising=False)
    return monkeypatch


# env_vars_for (display labels / identifiers only)

def test_env_vars_for():
    assert ek.env_vars_for("google") == ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"]
    assert ek.env_vars_for("anthropic") == ["ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"]
    assert ek.env_vars_for("minimax-cn") == ["MINIMAX_CN_API_KEY", "MINIMAX_API_KEY"]
    assert ek.env_vars_for("kimi-coding") == ["KIMI_API_KEY", "MOONSHOT_API_KEY"]
    assert ek.env_vars_for("deepseek") == ["DEEPSEEK_API_KEY"]
    assert ek.env_vars_for("totally-unknown") == []


# resolve_provider_key: AuthStore is the only key source

def test_store_key_resolves(env, monkeypatch):
    import openprogram.auth.resolver as _resolver
    monkeypatch.setattr(
        _resolver, "resolve_store_api_key_sync",
        lambda provider, *a, **k: "storekey" if provider == "deepseek" else None,
    )
    assert ek.resolve_provider_key("deepseek") == "storekey"
    assert ek.resolve_provider_key("openrouter") is None


def test_env_vars_are_inert(env):
    # A key sitting in the environment must NOT resolve — people repoint
    # vars like ANTHROPIC_API_KEY at other services (e.g. to run DeepSeek
    # inside Claude Code), so reading them produces wrong-provider keys.
    env.setenv("DEEPSEEK_API_KEY", "envkey")
    env.setenv("ANTHROPIC_API_KEY", "envkey2")
    env.setenv("ANTHROPIC_OAUTH_TOKEN", "oauth")
    assert ek.resolve_provider_key("deepseek") is None
    assert ek.resolve_provider_key("anthropic") is None


def test_no_store_no_key(env):
    assert ek.resolve_provider_key("deepseek") is None
    assert ek.resolve_provider_key("openai") is None


# is_configured

def test_is_configured_follows_the_store(env, monkeypatch):
    assert ek.is_configured("deepseek") is False
    import openprogram.auth.resolver as _resolver
    monkeypatch.setattr(
        _resolver, "resolve_store_api_key_sync", lambda *a, **k: "k"
    )
    assert ek.is_configured("deepseek") is True


def test_is_configured_ignores_env(env):
    env.setenv("DEEPSEEK_API_KEY", "k")
    assert ek.is_configured("deepseek") is False


# Bedrock / Vertex cloud-credential chains (the one sanctioned env use)

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
    ("ANTHROPIC_OAUTH_TOKEN", "anthropic"),
    ("KIMI_API_KEY", "kimi-coding"),
    ("OPENROUTER_API_KEY", "openrouter"),
    ("BRAVE_API_KEY", None),
    ("TOTALLY_UNKNOWN", None),
])
def test_provider_id_for_env_var(env_var, pid):
    assert ek.provider_id_for_env_var(env_var) == pid

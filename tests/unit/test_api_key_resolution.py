"""Unit tests for the canonical credential resolution in
``openprogram/providers/env_api_keys.py`` (audit #3, step 1).

env_vars_for / resolve_api_key / is_configured / provider_id_for_env_var are the
single source the runtime + webui converge on. Pin precedence, cloud-cred
handling, and the sentinel removal. LLM keys live in the AuthStore or env
vars only — config.json ``api_keys`` is no longer consulted (it remains the
storage for web-search / TTS keys, injected into the env at webui startup).
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
    """These tests pin the env layer; blank out the AuthStore step
    of get_env_api_key so the developer's real ~/.openprogram/auth
    credentials can't shadow the monkeypatched env vars."""
    import openprogram.auth.resolver as _resolver
    monkeypatch.setattr(
        _resolver, "resolve_store_api_key_sync", lambda *a, **k: None
    )


@pytest.fixture
def env(monkeypatch):
    """Isolate from the real environment: clear every name we touch."""
    for n in _ALL_KEY_NAMES + _AWS + _VERTEX:
        monkeypatch.delenv(n, raising=False)
    return monkeypatch


# ── env_vars_for ──────────────────────────────────────────────────────────────

def test_env_vars_for():
    assert ek.env_vars_for("google") == ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"]
    assert ek.env_vars_for("anthropic") == ["ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"]
    assert ek.env_vars_for("minimax-cn") == ["MINIMAX_CN_API_KEY", "MINIMAX_API_KEY"]
    assert ek.env_vars_for("kimi-coding") == ["KIMI_API_KEY", "MOONSHOT_API_KEY"]
    assert ek.env_vars_for("deepseek") == ["DEEPSEEK_API_KEY"]
    assert ek.env_vars_for("totally-unknown") == []


# ── resolve_api_key ───────────────────────────────────────────────────────────

def test_resolve_env_precedence_anthropic_oauth_wins(env):
    env.setenv("ANTHROPIC_API_KEY", "key1")
    assert ek.resolve_api_key("anthropic") == "key1"
    env.setenv("ANTHROPIC_OAUTH_TOKEN", "oauth1")
    assert ek.resolve_api_key("anthropic") == "oauth1"


def test_resolve_google_accepts_any_historical_name(env):
    env.setenv("GOOGLE_API_KEY", "gk")
    assert ek.resolve_api_key("google") == "gk"
    env.setenv("GEMINI_API_KEY", "gemk")          # higher precedence
    assert ek.resolve_api_key("google") == "gemk"


def test_resolve_no_config_fallback(env):
    # config.json api_keys is deliberately NOT consulted for LLM keys —
    # the AuthStore (handled a layer above) and env vars are the only
    # sources. With neither set, resolution is None.
    assert ek.resolve_api_key("deepseek") is None


def test_resolve_cloud_returns_none_never_sentinel(env):
    env.setenv("AWS_ACCESS_KEY_ID", "x")
    env.setenv("AWS_SECRET_ACCESS_KEY", "y")
    assert ek.resolve_api_key("amazon-bedrock") is None
    assert ek.resolve_api_key("google-vertex") is None


# ── is_configured (incl. cloud-cred chains) ───────────────────────────────────

def test_is_configured_key(env):
    assert ek.is_configured("deepseek") is False
    env.setenv("DEEPSEEK_API_KEY", "k")
    assert ek.is_configured("deepseek") is True


def test_is_configured_bedrock_chain(env):
    assert ek.is_configured("amazon-bedrock") is False
    env.setenv("AWS_ACCESS_KEY_ID", "x")
    env.setenv("AWS_SECRET_ACCESS_KEY", "y")
    assert ek.is_configured("amazon-bedrock") is True


def test_is_configured_vertex_needs_project_location_and_adc(env, tmp_path):
    assert ek.is_configured("google-vertex") is False
    env.setenv("GOOGLE_CLOUD_PROJECT", "p")
    env.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    adc = tmp_path / "adc.json"
    adc.write_text("{}")
    env.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(adc))
    assert ek.is_configured("google-vertex") is True


# ── provider_id_for_env_var (reverse) ─────────────────────────────────────────

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


# ── get_env_api_key (runtime path) ────────────────────────────────────────────

def test_get_env_api_key_authstore_wins(env, monkeypatch):
    # The AuthStore (where the settings UI saves pasted keys) is the first
    # rung of the runtime ladder — it beats the env var.
    env.setenv("DEEPSEEK_API_KEY", "envkey")
    import openprogram.auth.resolver as _resolver
    monkeypatch.setattr(
        _resolver, "resolve_store_api_key_sync", lambda *a, **k: "storekey"
    )
    assert ek.get_env_api_key("deepseek") == "storekey"


def test_get_env_api_key_anthropic_oauth_precedence(env):
    env.setenv("ANTHROPIC_API_KEY", "k")
    env.setenv("ANTHROPIC_OAUTH_TOKEN", "oauth")
    assert ek.get_env_api_key("anthropic") == "oauth"


def test_get_env_api_key_bedrock_sentinel_preserved(env):
    assert ek.get_env_api_key("amazon-bedrock") is None
    env.setenv("AWS_ACCESS_KEY_ID", "x")
    env.setenv("AWS_SECRET_ACCESS_KEY", "y")
    assert ek.get_env_api_key("amazon-bedrock") == "<authenticated>"

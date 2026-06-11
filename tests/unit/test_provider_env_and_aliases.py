from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.auth.aliases import known_aliases, resolve
from openprogram.providers import env_api_keys


@pytest.fixture(autouse=True)
def _no_auth_store(monkeypatch):
    """Blank out the AuthStore by default so the developer's real
    ~/.openprogram/auth credentials can't leak into assertions; tests
    that want a stored key re-monkeypatch it themselves."""
    import openprogram.auth.resolver as _resolver
    monkeypatch.setattr(
        _resolver, "resolve_store_api_key_sync", lambda *a, **k: None
    )


def test_resolve_maps_common_aliases_to_canonical_ids() -> None:
    assert resolve("codex") == "openai-codex"
    assert resolve("claude") == "anthropic"
    assert resolve("gemini") == "gemini-subscription"
    assert resolve("copilot") == "github-copilot"
    assert resolve("unknown-provider") == "unknown-provider"


def test_known_aliases_returns_copy() -> None:
    aliases = known_aliases()
    aliases["codex"] = "mutated"

    assert resolve("codex") == "openai-codex"


def test_resolve_provider_key_ignores_env_vars(monkeypatch) -> None:
    # Keys live in the AuthStore only. Shell vars — including ones other
    # tools repoint (ANTHROPIC_API_KEY aimed at DeepSeek, GH tokens) —
    # must never leak into provider auth.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-key")
    monkeypatch.setenv("ANTHROPIC_OAUTH_TOKEN", "oauth-token")
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "copilot-token")

    assert env_api_keys.resolve_provider_key("anthropic") is None
    assert env_api_keys.resolve_provider_key("github-copilot") is None


def test_resolve_provider_key_supports_google_vertex_adc_via_explicit_credentials(monkeypatch, tmp_path: Path) -> None:
    creds = tmp_path / "adc.json"
    creds.write_text("{}")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(creds))
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    assert env_api_keys.resolve_provider_key("google-vertex") == "<authenticated>"


def test_resolve_provider_key_supports_google_vertex_default_adc_path(monkeypatch, tmp_path: Path) -> None:
    adc_dir = tmp_path / ".config" / "gcloud"
    adc_dir.mkdir(parents=True)
    (adc_dir / "application_default_credentials.json").write_text("{}")
    monkeypatch.setattr(env_api_keys.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("GCLOUD_PROJECT", "demo-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "asia-east1")

    assert env_api_keys.resolve_provider_key("google-vertex") == "<authenticated>"


def test_resolve_provider_key_returns_none_when_google_vertex_context_is_incomplete(monkeypatch, tmp_path: Path) -> None:
    creds = tmp_path / "adc.json"
    creds.write_text("{}")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(creds))
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)

    assert env_api_keys.resolve_provider_key("google-vertex") is None


def test_resolve_provider_key_detects_bedrock_credentials(monkeypatch) -> None:
    monkeypatch.setenv("AWS_PROFILE", "default")

    assert env_api_keys.resolve_provider_key("amazon-bedrock") == "<authenticated>"


def test_resolve_provider_key_reads_the_auth_store(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")  # inert
    import openprogram.auth.resolver as _resolver
    monkeypatch.setattr(
        _resolver, "resolve_store_api_key_sync",
        lambda provider, *a, **k: "store-key" if provider == "openrouter" else None,
    )

    assert env_api_keys.resolve_provider_key("openrouter") == "store-key"

from __future__ import annotations

from pathlib import Path

from openprogram.auth.aliases import known_aliases, resolve
from openprogram.providers import env_api_keys


def test_resolve_maps_common_aliases_to_canonical_ids() -> None:
    assert resolve("codex") == "chatgpt-subscription"
    assert resolve("claude") == "anthropic"
    assert resolve("gemini") == "gemini-subscription"
    assert resolve("copilot") == "github-copilot"
    assert resolve("unknown-provider") == "unknown-provider"


def test_known_aliases_returns_copy() -> None:
    aliases = known_aliases()
    aliases["codex"] = "mutated"

    assert resolve("codex") == "chatgpt-subscription"


def test_get_env_api_key_prefers_anthropic_oauth_token(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-key")
    monkeypatch.setenv("ANTHROPIC_OAUTH_TOKEN", "oauth-token")

    assert env_api_keys.get_env_api_key("anthropic") == "oauth-token"


def test_get_env_api_key_uses_github_copilot_priority_order(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setenv("GH_TOKEN", "gh-token")
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "copilot-token")

    assert env_api_keys.get_env_api_key("github-copilot") == "copilot-token"


def test_get_env_api_key_supports_google_vertex_adc_via_explicit_credentials(monkeypatch, tmp_path: Path) -> None:
    creds = tmp_path / "adc.json"
    creds.write_text("{}")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(creds))
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    assert env_api_keys.get_env_api_key("google-vertex") == "<authenticated>"


def test_get_env_api_key_supports_google_vertex_default_adc_path(monkeypatch, tmp_path: Path) -> None:
    adc_dir = tmp_path / ".config" / "gcloud"
    adc_dir.mkdir(parents=True)
    (adc_dir / "application_default_credentials.json").write_text("{}")
    monkeypatch.setattr(env_api_keys.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("GCLOUD_PROJECT", "demo-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "asia-east1")

    assert env_api_keys.get_env_api_key("google-vertex") == "<authenticated>"


def test_get_env_api_key_returns_none_when_google_vertex_context_is_incomplete(monkeypatch, tmp_path: Path) -> None:
    creds = tmp_path / "adc.json"
    creds.write_text("{}")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(creds))
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)

    assert env_api_keys.get_env_api_key("google-vertex") is None


def test_get_env_api_key_detects_bedrock_credentials(monkeypatch) -> None:
    monkeypatch.setenv("AWS_PROFILE", "default")

    assert env_api_keys.get_env_api_key("amazon-bedrock") == "<authenticated>"


def test_get_env_api_key_reads_standard_provider_env_var(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")

    assert env_api_keys.get_env_api_key("openrouter") == "router-key"

"""Tests for provider auto-detection and lazy imports."""

import importlib
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """detect_provider() reads ~/.agentic/config.json which on a real dev
    box has a default_provider preset that defeats every env-only test
    here. Redirect get_config_path to a tmp file that doesn't exist so
    config-file lookup always misses."""
    monkeypatch.setattr(
        "openprogram.paths.get_config_path",
        lambda: str(tmp_path / "no-such-config.json"),
    )


class TestProviderDetection:
    """Tests for detect_provider() and create_runtime() wiring."""

    def test_detect_provider_prefers_explicit_env_config(self, monkeypatch):
        """AGENTIC_PROVIDER / AGENTIC_MODEL override CLI and API auto-detection."""
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude" if name == "claude" else None)
        monkeypatch.setenv("AGENTIC_PROVIDER", "openai")
        monkeypatch.setenv("AGENTIC_MODEL", "gpt-5.1-mini")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)

        from openprogram import providers
        importlib.reload(providers)

        assert providers.detect_provider() == ("openai", "gpt-5.1-mini")

    def test_detect_provider_uses_config_default_model_when_model_missing(self, monkeypatch):
        """AGENTIC_PROVIDER alone falls back to the registry default model."""
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.setenv("AGENTIC_PROVIDER", "anthropic")
        monkeypatch.delenv("AGENTIC_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)

        from openprogram import providers
        importlib.reload(providers)

        assert providers.detect_provider() == ("anthropic", "claude-sonnet-4-6")

    def test_detect_provider_accepts_google_generative_ai_api_key(self, monkeypatch):
        """Gemini API auto-detection accepts Google's alternate env var name."""
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.delenv("AGENTIC_PROVIDER", raising=False)
        monkeypatch.delenv("AGENTIC_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_GENERATIVE_AI_API_KEY", "fallback-key")

        from openprogram import providers
        importlib.reload(providers)

        assert providers.detect_provider() == ("gemini", "gemini-2.5-flash")

    def test_detect_provider_prefers_cli_before_api_keys(self, monkeypatch):
        """CLI providers should win over API keys during plain auto-detection."""
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/codex" if name == "codex" else None)
        monkeypatch.delenv("AGENTIC_PROVIDER", raising=False)
        monkeypatch.delenv("AGENTIC_MODEL", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
        monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)

        from openprogram import providers
        importlib.reload(providers)

        assert providers.detect_provider() == ("openai-codex", None)

    def test_check_providers_marks_env_selected_provider_default(self, monkeypatch):
        """check_providers() marks the configured provider as the auto-selected default."""
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.setenv("AGENTIC_PROVIDER", "gemini")
        monkeypatch.delenv("AGENTIC_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)

        from openprogram import providers
        importlib.reload(providers)

        statuses = providers.check_providers()
        assert statuses["gemini"]["default"] is True
        assert statuses["gemini"]["model"] == "gemini-2.5-flash"


class TestProviderLazyImport:
    """Test that providers/__init__.py lazy-loads correctly."""

    def test_unknown_attribute_raises(self):
        """Accessing unknown attribute raises AttributeError."""
        from openprogram import providers
        with pytest.raises(AttributeError, match="no attribute"):
            _ = providers.NonExistentRuntime

    def test_all_exports(self):
        """__all__ lists all providers."""
        from openprogram import providers
        assert "AnthropicRuntime" in providers.__all__
        assert "OpenAIRuntime" in providers.__all__
        assert "GeminiRuntime" in providers.__all__
        assert "ClaudeCodeRuntime" in providers.__all__
        assert "OpenAICodexRuntime" in providers.__all__
        assert "GeminiCLIRuntime" in providers.__all__

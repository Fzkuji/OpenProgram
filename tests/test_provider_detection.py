"""Tests for provider auto-detection and lazy imports."""

import importlib
import pytest
from unittest.mock import MagicMock, patch

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

        from agentic import providers
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

        from agentic import providers
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

        from agentic import providers
        importlib.reload(providers)

        assert providers.detect_provider() == ("gemini", "gemini-2.5-flash")

    def test_check_providers_marks_env_selected_provider_default(self, monkeypatch):
        """check_providers() marks the configured provider as the auto-selected default."""
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.setenv("AGENTIC_PROVIDER", "gemini")
        monkeypatch.delenv("AGENTIC_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)

        from agentic import providers
        importlib.reload(providers)

        statuses = providers.check_providers()
        assert statuses["gemini"]["default"] is True
        assert statuses["gemini"]["model"] == "gemini-2.5-flash"


class TestDetectCallerEnv:
    """Tests for _detect_caller_env() inside-agent detection."""

    def test_claude_code_env_detected(self, monkeypatch):
        """Running inside Claude Code is detected via env var + binary."""
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude" if name == "claude" else None)

        from agentic import providers
        importlib.reload(providers)

        result = providers._detect_caller_env()
        assert result == ("claude-code", "sonnet")

    def test_claude_code_entrypoint_detected(self, monkeypatch):
        """CLAUDE_CODE_ENTRYPOINT also triggers Claude Code detection."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "vscode")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude" if name == "claude" else None)

        from agentic import providers
        importlib.reload(providers)

        result = providers._detect_caller_env()
        assert result == ("claude-code", "sonnet")

    def test_claude_code_env_without_binary(self, monkeypatch):
        """Claude Code env vars without the binary → not detected."""
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setattr("shutil.which", lambda name: None)

        from agentic import providers
        importlib.reload(providers)

        result = providers._detect_caller_env()
        assert result is None

    def test_codex_env_detected(self, monkeypatch):
        """Running inside Codex CLI is detected."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
        monkeypatch.setenv("CODEX_CLI", "1")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/codex" if name == "codex" else None)

        from agentic import providers
        importlib.reload(providers)

        result = providers._detect_caller_env()
        assert result == ("codex", None)

    def test_no_caller_env(self, monkeypatch):
        """No agent environment detected → returns None."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
        monkeypatch.delenv("CODEX_CLI", raising=False)
        monkeypatch.delenv("CODEX_SANDBOX_TYPE", raising=False)

        from agentic import providers
        importlib.reload(providers)

        result = providers._detect_caller_env()
        assert result is None


class TestDetectProviderErrors:
    """Tests for detect_provider() error paths."""

    def test_no_providers_raises_runtime_error(self, monkeypatch):
        """detect_provider() raises RuntimeError when nothing is available."""
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.delenv("AGENTIC_PROVIDER", raising=False)
        monkeypatch.delenv("AGENTIC_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
        monkeypatch.delenv("CODEX_CLI", raising=False)
        monkeypatch.delenv("CODEX_SANDBOX_TYPE", raising=False)

        from agentic import providers
        importlib.reload(providers)

        with pytest.raises(RuntimeError, match="No LLM provider found"):
            providers.detect_provider()

    def test_cli_fallback_priority(self, monkeypatch):
        """CLI detection follows priority: claude > codex > gemini."""
        monkeypatch.delenv("AGENTIC_PROVIDER", raising=False)
        monkeypatch.delenv("AGENTIC_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
        monkeypatch.delenv("CODEX_CLI", raising=False)
        monkeypatch.delenv("CODEX_SANDBOX_TYPE", raising=False)

        # Only gemini CLI available
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gemini" if name == "gemini" else None)

        from agentic import providers
        importlib.reload(providers)

        assert providers.detect_provider() == ("gemini-cli", "gemini-2.5-flash")

    def test_api_key_fallback_priority(self, monkeypatch):
        """API key detection follows: anthropic > openai > gemini."""
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.delenv("AGENTIC_PROVIDER", raising=False)
        monkeypatch.delenv("AGENTIC_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
        monkeypatch.delenv("CODEX_CLI", raising=False)
        monkeypatch.delenv("CODEX_SANDBOX_TYPE", raising=False)

        # Only OpenAI key available
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        from agentic import providers
        importlib.reload(providers)

        assert providers.detect_provider() == ("openai", "gpt-4.1")


class TestCreateRuntime:
    """Tests for create_runtime() factory function."""

    def test_unknown_provider_raises(self, monkeypatch):
        """create_runtime() raises ValueError for unknown provider name."""
        from agentic import providers
        with pytest.raises(ValueError, match="Unknown provider.*nonexistent"):
            providers.create_runtime(provider="nonexistent")

    def test_explicit_provider_imports_module(self, monkeypatch):
        """create_runtime() imports and instantiates the requested provider."""
        # Mock the anthropic module so we don't need a real API key
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        import sys
        original = sys.modules.get("anthropic")
        sys.modules["anthropic"] = mock_anthropic

        # Clear cached import
        if "agentic.providers.anthropic" in sys.modules:
            del sys.modules["agentic.providers.anthropic"]

        try:
            from agentic import providers
            rt = providers.create_runtime(
                provider="anthropic",
                model="claude-haiku",
                api_key="test-key",
            )
            assert rt.model == "claude-haiku"
            assert hasattr(rt, 'client')
        finally:
            if original is not None:
                sys.modules["anthropic"] = original
            elif "anthropic" in sys.modules:
                del sys.modules["anthropic"]
            if "agentic.providers.anthropic" in sys.modules:
                del sys.modules["agentic.providers.anthropic"]

    def test_model_override(self, monkeypatch):
        """create_runtime() uses the model arg over the registry default."""
        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = MagicMock()

        import sys
        original = sys.modules.get("openai")
        sys.modules["openai"] = mock_openai

        if "agentic.providers.openai" in sys.modules:
            del sys.modules["agentic.providers.openai"]

        try:
            from agentic import providers
            rt = providers.create_runtime(
                provider="openai",
                model="gpt-5.4-turbo",
                api_key="test-key",
            )
            assert rt.model == "gpt-5.4-turbo"
        finally:
            if original is not None:
                sys.modules["openai"] = original
            elif "openai" in sys.modules:
                del sys.modules["openai"]
            if "agentic.providers.openai" in sys.modules:
                del sys.modules["agentic.providers.openai"]

    def test_default_model_from_registry(self, monkeypatch):
        """create_runtime() uses registry default model when model=None."""
        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = MagicMock()

        import sys
        original = sys.modules.get("openai")
        sys.modules["openai"] = mock_openai

        if "agentic.providers.openai" in sys.modules:
            del sys.modules["agentic.providers.openai"]

        try:
            from agentic import providers
            rt = providers.create_runtime(provider="openai", api_key="test-key")
            # Should use the registry default: "gpt-4.1"
            assert rt.model == "gpt-4.1"
        finally:
            if original is not None:
                sys.modules["openai"] = original
            elif "openai" in sys.modules:
                del sys.modules["openai"]
            if "agentic.providers.openai" in sys.modules:
                del sys.modules["agentic.providers.openai"]


class TestCheckProviders:
    """Tests for check_providers() status report."""

    def test_reports_all_providers(self, monkeypatch):
        """check_providers() reports status for all 6 providers."""
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.delenv("AGENTIC_PROVIDER", raising=False)
        monkeypatch.delenv("AGENTIC_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
        monkeypatch.delenv("CODEX_CLI", raising=False)
        monkeypatch.delenv("CODEX_SANDBOX_TYPE", raising=False)

        from agentic import providers
        importlib.reload(providers)

        statuses = providers.check_providers()
        assert len(statuses) == 6
        expected_names = {"claude-code", "codex", "gemini-cli", "anthropic", "openai", "gemini"}
        assert set(statuses.keys()) == expected_names
        # All should be unavailable
        for name, status in statuses.items():
            assert status["available"] is False

    def test_cli_and_api_methods(self, monkeypatch):
        """check_providers() correctly labels CLI vs API methods."""
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.delenv("AGENTIC_PROVIDER", raising=False)
        monkeypatch.delenv("AGENTIC_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
        monkeypatch.delenv("CODEX_CLI", raising=False)
        monkeypatch.delenv("CODEX_SANDBOX_TYPE", raising=False)

        from agentic import providers
        importlib.reload(providers)

        statuses = providers.check_providers()
        assert statuses["claude-code"]["method"] == "CLI"
        assert statuses["codex"]["method"] == "CLI"
        assert statuses["gemini-cli"]["method"] == "CLI"
        assert statuses["anthropic"]["method"] == "API"
        assert statuses["openai"]["method"] == "API"
        assert statuses["gemini"]["method"] == "API"


class TestProviderLazyImport:
    """Test that providers/__init__.py lazy-loads correctly."""

    def test_unknown_attribute_raises(self):
        """Accessing unknown attribute raises AttributeError."""
        from agentic import providers
        with pytest.raises(AttributeError, match="no attribute"):
            _ = providers.NonExistentRuntime

    def test_all_exports(self):
        """__all__ lists all providers and check_providers."""
        from agentic import providers
        assert "AnthropicRuntime" in providers.__all__
        assert "OpenAIRuntime" in providers.__all__
        assert "GeminiRuntime" in providers.__all__
        assert "ClaudeCodeRuntime" in providers.__all__
        assert "CodexRuntime" in providers.__all__
        assert "GeminiCLIRuntime" in providers.__all__
        assert "check_providers" in providers.__all__

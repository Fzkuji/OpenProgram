"""
Tests for built-in provider Runtimes (Anthropic, OpenAI, Gemini).

All tests use mocked SDKs — no real API calls are made.
"""

import base64
import os
import types
import pytest
from unittest.mock import MagicMock, patch, mock_open

from agentic import agentic_function
from agentic.runtime import Runtime


# ══════════════════════════════════════════════════════════════
# AnthropicRuntime tests
# ══════════════════════════════════════════════════════════════

class TestAnthropicRuntime:
    """Tests for AnthropicRuntime with mocked anthropic SDK."""

    @pytest.fixture(autouse=True)
    def setup_mock(self, monkeypatch):
        """Mock the anthropic module before importing AnthropicRuntime."""
        self.mock_anthropic = MagicMock()

        # Mock response
        mock_content_block = MagicMock()
        mock_content_block.text = "mock reply"
        mock_response = MagicMock()
        mock_response.content = [mock_content_block]
        self.mock_client = MagicMock()
        self.mock_client.messages.create.return_value = mock_response
        self.mock_anthropic.Anthropic.return_value = self.mock_client

        # Patch the import
        import sys
        self._original = sys.modules.get("anthropic")
        sys.modules["anthropic"] = self.mock_anthropic
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        # Force reimport
        import importlib
        if "agentic.providers.anthropic" in sys.modules:
            del sys.modules["agentic.providers.anthropic"]

        yield

        # Restore
        if self._original is not None:
            sys.modules["anthropic"] = self._original
        elif "anthropic" in sys.modules:
            del sys.modules["anthropic"]
        if "agentic.providers.anthropic" in sys.modules:
            del sys.modules["agentic.providers.anthropic"]

    def _make_runtime(self, **kwargs):
        from agentic.providers.anthropic import AnthropicRuntime
        return AnthropicRuntime(api_key="test-key", **kwargs)

    def test_text_block_conversion(self):
        """Text blocks are converted to Anthropic format."""
        rt = self._make_runtime()
        rt._call(
            [{"type": "text", "text": "hello"}],
            model="claude-sonnet-4-20250514",
        )
        call_kwargs = self.mock_client.messages.create.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "hello"

    def test_cache_control_injected_on_last_block(self):
        """cache_control is added to the last content block."""
        rt = self._make_runtime()
        rt._call(
            [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
            ],
            model="claude-sonnet-4-20250514",
        )
        call_kwargs = self.mock_client.messages.create.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        assert "cache_control" not in content[0]
        assert content[1]["cache_control"] == {"type": "ephemeral"}

    def test_cache_control_passthrough(self):
        """User-provided cache_control on text blocks is preserved."""
        rt = self._make_runtime()
        rt._call(
            [{"type": "text", "text": "cached", "cache_control": {"type": "ephemeral"}}],
            model="claude-sonnet-4-20250514",
        )
        call_kwargs = self.mock_client.messages.create.call_args[1]
        # The block should have cache_control (from both user and auto-injection)
        content = call_kwargs["messages"][0]["content"]
        assert content[0]["cache_control"] == {"type": "ephemeral"}

    def test_image_from_base64(self):
        """Image block with data is converted to base64 source."""
        rt = self._make_runtime()
        rt._call(
            [{"type": "image", "data": "abc123", "media_type": "image/jpeg"}],
            model="claude-sonnet-4-20250514",
        )
        call_kwargs = self.mock_client.messages.create.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["type"] == "base64"
        assert content[0]["source"]["data"] == "abc123"
        assert content[0]["source"]["media_type"] == "image/jpeg"

    def test_image_from_url(self):
        """Image block with url is converted to URL source."""
        rt = self._make_runtime()
        rt._call(
            [{"type": "image", "url": "https://example.com/img.png"}],
            model="claude-sonnet-4-20250514",
        )
        call_kwargs = self.mock_client.messages.create.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["type"] == "url"

    def test_image_from_file(self, tmp_path):
        """Image block with path reads and base64-encodes the file."""
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)

        rt = self._make_runtime()
        rt._call(
            [{"type": "image", "path": str(img_path)}],
            model="claude-sonnet-4-20250514",
        )
        call_kwargs = self.mock_client.messages.create.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["type"] == "base64"
        # Should be valid base64
        decoded = base64.b64decode(content[0]["source"]["data"])
        assert decoded[:4] == b"\x89PNG"

    def test_system_prompt_with_cache(self):
        """System prompt with cache_system=True adds cache_control."""
        rt = self._make_runtime(system="You are a helper.", cache_system=True)
        rt._call(
            [{"type": "text", "text": "hello"}],
            model="claude-sonnet-4-20250514",
        )
        call_kwargs = self.mock_client.messages.create.call_args[1]
        system = call_kwargs["system"]
        assert isinstance(system, list)
        assert system[0]["cache_control"] == {"type": "ephemeral"}

    def test_system_prompt_without_cache(self):
        """System prompt with cache_system=False passes string directly."""
        rt = self._make_runtime(system="You are a helper.", cache_system=False)
        rt._call(
            [{"type": "text", "text": "hello"}],
            model="claude-sonnet-4-20250514",
        )
        call_kwargs = self.mock_client.messages.create.call_args[1]
        assert call_kwargs["system"] == "You are a helper."

    def test_unknown_block_with_text_fallback(self):
        """Unknown block types with 'text' key fall back to text."""
        rt = self._make_runtime()
        rt._call(
            [{"type": "custom", "text": "fallback text"}],
            model="claude-sonnet-4-20250514",
        )
        call_kwargs = self.mock_client.messages.create.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "fallback text"

    def test_unknown_block_without_text_skipped(self):
        """Unknown block types without 'text' key are skipped."""
        rt = self._make_runtime()
        rt._call(
            [
                {"type": "text", "text": "keep"},
                {"type": "custom_no_text", "data": "skip"},
            ],
            model="claude-sonnet-4-20250514",
        )
        call_kwargs = self.mock_client.messages.create.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        # Only the text block should be present
        assert len(content) == 1
        assert content[0]["text"] == "keep"

    def test_api_error_propagates(self):
        """Anthropic API errors propagate through exec() retry."""
        self.mock_client.messages.create.side_effect = Exception("API rate limit")
        rt = self._make_runtime()

        @agentic_function
        def failing():
            """Test."""
            return rt.exec(content=[{"type": "text", "text": "test"}])

        with pytest.raises(RuntimeError, match="failed after"):
            failing()

    def test_no_api_key_raises(self, monkeypatch):
        """Missing API key raises ValueError."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from agentic.providers.anthropic import AnthropicRuntime
        with pytest.raises(ValueError, match="API key"):
            AnthropicRuntime(api_key=None)

    def test_model_default_and_override(self):
        """Default model is used, and per-call override works."""
        rt = self._make_runtime(model="claude-haiku")
        rt._call([{"type": "text", "text": "hi"}])
        call_kwargs = self.mock_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-haiku"

        rt._call([{"type": "text", "text": "hi"}], model="claude-opus")
        call_kwargs = self.mock_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-opus"


# ══════════════════════════════════════════════════════════════
# OpenAIRuntime tests
# ══════════════════════════════════════════════════════════════

class TestOpenAIRuntime:
    """Tests for OpenAIRuntime with mocked openai SDK."""

    @pytest.fixture(autouse=True)
    def setup_mock(self, monkeypatch):
        """Mock the openai module."""
        self.mock_openai = MagicMock()

        # Mock response
        mock_message = MagicMock()
        mock_message.content = "mock reply"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        self.mock_client = MagicMock()
        self.mock_client.chat.completions.create.return_value = mock_response
        self.mock_openai.OpenAI.return_value = self.mock_client

        import sys
        self._original = sys.modules.get("openai")
        sys.modules["openai"] = self.mock_openai
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        if "agentic.providers.openai" in sys.modules:
            del sys.modules["agentic.providers.openai"]

        yield

        if self._original is not None:
            sys.modules["openai"] = self._original
        elif "openai" in sys.modules:
            del sys.modules["openai"]
        if "agentic.providers.openai" in sys.modules:
            del sys.modules["agentic.providers.openai"]

    def _make_runtime(self, **kwargs):
        from agentic.providers.openai import OpenAIRuntime
        return OpenAIRuntime(api_key="test-key", **kwargs)

    def test_text_block_conversion(self):
        """Text blocks are converted to OpenAI format."""
        rt = self._make_runtime()
        rt._call([{"type": "text", "text": "hello"}])
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        user_msg = call_kwargs["messages"][-1]
        assert user_msg["content"][0] == {"type": "text", "text": "hello"}

    def test_image_from_url(self):
        """Image block with url → image_url format."""
        rt = self._make_runtime()
        rt._call([{"type": "image", "url": "https://example.com/img.png"}])
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        content = call_kwargs["messages"][-1]["content"]
        assert content[0]["type"] == "image_url"
        assert content[0]["image_url"]["url"] == "https://example.com/img.png"

    def test_image_from_base64(self):
        """Image block with data → data URL format."""
        rt = self._make_runtime()
        rt._call([{"type": "image", "data": "abc123", "media_type": "image/jpeg"}])
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        content = call_kwargs["messages"][-1]["content"]
        assert content[0]["type"] == "image_url"
        assert "data:image/jpeg;base64,abc123" in content[0]["image_url"]["url"]

    def test_image_from_file(self, tmp_path):
        """Image block with path → read + base64 encode."""
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)

        rt = self._make_runtime()
        rt._call([{"type": "image", "path": str(img_path)}])
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        content = call_kwargs["messages"][-1]["content"]
        assert content[0]["type"] == "image_url"
        assert "data:image/jpeg;base64," in content[0]["image_url"]["url"]

    def test_system_prompt(self):
        """System prompt is sent as a system message."""
        rt = self._make_runtime(system="You are helpful.")
        rt._call([{"type": "text", "text": "hello"}])
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        messages = call_kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are helpful."

    def test_response_format_passed(self):
        """response_format is passed through to the API."""
        rt = self._make_runtime()
        schema = {"type": "json_object"}
        rt._call([{"type": "text", "text": "test"}], response_format=schema)
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["response_format"] == schema

    def test_temperature(self):
        """Temperature is passed when set."""
        rt = self._make_runtime(temperature=0.5)
        rt._call([{"type": "text", "text": "test"}])
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["temperature"] == 0.5

    def test_no_temperature_by_default(self):
        """Temperature is not passed when not set."""
        rt = self._make_runtime()
        rt._call([{"type": "text", "text": "test"}])
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        assert "temperature" not in call_kwargs

    def test_base_url(self):
        """base_url is passed to OpenAI client."""
        rt = self._make_runtime(base_url="https://custom.api.com")
        # Verify OpenAI was called with base_url
        call_args = self.mock_openai.OpenAI.call_args
        assert call_args[1].get("base_url") == "https://custom.api.com"

    def test_no_api_key_raises(self, monkeypatch):
        """Missing API key raises ValueError."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from agentic.providers.openai import OpenAIRuntime
        with pytest.raises(ValueError, match="API key"):
            OpenAIRuntime(api_key=None)

    def test_model_default_and_override(self):
        """Default model and per-call override."""
        rt = self._make_runtime(model="gpt-4o-mini")
        rt._call([{"type": "text", "text": "hi"}])
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-4o-mini"

        rt._call([{"type": "text", "text": "hi"}], model="gpt-4o")
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-4o"

    def test_api_error_propagates(self):
        """API errors propagate through exec()."""
        self.mock_client.chat.completions.create.side_effect = Exception("quota exceeded")
        rt = self._make_runtime()

        @agentic_function
        def failing():
            """Test."""
            return rt.exec(content=[{"type": "text", "text": "test"}])

        with pytest.raises(RuntimeError, match="failed after"):
            failing()


# ══════════════════════════════════════════════════════════════
# GeminiRuntime tests
# ══════════════════════════════════════════════════════════════

class TestGeminiRuntime:
    """Tests for GeminiRuntime with mocked google-genai SDK."""

    @pytest.fixture(autouse=True)
    def setup_mock(self, monkeypatch):
        """Mock the google.genai module."""
        # Create mock module structure: google.genai and google.genai.types
        self.mock_types = MagicMock()
        self.mock_genai = MagicMock()
        self.mock_genai.types = self.mock_types
        self.mock_google = MagicMock()
        self.mock_google.genai = self.mock_genai

        # Mock response
        mock_response = MagicMock()
        mock_response.text = "mock reply"
        self.mock_client = MagicMock()
        self.mock_client.models.generate_content.return_value = mock_response
        self.mock_genai.Client.return_value = self.mock_client

        # Mock types.Part
        self.mock_types.Part.from_text.side_effect = lambda text: {"_mock": "text", "text": text}
        self.mock_types.Part.from_bytes.side_effect = lambda data, mime_type: {"_mock": "bytes", "mime_type": mime_type}
        self.mock_types.GenerateContentConfig.return_value = MagicMock()

        import sys
        self._originals = {}
        for mod in ["google", "google.genai", "google.genai.types"]:
            self._originals[mod] = sys.modules.get(mod)

        sys.modules["google"] = self.mock_google
        sys.modules["google.genai"] = self.mock_genai
        sys.modules["google.genai.types"] = self.mock_types

        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

        if "agentic.providers.gemini" in sys.modules:
            del sys.modules["agentic.providers.gemini"]

        yield

        for mod, orig in self._originals.items():
            if orig is not None:
                sys.modules[mod] = orig
            elif mod in sys.modules:
                del sys.modules[mod]
        if "agentic.providers.gemini" in sys.modules:
            del sys.modules["agentic.providers.gemini"]

    def _make_runtime(self, **kwargs):
        from agentic.providers.gemini import GeminiRuntime
        return GeminiRuntime(api_key="test-key", **kwargs)

    def test_text_block_conversion(self):
        """Text blocks → types.Part.from_text()."""
        rt = self._make_runtime()
        rt._call([{"type": "text", "text": "hello"}])
        self.mock_types.Part.from_text.assert_called_with(text="hello")

    def test_image_from_base64(self):
        """Image with data → types.Part.from_bytes()."""
        rt = self._make_runtime()
        rt._call([{"type": "image", "data": base64.b64encode(b"imgdata").decode(), "media_type": "image/png"}])
        self.mock_types.Part.from_bytes.assert_called_once()
        call_kwargs = self.mock_types.Part.from_bytes.call_args[1]
        assert call_kwargs["mime_type"] == "image/png"

    def test_image_from_file(self, tmp_path):
        """Image with path → read file + from_bytes()."""
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"\x89PNG" + b"\x00" * 10)

        rt = self._make_runtime()
        rt._call([{"type": "image", "path": str(img_path)}])
        self.mock_types.Part.from_bytes.assert_called_once()

    def test_system_instruction(self):
        """System instruction is set on config."""
        rt = self._make_runtime(system_instruction="Be helpful.")
        rt._call([{"type": "text", "text": "hello"}])
        config = self.mock_client.models.generate_content.call_args[1]["config"]
        assert config.system_instruction == "Be helpful."

    def test_response_format_json(self):
        """response_format with schema sets response_mime_type."""
        rt = self._make_runtime()
        schema = {"schema": {"type": "object"}}
        rt._call([{"type": "text", "text": "test"}], response_format=schema)
        config_call = self.mock_types.GenerateContentConfig.call_args[1]
        assert config_call["response_mime_type"] == "application/json"

    def test_no_api_key_raises(self, monkeypatch):
        """Missing API key raises ValueError."""
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        from agentic.providers.gemini import GeminiRuntime
        with pytest.raises(ValueError, match="API key"):
            GeminiRuntime(api_key=None)

    def test_model_default_and_override(self):
        """Default model and per-call override."""
        rt = self._make_runtime(model="gemini-2.5-flash")
        rt._call([{"type": "text", "text": "hi"}])
        call_kwargs = self.mock_client.models.generate_content.call_args[1]
        assert call_kwargs["model"] == "gemini-2.5-flash"

        rt._call([{"type": "text", "text": "hi"}], model="gemini-2.5-pro")
        call_kwargs = self.mock_client.models.generate_content.call_args[1]
        assert call_kwargs["model"] == "gemini-2.5-pro"

    def test_api_error_propagates(self):
        """API errors propagate through exec()."""
        self.mock_client.models.generate_content.side_effect = Exception("quota exceeded")
        rt = self._make_runtime()

        @agentic_function
        def failing():
            """Test."""
            return rt.exec(content=[{"type": "text", "text": "test"}])

        with pytest.raises(RuntimeError, match="failed after"):
            failing()

    def test_temperature(self):
        """Temperature is passed to config."""
        rt = self._make_runtime(temperature=0.7)
        rt._call([{"type": "text", "text": "hi"}])
        config_call = self.mock_types.GenerateContentConfig.call_args[1]
        assert config_call["temperature"] == 0.7


# ══════════════════════════════════════════════════════════════
# Provider lazy import tests
# ══════════════════════════════════════════════════════════════

class TestProviderLazyImport:
    """Test that providers/__init__.py lazy-loads correctly."""

    def test_unknown_attribute_raises(self):
        """Accessing unknown attribute raises AttributeError."""
        from agentic import providers
        with pytest.raises(AttributeError, match="no attribute"):
            _ = providers.NonExistentRuntime

    def test_all_exports(self):
        """__all__ lists the three providers."""
        from agentic import providers
        assert "AnthropicRuntime" in providers.__all__
        assert "OpenAIRuntime" in providers.__all__
        assert "GeminiRuntime" in providers.__all__

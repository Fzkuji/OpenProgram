"""Tests for AnthropicRuntime with mocked anthropic SDK."""

import base64
import importlib
import types
import pytest
from unittest.mock import MagicMock

from openprogram import agentic_function
from openprogram.agentic_programming.runtime import Runtime

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
        if "openprogram.legacy_providers.anthropic" in sys.modules:
            del sys.modules["openprogram.legacy_providers.anthropic"]

        yield

        # Restore
        if self._original is not None:
            sys.modules["anthropic"] = self._original
        elif "anthropic" in sys.modules:
            del sys.modules["anthropic"]
        if "openprogram.legacy_providers.anthropic" in sys.modules:
            del sys.modules["openprogram.legacy_providers.anthropic"]

    def _make_runtime(self, **kwargs):
        from openprogram.legacy_providers.anthropic import AnthropicRuntime
        return AnthropicRuntime(api_key="test-key", **kwargs)

    def test_text_block_conversion(self):
        """Text blocks are converted to Anthropic format."""
        rt = self._make_runtime()
        rt._call(
            [{"type": "text", "text": "hello"}],
            model="claude-sonnet-4-6",
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
            model="claude-sonnet-4-6",
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
            model="claude-sonnet-4-6",
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
            model="claude-sonnet-4-6",
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
            model="claude-sonnet-4-6",
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
            model="claude-sonnet-4-6",
        )
        call_kwargs = self.mock_client.messages.create.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["type"] == "base64"
        # Should be valid base64
        decoded = base64.b64decode(content[0]["source"]["data"])
        assert decoded[:4] == b"\x89PNG"

    def test_response_format_appended_as_json_instruction(self):
        """response_format is appended as a JSON-only text instruction."""
        rt = self._make_runtime()
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}

        rt._call(
            [{"type": "text", "text": "Return JSON"}],
            model="claude-sonnet-4-6",
            response_format=schema,
        )

        call_kwargs = self.mock_client.messages.create.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        assert len(content) == 2
        assert content[-1]["type"] == "text"
        assert "ONLY valid JSON" in content[-1]["text"]
        assert '"answer"' in content[-1]["text"]
        assert content[-1]["cache_control"] == {"type": "ephemeral"}

    def test_system_prompt_with_cache(self):
        """System prompt with cache_system=True adds cache_control."""
        rt = self._make_runtime(system="You are a helper.", cache_system=True)
        rt._call(
            [{"type": "text", "text": "hello"}],
            model="claude-sonnet-4-6",
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
            model="claude-sonnet-4-6",
        )
        call_kwargs = self.mock_client.messages.create.call_args[1]
        assert call_kwargs["system"] == "You are a helper."

    def test_unknown_block_with_text_fallback(self):
        """Unknown block types with 'text' key fall back to text."""
        rt = self._make_runtime()
        rt._call(
            [{"type": "custom", "text": "fallback text"}],
            model="claude-sonnet-4-6",
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
            model="claude-sonnet-4-6",
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
        from openprogram.legacy_providers.anthropic import AnthropicRuntime
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

    def test_file_pdf_from_base64(self):
        """File block with data is converted to Anthropic document type."""
        rt = self._make_runtime()
        rt._call(
            [{"type": "file", "data": "abc123", "mime_type": "application/pdf"}],
            model="claude-sonnet-4-6",
        )
        call_kwargs = self.mock_client.messages.create.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        assert content[0]["type"] == "document"
        assert content[0]["source"]["type"] == "base64"
        assert content[0]["source"]["data"] == "abc123"
        assert content[0]["source"]["media_type"] == "application/pdf"

    def test_file_pdf_from_path(self, tmp_path):
        """File block with path reads and base64-encodes the PDF."""
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4" + b"\x00" * 10)

        rt = self._make_runtime()
        rt._call(
            [{"type": "file", "path": str(pdf_path)}],
            model="claude-sonnet-4-6",
        )
        call_kwargs = self.mock_client.messages.create.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        assert content[0]["type"] == "document"
        assert content[0]["source"]["type"] == "base64"
        assert content[0]["source"]["media_type"] == "application/pdf"

    def test_audio_block_warns(self):
        """Audio blocks emit a warning and are skipped."""
        rt = self._make_runtime()
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            rt._call(
                [
                    {"type": "text", "text": "hello"},
                    {"type": "audio", "path": "test.wav"},
                ],
                model="claude-sonnet-4-6",
            )
            audio_warnings = [x for x in w if "audio" in str(x.message).lower()]
            assert len(audio_warnings) == 1
        call_kwargs = self.mock_client.messages.create.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"

    def test_video_block_warns(self):
        """Video blocks emit a warning and are skipped."""
        rt = self._make_runtime()
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            rt._call(
                [
                    {"type": "text", "text": "hello"},
                    {"type": "video", "path": "test.mp4"},
                ],
                model="claude-sonnet-4-6",
            )
            video_warnings = [x for x in w if "video" in str(x.message).lower()]
            assert len(video_warnings) == 1
        call_kwargs = self.mock_client.messages.create.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"


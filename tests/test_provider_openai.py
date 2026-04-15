"""Tests for OpenAIRuntime with mocked openai SDK."""

import base64
import json
import pytest
from unittest.mock import MagicMock

from agentic import agentic_function
from agentic.runtime import Runtime

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

    def test_audio_from_base64(self):
        """Audio block with data → input_audio format."""
        rt = self._make_runtime()
        rt._call([{"type": "audio", "data": "audiodata123", "format": "wav"}])
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        content = call_kwargs["messages"][-1]["content"]
        assert content[0]["type"] == "input_audio"
        assert content[0]["input_audio"]["data"] == "audiodata123"
        assert content[0]["input_audio"]["format"] == "wav"

    def test_audio_from_file(self, tmp_path):
        """Audio block with path → read + base64 + input_audio."""
        audio_path = tmp_path / "test.wav"
        audio_path.write_bytes(b"RIFF" + b"\x00" * 10)

        rt = self._make_runtime()
        rt._call([{"type": "audio", "path": str(audio_path)}])
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        content = call_kwargs["messages"][-1]["content"]
        assert content[0]["type"] == "input_audio"
        assert content[0]["input_audio"]["format"] == "wav"

    def test_audio_mp3_format(self, tmp_path):
        """Audio with .mp3 extension uses mp3 format."""
        audio_path = tmp_path / "test.mp3"
        audio_path.write_bytes(b"\xff\xfb" + b"\x00" * 10)

        rt = self._make_runtime()
        rt._call([{"type": "audio", "path": str(audio_path)}])
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        content = call_kwargs["messages"][-1]["content"]
        assert content[0]["input_audio"]["format"] == "mp3"

    def test_file_pdf_from_base64(self):
        """File block with data → OpenAI file format."""
        rt = self._make_runtime()
        rt._call([{"type": "file", "data": "pdfdata123", "mime_type": "application/pdf"}])
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        content = call_kwargs["messages"][-1]["content"]
        assert content[0]["type"] == "file"
        assert "data:application/pdf;base64,pdfdata123" in content[0]["file"]["file_data"]

    def test_file_pdf_from_path(self, tmp_path):
        """File block with path → read + base64 + file format."""
        pdf_path = tmp_path / "doc.pdf"
        pdf_path.write_bytes(b"%PDF-1.4" + b"\x00" * 10)

        rt = self._make_runtime()
        rt._call([{"type": "file", "path": str(pdf_path)}])
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        content = call_kwargs["messages"][-1]["content"]
        assert content[0]["type"] == "file"
        assert content[0]["file"]["filename"] == "doc.pdf"
        assert "data:application/pdf;base64," in content[0]["file"]["file_data"]

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
            )
            video_warnings = [x for x in w if "video" in str(x.message).lower()]
            assert len(video_warnings) == 1
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        content = call_kwargs["messages"][-1]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"

    def test_usage_is_normalized(self):
        """Usage metadata keeps a consistent shape across providers."""
        usage = MagicMock()
        usage.prompt_tokens = 123
        usage.completion_tokens = 45
        usage.prompt_tokens_details = MagicMock(cached_tokens=23)
        response = self.mock_client.chat.completions.create.return_value
        response.usage = usage

        rt = self._make_runtime()
        rt._call([{"type": "text", "text": "hello"}])

        assert rt.last_usage == {
            "input_tokens": 123,
            "output_tokens": 45,
            "cache_read": 23,
            "cache_create": 0,
        }

    def test_list_models_success(self):
        """list_models() returns sorted GPT/o-series model IDs."""
        m1 = MagicMock(); m1.id = "gpt-4o"
        m2 = MagicMock(); m2.id = "o4-mini"
        m3 = MagicMock(); m3.id = "dall-e-3"  # should be filtered out
        self.mock_client.models.list.return_value = [m1, m2, m3]

        rt = self._make_runtime()
        models = rt.list_models()
        assert "gpt-4o" in models
        assert "o4-mini" in models
        assert "dall-e-3" not in models

    def test_list_models_fallback(self):
        """list_models() returns fallback list on error."""
        self.mock_client.models.list.side_effect = Exception("API error")
        rt = self._make_runtime()
        models = rt.list_models()
        assert "gpt-4o" in models



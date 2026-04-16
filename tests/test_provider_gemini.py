"""Tests for GeminiRuntime with mocked google-genai SDK."""

import base64
import json
import types
import pytest
from unittest.mock import MagicMock

from agentic import agentic_function
from agentic.runtime import Runtime

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
        assert config_call["response_schema"] == {"type": "object"}

    def test_response_format_plain_schema(self):
        """Plain schema dicts are also forwarded as Gemini response_schema."""
        rt = self._make_runtime()
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        rt._call([{"type": "text", "text": "test"}], response_format=schema)
        config_call = self.mock_types.GenerateContentConfig.call_args[1]
        assert config_call["response_mime_type"] == "application/json"
        assert config_call["response_schema"] == schema

    def test_fallback_env_var_google_generative_ai_api_key(self, monkeypatch):
        """GOOGLE_GENERATIVE_AI_API_KEY is accepted for Gemini API compatibility."""
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_GENERATIVE_AI_API_KEY", "fallback-key")
        from agentic.providers.gemini import GeminiRuntime

        rt = GeminiRuntime(api_key=None)
        assert rt.client == self.mock_client
        self.mock_genai.Client.assert_called_with(api_key="fallback-key")

    def test_no_api_key_raises(self, monkeypatch):
        """Missing API key raises ValueError."""
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
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

    def test_audio_from_base64(self):
        """Audio with data → types.Part.from_bytes() with audio mime."""
        rt = self._make_runtime()
        rt._call([{"type": "audio", "data": base64.b64encode(b"audiodata").decode(), "media_type": "audio/wav"}])
        self.mock_types.Part.from_bytes.assert_called_once()
        call_kwargs = self.mock_types.Part.from_bytes.call_args[1]
        assert call_kwargs["mime_type"] == "audio/wav"

    def test_audio_from_file(self, tmp_path):
        """Audio with path → read file + from_bytes()."""
        audio_path = tmp_path / "test.wav"
        audio_path.write_bytes(b"RIFF" + b"\x00" * 10)

        rt = self._make_runtime()
        rt._call([{"type": "audio", "path": str(audio_path)}])
        self.mock_types.Part.from_bytes.assert_called_once()
        call_kwargs = self.mock_types.Part.from_bytes.call_args[1]
        assert "audio" in call_kwargs["mime_type"]

    def test_video_from_base64(self):
        """Video with data → types.Part.from_bytes() with video mime."""
        rt = self._make_runtime()
        rt._call([{"type": "video", "data": base64.b64encode(b"videodata").decode(), "media_type": "video/mp4"}])
        self.mock_types.Part.from_bytes.assert_called_once()
        call_kwargs = self.mock_types.Part.from_bytes.call_args[1]
        assert call_kwargs["mime_type"] == "video/mp4"

    def test_video_from_file(self, tmp_path):
        """Video with path → read file + from_bytes()."""
        video_path = tmp_path / "test.mp4"
        video_path.write_bytes(b"\x00\x00\x00\x1cftyp" + b"\x00" * 10)

        rt = self._make_runtime()
        rt._call([{"type": "video", "path": str(video_path)}])
        self.mock_types.Part.from_bytes.assert_called_once()
        call_kwargs = self.mock_types.Part.from_bytes.call_args[1]
        assert "video" in call_kwargs["mime_type"]

    def test_file_pdf_from_base64(self):
        """File/PDF with data → types.Part.from_bytes() with pdf mime."""
        rt = self._make_runtime()
        rt._call([{"type": "file", "data": base64.b64encode(b"pdfdata").decode(), "mime_type": "application/pdf"}])
        self.mock_types.Part.from_bytes.assert_called_once()
        call_kwargs = self.mock_types.Part.from_bytes.call_args[1]
        assert call_kwargs["mime_type"] == "application/pdf"

    def test_file_pdf_from_path(self, tmp_path):
        """File/PDF with path → read file + from_bytes()."""
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4" + b"\x00" * 10)

        rt = self._make_runtime()
        rt._call([{"type": "file", "path": str(pdf_path)}])
        self.mock_types.Part.from_bytes.assert_called_once()
        call_kwargs = self.mock_types.Part.from_bytes.call_args[1]
        assert call_kwargs["mime_type"] == "application/pdf"

    def test_usage_is_normalized(self):
        """Usage metadata keeps a consistent shape across providers."""
        usage = MagicMock()
        usage.prompt_token_count = 77
        usage.candidates_token_count = 19
        response = self.mock_client.models.generate_content.return_value
        response.usage_metadata = usage

        rt = self._make_runtime()
        rt._call([{"type": "text", "text": "hello"}])

        assert rt.last_usage == {
            "input_tokens": 77,
            "output_tokens": 19,
            "cache_read": 0,
            "cache_create": 0,
        }

    def test_list_models_returns_generate_content_models(self):
        """list_models() returns models that support generateContent."""
        model_flash = MagicMock()
        model_flash.name = "models/gemini-2.5-flash"
        model_flash.supported_generation_methods = ["generateContent", "countTokens"]
        model_embed = MagicMock()
        model_embed.name = "models/text-embedding-004"
        model_embed.supported_generation_methods = ["embedContent"]
        self.mock_client.models.list.return_value = [model_flash, model_embed]

        rt = self._make_runtime()
        models = rt.list_models()
        assert models == ["gemini-2.5-flash"]
        assert "text-embedding-004" not in models

    def test_list_models_strips_models_prefix(self):
        """list_models() strips the 'models/' prefix from model names."""
        model = MagicMock()
        model.name = "models/gemini-2.5-pro"
        model.supported_generation_methods = ["generateContent"]
        self.mock_client.models.list.return_value = [model]

        rt = self._make_runtime()
        models = rt.list_models()
        assert models == ["gemini-2.5-pro"]

    def test_list_models_fallback_on_error(self):
        """list_models() returns hardcoded fallback when API fails."""
        self.mock_client.models.list.side_effect = Exception("auth error")

        rt = self._make_runtime()
        models = rt.list_models()
        assert "gemini-2.5-flash" in models


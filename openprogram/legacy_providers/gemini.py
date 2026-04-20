"""
GeminiRuntime — Runtime subclass for Google Gemini API.

Supports:
    - Text and image (file / base64 / URL) content blocks
    - Audio content blocks (wav, mp3, etc.)
    - Video content blocks (mp4, webm, etc.)
    - PDF/document content blocks
    - System instructions
    - Max tokens configuration
    - Safety settings

Requires: pip install google-genai

Usage:
    from openprogram.legacy_providers import GeminiRuntime

    rt = GeminiRuntime(api_key="...", model="gemini-2.5-flash")

    @agentic_function
    def analyze(task):
        '''Analyze the given task.'''
        return rt.exec(content=[
            {"type": "text", "text": f"Analyze: {task}"},
        ])
"""

from __future__ import annotations

import base64
import mimetypes
import os
from typing import Optional

from openprogram.agentic_programming.runtime import Runtime

try:
    from google import genai
    from google.genai import types
except ImportError:
    raise ImportError(
        "GeminiRuntime requires the 'google-genai' package.\n"
        "Install it with: pip install google-genai"
    )


class GeminiRuntime(Runtime):
    """
    Runtime implementation for Google Gemini.

    Args:
        api_key:            Google AI API key. If None, reads from GOOGLE_API_KEY or
                            GOOGLE_GENERATIVE_AI_API_KEY env vars.
        model:              Default model name (e.g. "gemini-2.5-flash").
        max_output_tokens:  Maximum tokens in the response (default: 4096).
        system_instruction: System instruction. If provided, sent as system_instruction.
        temperature:        Sampling temperature (default: None, uses API default).
        max_retries:        Maximum number of exec() attempts before raising.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-2.5-flash",
        max_output_tokens: int = 4096,
        system_instruction: Optional[str] = None,
        temperature: Optional[float] = None,
        max_retries: int = 2,
    ):
        super().__init__(model=model, max_retries=max_retries)
        self.max_output_tokens = max_output_tokens
        self.system_instruction = system_instruction
        self.temperature = temperature

        api_key = (
            api_key
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY")
        )
        if not api_key:
            raise ValueError(
                "Google API key is required. Pass api_key= or set GOOGLE_API_KEY "
                "(or GOOGLE_GENERATIVE_AI_API_KEY) env var."
            )
        self.client = genai.Client(api_key=api_key)

    def list_models(self) -> list[str]:
        """Return available Gemini models that support generateContent."""
        try:
            models = self.client.models.list()
            result = []
            for m in models:
                methods = getattr(m, "supported_generation_methods", None) or []
                if "generateContent" in methods:
                    name = m.name
                    if name.startswith("models/"):
                        name = name[len("models/"):]
                    result.append(name)
            return sorted(result)
        except Exception:
            return ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]

    def _call(
        self,
        content: list[dict],
        model: str = "default",
        response_format: Optional[dict] = None,
    ) -> str:
        """
        Call Google Gemini API.

        Content blocks are converted to Gemini's format:
            {"type": "text", "text": "..."}
                → types.Part.from_text("...")

            {"type": "image", "path": "screenshot.png"}
                → types.Part.from_bytes(data=..., mime_type=...)

            {"type": "image", "data": "<base64>", "media_type": "image/png"}
                → types.Part.from_bytes(data=..., mime_type=...)
        """
        parts = []
        for block in content:
            converted = self._convert_block(block)
            if converted:
                parts.append(converted)

        use_model = model if model != "default" else self.model

        # Build generation config
        config_kwargs = {
            "max_output_tokens": self.max_output_tokens,
        }
        if self.temperature is not None:
            config_kwargs["temperature"] = self.temperature
        if response_format is not None:
            config_kwargs["response_mime_type"] = "application/json"
            if isinstance(response_format, dict):
                # Accept either {"schema": {...}} or a raw schema dict.
                # This keeps Gemini aligned with the other runtimes, which
                # already accept plain schema dictionaries directly.
                config_kwargs["response_schema"] = response_format.get("schema", response_format)

        # Thinking budget — maps UI effort to Gemini 2.5's ThinkingConfig.
        # 0 = disabled, -1 = dynamic (auto). Only meaningful on thinking-capable
        # Gemini 2.5+ models; silently ignored on older models by the SDK.
        effort = getattr(self, "_thinking_effort", None)
        budget_map = {"off": 0, "low": 1024, "medium": 4096, "high": 10000, "auto": -1}
        budget = budget_map.get(effort) if effort else None
        if budget is not None and hasattr(types, "ThinkingConfig"):
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)

        config = types.GenerateContentConfig(
            **config_kwargs,
        )

        if self.system_instruction:
            config.system_instruction = self.system_instruction

        response = self.client.models.generate_content(
            model=use_model,
            contents=parts,
            config=config,
        )
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            u = response.usage_metadata
            self.last_usage = {
                "input_tokens": getattr(u, 'prompt_token_count', 0),
                "output_tokens": getattr(u, 'candidates_token_count', 0),
                "cache_read": 0,
                "cache_create": 0,
            }
        return response.text

    def _convert_block(self, block: dict) -> Optional[object]:
        """Convert a generic content block to Gemini Part."""
        block_type = block.get("type", "text")

        if block_type == "text":
            return types.Part.from_text(text=block["text"])

        if block_type == "image":
            # Image from base64 data
            if "data" in block:
                media_type = block.get("media_type", "image/png")
                data = base64.b64decode(block["data"])
                return types.Part.from_bytes(data=data, mime_type=media_type)

            # Image from file path
            if "path" in block:
                path = block["path"]
                media_type = mimetypes.guess_type(path)[0] or "image/png"
                with open(path, "rb") as f:
                    data = f.read()
                return types.Part.from_bytes(data=data, mime_type=media_type)

            # Image from URL — download and send as bytes
            if "url" in block:
                import urllib.request
                url = block["url"]
                with urllib.request.urlopen(url) as resp:
                    data = resp.read()
                    media_type = resp.headers.get_content_type() or "image/png"
                return types.Part.from_bytes(data=data, mime_type=media_type)

        if block_type == "audio":
            # Gemini natively supports audio input (wav, mp3, aac, flac, ogg, etc.)
            if "data" in block:
                media_type = block.get("media_type", "audio/wav")
                data = base64.b64decode(block["data"])
                return types.Part.from_bytes(data=data, mime_type=media_type)

            if "path" in block:
                path = block["path"]
                media_type = mimetypes.guess_type(path)[0] or "audio/wav"
                with open(path, "rb") as f:
                    data = f.read()
                return types.Part.from_bytes(data=data, mime_type=media_type)

        if block_type == "video":
            # Gemini natively supports video input (mp4, webm, mov, avi, etc.)
            if "data" in block:
                media_type = block.get("media_type", "video/mp4")
                data = base64.b64decode(block["data"])
                return types.Part.from_bytes(data=data, mime_type=media_type)

            if "path" in block:
                path = block["path"]
                media_type = mimetypes.guess_type(path)[0] or "video/mp4"
                with open(path, "rb") as f:
                    data = f.read()
                return types.Part.from_bytes(data=data, mime_type=media_type)

        if block_type == "file":
            # PDF/document support — Gemini supports PDF natively
            mime_type = block.get("mime_type", "application/pdf")

            if "data" in block:
                data = base64.b64decode(block["data"])
                return types.Part.from_bytes(data=data, mime_type=mime_type)

            if "path" in block:
                path = block["path"]
                detected_mime = mimetypes.guess_type(path)[0] or mime_type
                with open(path, "rb") as f:
                    data = f.read()
                return types.Part.from_bytes(data=data, mime_type=detected_mime)

        # Unknown block type — pass as text
        if "text" in block:
            return types.Part.from_text(text=block["text"])

        return None

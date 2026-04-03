"""
GeminiRuntime — Runtime subclass for Google Gemini API.

Supports:
    - Text and image (file / base64 / URL) content blocks
    - System instructions
    - Max tokens configuration
    - Safety settings

Requires: pip install google-genai

Usage:
    from agentic.providers import GeminiRuntime

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

from agentic.runtime import Runtime

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
        api_key:            Google AI API key. If None, reads from GOOGLE_API_KEY env var.
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

        api_key = api_key or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError(
                "Google API key is required. Pass api_key= or set GOOGLE_API_KEY env var."
            )
        self.client = genai.Client(api_key=api_key)

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
            if isinstance(response_format, dict) and "schema" in response_format:
                config_kwargs["response_schema"] = response_format["schema"]

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

        # Unknown block type — pass as text
        if "text" in block:
            return types.Part.from_text(text=block["text"])

        return None

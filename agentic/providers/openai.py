"""
OpenAIRuntime — Runtime subclass for OpenAI API.

Supports:
    - Text and image (base64 / URL) content blocks
    - response_format (JSON mode / structured output)
    - System prompts
    - Max tokens configuration

Requires: pip install openai

Usage:
    from agentic.providers import OpenAIRuntime

    rt = OpenAIRuntime(api_key="sk-...", model="gpt-4o")

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
    import openai
except ImportError:
    raise ImportError(
        "OpenAIRuntime requires the 'openai' package.\n"
        "Install it with: pip install openai"
    )


class OpenAIRuntime(Runtime):
    """
    Runtime implementation for OpenAI GPT models.

    Args:
        api_key:        OpenAI API key. If None, reads from OPENAI_API_KEY env var.
        model:          Default model name (e.g. "gpt-4o").
        max_tokens:     Maximum tokens in the response (default: 4096).
        system:         System prompt. If provided, sent as a system message.
        temperature:    Sampling temperature (default: None, uses API default).
        max_retries:    Maximum number of exec() attempts before raising.
        base_url:       Override base URL (for Azure, local servers, etc.).
        **client_kwargs: Additional kwargs passed to openai.OpenAI().
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o",
        max_tokens: int = 4096,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        max_retries: int = 2,
        base_url: Optional[str] = None,
        **client_kwargs,
    ):
        super().__init__(model=model, max_retries=max_retries)
        self.max_tokens = max_tokens
        self.system = system
        self.temperature = temperature

        api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OpenAI API key is required. Pass api_key= or set OPENAI_API_KEY env var."
            )

        client_kwargs_final = {}
        if base_url:
            client_kwargs_final["base_url"] = base_url
        client_kwargs_final.update(client_kwargs)

        self.client = openai.OpenAI(api_key=api_key, **client_kwargs_final)

    def _call(
        self,
        content: list[dict],
        model: str = "default",
        response_format: Optional[dict] = None,
    ) -> str:
        """
        Call OpenAI API.

        Content blocks are converted to OpenAI's format:
            {"type": "text", "text": "..."}
                → {"type": "text", "text": "..."}

            {"type": "image", "path": "screenshot.png"}
                → {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}

            {"type": "image", "url": "https://..."}
                → {"type": "image_url", "image_url": {"url": "https://..."}}

            {"type": "image", "data": "<base64>", "media_type": "image/png"}
                → {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
        """
        messages = []

        # System message
        if self.system:
            messages.append({"role": "system", "content": self.system})

        # User message with content blocks
        user_content = []
        for block in content:
            converted = self._convert_block(block)
            if converted:
                user_content.append(converted)

        messages.append({"role": "user", "content": user_content})

        kwargs = {
            "model": model if model != "default" else self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }

        if self.temperature is not None:
            kwargs["temperature"] = self.temperature

        if response_format is not None:
            kwargs["response_format"] = response_format

        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    def _convert_block(self, block: dict) -> Optional[dict]:
        """Convert a generic content block to OpenAI format."""
        block_type = block.get("type", "text")

        if block_type == "text":
            return {"type": "text", "text": block["text"]}

        if block_type == "image":
            # Image from URL
            if "url" in block:
                return {
                    "type": "image_url",
                    "image_url": {"url": block["url"]},
                }

            # Image from base64 data
            if "data" in block:
                media_type = block.get("media_type", "image/png")
                data_url = f"data:{media_type};base64,{block['data']}"
                return {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                }

            # Image from file path
            if "path" in block:
                path = block["path"]
                media_type = mimetypes.guess_type(path)[0] or "image/png"
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("utf-8")
                data_url = f"data:{media_type};base64,{data}"
                return {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                }

        # Unknown block type — pass text representation
        if "text" in block:
            return {"type": "text", "text": block["text"]}

        return None

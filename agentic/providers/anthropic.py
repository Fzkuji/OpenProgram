"""
AnthropicRuntime — Runtime subclass for Anthropic Claude API.

Supports:
    - Text and image content blocks
    - Prompt caching via cache_control
    - System prompts
    - Max tokens configuration

Requires: pip install anthropic

Usage:
    from agentic.providers import AnthropicRuntime

    rt = AnthropicRuntime(api_key="sk-...", model="claude-sonnet-4-20250514")

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
    import anthropic
except ImportError:
    raise ImportError(
        "AnthropicRuntime requires the 'anthropic' package.\n"
        "Install it with: pip install anthropic"
    )


class AnthropicRuntime(Runtime):
    """
    Runtime implementation for Anthropic Claude.

    Args:
        api_key:        Anthropic API key. If None, reads from ANTHROPIC_API_KEY env var.
        model:          Default model name (e.g. "claude-sonnet-4-20250514").
        max_tokens:     Maximum tokens in the response (default: 4096).
        system:         System prompt. If provided, sent as the system parameter.
        cache_system:   Whether to cache the system prompt (default: True).
                        Adds cache_control to the system block for prompt caching.
        max_retries:    Maximum number of exec() attempts before raising.
        **client_kwargs: Additional kwargs passed to anthropic.Anthropic().
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 4096,
        system: Optional[str] = None,
        cache_system: bool = True,
        max_retries: int = 2,
        **client_kwargs,
    ):
        super().__init__(model=model, max_retries=max_retries)
        self.max_tokens = max_tokens
        self.system = system
        self.cache_system = cache_system

        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "Anthropic API key is required. Pass api_key= or set ANTHROPIC_API_KEY env var."
            )
        self.client = anthropic.Anthropic(api_key=api_key, **client_kwargs)

    def _call(
        self,
        content: list[dict],
        model: str = "default",
        response_format: Optional[dict] = None,
    ) -> str:
        """
        Call Anthropic Claude API.

        Content blocks are converted to Anthropic's format:
            {"type": "text", "text": "..."}
                → {"type": "text", "text": "..."}

            {"type": "image", "path": "screenshot.png"}
                → {"type": "image", "source": {"type": "base64", ...}}

            {"type": "image", "data": "<base64>", "media_type": "image/png"}
                → {"type": "image", "source": {"type": "base64", ...}}

        If cache_control is set on a content block, it's passed through.
        """
        messages_content = []
        for block in content:
            converted = self._convert_block(block)
            if converted:
                messages_content.append(converted)

        # Enable prompt caching on the last content block
        if messages_content:
            messages_content[-1]["cache_control"] = {"type": "ephemeral"}

        kwargs = {
            "model": model if model != "default" else self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": messages_content}],
        }

        # System prompt with optional caching
        if self.system:
            if self.cache_system:
                kwargs["system"] = [
                    {
                        "type": "text",
                        "text": self.system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            else:
                kwargs["system"] = self.system

        response = self.client.messages.create(**kwargs)
        return response.content[0].text

    def _convert_block(self, block: dict) -> Optional[dict]:
        """Convert a generic content block to Anthropic format."""
        block_type = block.get("type", "text")

        if block_type == "text":
            result = {"type": "text", "text": block["text"]}
            if "cache_control" in block:
                result["cache_control"] = block["cache_control"]
            return result

        if block_type == "image":
            # Image from base64 data
            if "data" in block:
                media_type = block.get("media_type", "image/png")
                return {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": block["data"],
                    },
                }

            # Image from file path
            if "path" in block:
                path = block["path"]
                media_type = mimetypes.guess_type(path)[0] or "image/png"
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("utf-8")
                return {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data,
                    },
                }

            # Image from URL
            if "url" in block:
                return {
                    "type": "image",
                    "source": {
                        "type": "url",
                        "url": block["url"],
                    },
                }

        # Unknown block type — pass text representation
        if "text" in block:
            return {"type": "text", "text": block["text"]}

        return None

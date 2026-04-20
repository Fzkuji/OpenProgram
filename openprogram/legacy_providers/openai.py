"""
OpenAIRuntime — Runtime subclass for OpenAI API.

Supports:
    - Text and image (base64 / URL) content blocks
    - Audio input content blocks (for gpt-4o-audio-preview models)
    - File/PDF content blocks (base64 encoded)
    - response_format (JSON mode / structured output)
    - System prompts
    - Max tokens configuration

Requires: pip install openai

Usage:
    from openprogram.legacy_providers import OpenAIRuntime

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
import warnings
from typing import Optional

from openprogram.agentic_programming.runtime import Runtime

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

    def list_models(self) -> list[str]:
        """Return available OpenAI chat completion models."""
        try:
            models = self.client.models.list()
            prefixes = ("gpt-", "o1-", "o3-", "o4-")
            return sorted([m.id for m in models if m.id.startswith(prefixes)])
        except Exception:
            return ["gpt-4o", "gpt-4o-mini", "o4-mini"]

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

        use_model = model if model != "default" else self.model
        is_reasoning_model = use_model.startswith(("o1", "o3", "o4", "gpt-5"))

        kwargs = {
            "model": use_model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }

        # Temperature is rejected by reasoning-series models (o1/o3/o4/gpt-5).
        if self.temperature is not None and not is_reasoning_model:
            kwargs["temperature"] = self.temperature

        if response_format is not None:
            kwargs["response_format"] = response_format

        # Reasoning effort — only valid on reasoning-capable models. Maps UI
        # level to the API's reasoning_effort. "xhigh" (codex-only concept)
        # downgrades to "high" here since the Chat Completions API accepts
        # only low/medium/high.
        effort = getattr(self, "_thinking_effort", None)
        if effort and is_reasoning_model:
            effort_map = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high"}
            mapped = effort_map.get(effort)
            if mapped:
                kwargs["reasoning_effort"] = mapped

        response = self.client.chat.completions.create(**kwargs)
        if hasattr(response, 'usage') and response.usage:
            u = response.usage
            # input_tokens = total input (including cached), consistent across all providers.
            # Frontend computes new = input_tokens - cache_read.
            details = getattr(u, 'prompt_tokens_details', None)
            cached = getattr(details, 'cached_tokens', 0) if details else 0
            total_in = getattr(u, 'prompt_tokens', 0)
            self.last_usage = {
                "input_tokens": total_in,
                "output_tokens": getattr(u, 'completion_tokens', 0),
                "cache_read": cached or 0,
                "cache_create": 0,
            }
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

        if block_type == "audio":
            # Audio input support for gpt-4o-audio-preview and similar models.
            # Uses OpenAI's input_audio content part format.
            if "data" in block:
                audio_format = block.get("format", "wav")
                return {
                    "type": "input_audio",
                    "input_audio": {
                        "data": block["data"],
                        "format": audio_format,
                    },
                }

            if "path" in block:
                path = block["path"]
                # Determine audio format from extension
                ext = os.path.splitext(path)[1].lower().lstrip(".")
                audio_format = ext if ext in ("wav", "mp3") else "wav"
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("utf-8")
                return {
                    "type": "input_audio",
                    "input_audio": {
                        "data": data,
                        "format": audio_format,
                    },
                }

        if block_type == "file":
            # File/PDF support: encode as base64 and send via OpenAI's file content part.
            # Works with models that support document/file understanding.
            mime_type = block.get("mime_type", "application/pdf")

            if "data" in block:
                filename = block.get("filename", "document.pdf")
                return {
                    "type": "file",
                    "file": {
                        "filename": filename,
                        "file_data": f"data:{mime_type};base64,{block['data']}",
                    },
                }

            if "path" in block:
                path = block["path"]
                detected_mime = mimetypes.guess_type(path)[0] or mime_type
                filename = os.path.basename(path)
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("utf-8")
                return {
                    "type": "file",
                    "file": {
                        "filename": filename,
                        "file_data": f"data:{detected_mime};base64,{data}",
                    },
                }

        if block_type == "video":
            import warnings
            warnings.warn(
                "OpenAIRuntime does not support video content blocks. "
                "Video block will be skipped. Consider using GeminiRuntime for video.",
                UserWarning,
                stacklevel=3,
            )
            return None

        # Unknown block type — pass text representation
        if "text" in block:
            return {"type": "text", "text": block["text"]}

        return None

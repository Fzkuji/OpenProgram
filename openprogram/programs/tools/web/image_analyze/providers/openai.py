"""OpenAI vision provider (gpt-4o, gpt-4o-mini, etc.)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from openprogram.programs.tools.web.web_search._http import post_json

from .._encode import read_b64
from ..registry import ImageInput


API_URL = "https://api.openai.com/v1/chat/completions"
TIMEOUT = 120.0
DEFAULT_MODEL = "gpt-4o-mini"


@dataclass
class OpenAIVisionProvider:
    name: str = "openai"
    priority: int = 100
    requires_env: tuple = ("OPENAI_API_KEY",)
    supported_models: list[str] = field(default_factory=lambda: [
        "gpt-4o-mini", "gpt-4o", "gpt-4-turbo",
    ])

    def is_available(self) -> bool:
        from openprogram.providers.env_api_keys import resolve_provider_key
        return bool(resolve_provider_key("openai"))

    def analyze(
        self,
        images: list[ImageInput],
        prompt: str,
        *,
        model: str | None = None,
    ) -> str:
        from openprogram.providers.env_api_keys import resolve_provider_key
        key = resolve_provider_key("openai") or ""
        if not key:
            raise RuntimeError(
                "No OpenAI API key. Add one in Settings -> Providers or run: "
                "openprogram providers login openai --api-key"
            )
        mdl = model or DEFAULT_MODEL

        content: list[dict] = [{"type": "text", "text": prompt}]
        for img in images:
            if img.url:
                # OpenAI accepts public HTTP URLs directly — saves us the
                # download round-trip and is the cheapest option.
                content.append({"type": "image_url", "image_url": {"url": img.url}})
            elif img.path:
                mime, b64 = read_b64(img.path)
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })

        payload = {
            "model": mdl,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 1024,
        }
        data = post_json(
            API_URL,
            body=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            }, timeout=TIMEOUT, provider_label="OpenAI vision",
            consumer="tool.image_api.fixed",
        )

        choices = data.get("choices") or []
        if not choices:
            return ""
        return str(choices[0].get("message", {}).get("content", "") or "")

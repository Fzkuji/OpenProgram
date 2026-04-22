"""OpenAI vision provider (gpt-4o, gpt-4o-mini, etc.)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

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
        return bool(os.environ.get("OPENAI_API_KEY"))

    def analyze(
        self,
        images: list[ImageInput],
        prompt: str,
        *,
        model: str | None = None,
    ) -> str:
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
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
        req = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = str(e)
            raise RuntimeError(f"OpenAI vision HTTP {e.code}: {body}") from e

        choices = data.get("choices") or []
        if not choices:
            return ""
        return str(choices[0].get("message", {}).get("content", "") or "")

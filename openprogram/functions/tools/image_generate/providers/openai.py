"""OpenAI image-generation provider (DALL-E 3, GPT-Image-1).

Uses POST /v1/images/generations with response_format=b64_json so we
get bytes back in one round-trip — avoids us having to re-fetch from
``oai-cdn.com`` URLs which expire.

Docs: https://platform.openai.com/docs/api-reference/images
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from ..registry import GeneratedImage


API_URL = "https://api.openai.com/v1/images/generations"
TIMEOUT = 120.0
DEFAULT_MODEL = "dall-e-3"


@dataclass
class OpenAIImageProvider:
    name: str = "openai"
    priority: int = 100
    requires_env: tuple = ("OPENAI_API_KEY",)
    supported_models: list[str] = field(default_factory=lambda: [
        "dall-e-3", "dall-e-2", "gpt-image-1",
    ])

    def is_available(self) -> bool:
        from openprogram.providers.env_api_keys import resolve_provider_key
        return bool(resolve_provider_key("openai"))

    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        size: str = "1024x1024",
        n: int = 1,
    ) -> list[GeneratedImage]:
        from openprogram.providers.env_api_keys import resolve_provider_key
        key = resolve_provider_key("openai") or ""
        if not key:
            raise RuntimeError(
                "No OpenAI API key. Add one in Settings -> Providers or run: "
                "openprogram providers login openai --api-key"
            )
        mdl = model or DEFAULT_MODEL
        # DALL-E 3 only supports n=1; transparent cap avoids a cryptic
        # server-side 400.
        effective_n = 1 if mdl == "dall-e-3" else max(1, min(int(n), 10))
        payload: dict = {
            "model": mdl,
            "prompt": prompt,
            "n": effective_n,
            "size": size,
            "response_format": "b64_json",
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
            raise RuntimeError(f"OpenAI image HTTP {e.code}: {body}") from e
        out: list[GeneratedImage] = []
        for item in data.get("data", []):
            raw_b64 = item.get("b64_json") or ""
            if not raw_b64:
                continue
            try:
                img_bytes = base64.b64decode(raw_b64)
            except Exception:
                continue
            out.append(GeneratedImage(
                data=img_bytes,
                mime="image/png",
                revised_prompt=str(item.get("revised_prompt", "") or prompt),
                extras={"model": mdl, "size": size},
            ))
        return out

"""Google Gemini / Imagen image-generation provider.

Calls the AI Studio REST endpoint (no Google Cloud SDK needed). Accepts
GEMINI_API_KEY or GOOGLE_API_KEY — Google's own tooling is inconsistent
about which name to use, so we accept either.

Docs: https://ai.google.dev/gemini-api/docs/image-generation
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from ..registry import GeneratedImage


API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
TIMEOUT = 120.0
DEFAULT_MODEL = "imagen-3.0-generate-002"


@dataclass
class GeminiImagenProvider:
    name: str = "gemini"
    priority: int = 80
    requires_env: tuple = ()  # handled via _resolve_key; registry sees is_available()
    supported_models: list[str] = field(default_factory=lambda: [
        "imagen-3.0-generate-002",
        "imagen-3.0-generate-001",
    ])

    def is_available(self) -> bool:
        return bool(self._resolve_key())

    @staticmethod
    def _resolve_key() -> str:
        from openprogram.providers.env_api_keys import resolve_provider_key
        return resolve_provider_key("google") or ""

    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        size: str = "1024x1024",
        n: int = 1,
    ) -> list[GeneratedImage]:
        key = self._resolve_key()
        if not key:
            raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY not set")
        mdl = model or DEFAULT_MODEL
        # Imagen uses `aspectRatio` strings, not WxH. Translate the
        # common sizes; the model rejects unknown aspect ratios with a
        # 400 so we default rather than passing size through blindly.
        aspect = _size_to_aspect(size)
        url = f"{API_BASE}/{mdl}:predict?key={key}"
        payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {
                "sampleCount": max(1, min(int(n), 4)),
                "aspectRatio": aspect,
            },
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = str(e)
            raise RuntimeError(f"Gemini Imagen HTTP {e.code}: {body}") from e

        out: list[GeneratedImage] = []
        for pred in data.get("predictions", []):
            raw_b64 = pred.get("bytesBase64Encoded") or ""
            if not raw_b64:
                continue
            try:
                img_bytes = base64.b64decode(raw_b64)
            except Exception:
                continue
            mime = pred.get("mimeType") or "image/png"
            out.append(GeneratedImage(
                data=img_bytes,
                mime=mime,
                revised_prompt=prompt,
                extras={"model": mdl, "aspectRatio": aspect},
            ))
        return out


def _size_to_aspect(size: str) -> str:
    """Map WxH string to Imagen aspectRatio enum.

    Imagen supports exactly these: 1:1, 3:4, 4:3, 9:16, 16:9. Anything
    else falls back to 1:1 rather than crashing — agents pass ad-hoc
    sizes and we'd rather generate something square than fail.
    """
    mapping = {
        "1024x1024": "1:1",
        "512x512": "1:1",
        "768x1024": "3:4",
        "1024x768": "4:3",
        "576x1024": "9:16",
        "1024x576": "16:9",
        "1024x1792": "9:16",
        "1792x1024": "16:9",
    }
    return mapping.get(size, "1:1")

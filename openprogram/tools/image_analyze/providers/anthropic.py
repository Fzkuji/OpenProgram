"""Anthropic Claude vision provider (haiku, sonnet, opus)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .._encode import read_b64, sniff_mime
from ..registry import ImageInput


API_URL = "https://api.anthropic.com/v1/messages"
TIMEOUT = 120.0
# Claude 3.5 Haiku — fast, cheap, good enough for most image Q&A.
DEFAULT_MODEL = "claude-3-5-haiku-20241022"


@dataclass
class AnthropicVisionProvider:
    name: str = "anthropic"
    priority: int = 95
    requires_env: tuple = ()
    supported_models: list[str] = field(default_factory=lambda: [
        "claude-3-5-haiku-20241022",
        "claude-3-5-sonnet-20241022",
        "claude-3-opus-20240229",
    ])

    def is_available(self) -> bool:
        return bool(self._resolve_key())

    @staticmethod
    def _resolve_key() -> str:
        return (
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_OAUTH_TOKEN")
            or ""
        )

    def analyze(
        self,
        images: list[ImageInput],
        prompt: str,
        *,
        model: str | None = None,
    ) -> str:
        key = self._resolve_key()
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        mdl = model or DEFAULT_MODEL

        content: list[dict] = []
        for img in images:
            if img.url:
                # Claude accepts URL sources directly as of 2024-09.
                content.append({
                    "type": "image",
                    "source": {"type": "url", "url": img.url},
                })
            elif img.path:
                mime, b64 = read_b64(img.path)
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": b64},
                })
        content.append({"type": "text", "text": prompt})

        payload = {
            "model": mdl,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": content}],
        }
        req = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
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
            raise RuntimeError(f"Anthropic vision HTTP {e.code}: {body}") from e

        parts = data.get("content") or []
        # Concatenate any text parts in case Claude returned multiple.
        out_parts = [p.get("text", "") for p in parts if p.get("type") == "text"]
        return "".join(out_parts)

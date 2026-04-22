"""Google Gemini vision provider (gemini-1.5-flash, gemini-1.5-pro)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .._encode import read_b64, sniff_mime
from ..registry import ImageInput


API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
TIMEOUT = 120.0
DEFAULT_MODEL = "gemini-1.5-flash"


@dataclass
class GeminiVisionProvider:
    name: str = "gemini"
    priority: int = 85
    requires_env: tuple = ()
    supported_models: list[str] = field(default_factory=lambda: [
        "gemini-1.5-flash",
        "gemini-1.5-pro",
        "gemini-2.0-flash-exp",
    ])

    def is_available(self) -> bool:
        return bool(self._resolve_key())

    @staticmethod
    def _resolve_key() -> str:
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""

    def analyze(
        self,
        images: list[ImageInput],
        prompt: str,
        *,
        model: str | None = None,
    ) -> str:
        key = self._resolve_key()
        if not key:
            raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY not set")
        mdl = model or DEFAULT_MODEL

        # Gemini expects inlineData for local files. For URLs we have to
        # fetch+encode ourselves since its REST API doesn't take URLs.
        parts: list[dict] = []
        for img in images:
            if img.path:
                mime, b64 = read_b64(img.path)
                parts.append({"inlineData": {"mimeType": mime, "data": b64}})
            elif img.url:
                b64, mime = _url_to_b64(img.url)
                parts.append({"inlineData": {"mimeType": mime, "data": b64}})
        parts.append({"text": prompt})

        url = f"{API_BASE}/{mdl}:generateContent?key={key}"
        payload = {"contents": [{"parts": parts}]}
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
            raise RuntimeError(f"Gemini vision HTTP {e.code}: {body}") from e

        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        out_parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in out_parts)


def _url_to_b64(url: str) -> tuple[str, str]:
    import base64

    with urllib.request.urlopen(url, timeout=30) as resp:
        data = resp.read()
        mime = resp.headers.get("Content-Type") or sniff_mime(url)
    return base64.b64encode(data).decode("ascii"), mime

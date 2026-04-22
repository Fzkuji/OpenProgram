"""Shared image-loading helpers for image_analyze providers.

Each provider has its own wire format (OpenAI wants a ``data:`` URL,
Claude wants ``source.data`` + ``mediaType``, Gemini wants
``inlineData``). They all share the load-bytes-from-path /
sniff-mime-type steps, which live here.
"""

from __future__ import annotations

import base64
from pathlib import Path


_EXT_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def sniff_mime(path_or_url: str) -> str:
    """Cheap MIME inference from the path/URL extension. PNG fallback."""
    ext = Path(path_or_url.split("?", 1)[0]).suffix.lower()
    return _EXT_MIME.get(ext, "image/png")


def read_b64(path: str) -> tuple[str, str]:
    """Return (mime, base64_data). Raises for non-existent / unreadable files."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = p.resolve()
    if not p.exists():
        raise FileNotFoundError(f"image not found: {path}")
    data = p.read_bytes()
    return sniff_mime(path), base64.b64encode(data).decode("ascii")


__all__ = ["read_b64", "sniff_mime"]

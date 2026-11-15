"""The body of a Codex turn as it goes over HTTP: zstd-compressed for a ChatGPT sign-in.

Codex's own client compresses a ChatGPT account's ``POST /responses``
bodies with zstd at level 3 and sets ``Content-Encoding: zstd``
(codex-rs ``responses_request_compression``, on by default); an API key,
another provider, ``/responses/compact`` and ``/models`` go plain. Large
turns sent plain have been answered 503 where the same turn compressed
was served (magpie's ``codex_zstd.go``, yetone/magpie, MIT).

``OPENPROGRAM_CODEX_ZSTD=0`` sends every body plain.
"""

from __future__ import annotations

import json
import os
from typing import Any

ZSTD_LEVEL = 3


def _enabled() -> bool:
    return os.environ.get("OPENPROGRAM_CODEX_ZSTD", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def encode_turn(body: dict[str, Any], *, chatgpt_sign_in: bool) -> tuple[bytes, dict[str, str]]:
    """The request bytes and the headers they need."""
    raw = json.dumps(body).encode("utf-8")
    if not (chatgpt_sign_in and _enabled()):
        return raw, {}
    try:
        import zstandard
    except ImportError:
        return raw, {}
    compressor = zstandard.ZstdCompressor(level=ZSTD_LEVEL, write_checksum=False)
    return compressor.compress(raw), {"Content-Encoding": "zstd"}

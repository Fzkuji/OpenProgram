"""The page that draws a ```visual block from a chat reply.

A reply may carry HTML or SVG the model wrote (``apps/web/lib/chat/markdown/visual.ts``
mounts it). It runs in an iframe of this page, never in the App's own page:
the iframe is sandboxed without ``allow-same-origin``, so its origin is opaque
and it cannot read the App, its cookies or its storage, and this response's
policy lets it load scripts only from the public CDNs below and make no
network requests of its own. The App posts the block's source and the theme's
colours to it; the page posts back its height, a link to open, or text the user
chose to send as their next message.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.responses import Response

#: Where a visual may load libraries and styles from.
CDN_HOSTS = (
    "https://cdnjs.cloudflare.com",
    "https://cdn.jsdelivr.net",
    "https://unpkg.com",
    "https://esm.sh",
)

_CDN = " ".join(CDN_HOSTS)

POLICY = (
    "sandbox allow-scripts; default-src 'none'; "
    f"script-src 'unsafe-inline' 'unsafe-eval' {_CDN}; "
    f"style-src 'unsafe-inline' {_CDN} https://fonts.googleapis.com; "
    f"font-src data: {_CDN} https://fonts.gstatic.com; "
    f"img-src data: blob: {_CDN}; media-src data: blob:; "
    "connect-src 'none'; frame-src 'none'; worker-src blob:; "
    "form-action 'none'; base-uri 'none'; frame-ancestors 'self'"
)

_PAGE = Path(__file__).with_name("visual-frame.html")


def register(app) -> None:
    @app.get("/visual-frame")
    async def visual_frame():
        return Response(
            _PAGE.read_bytes(),
            media_type="text/html",
            headers={
                "Content-Security-Policy": POLICY,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

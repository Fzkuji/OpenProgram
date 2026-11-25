"""Screenshot routes: what the App's screenshot tool asks the Worker.

Endpoints:
  GET  /api/screenshot/windows?exclude=1,2   the windows on screen, front first, for snapping a selection
  POST /api/screenshot/ocr                   a PNG in the body → {"lines": [{text, box: [x, y, w, h]}]}

The tool captures and draws in the App; the Worker answers what only the
operating system knows (where other applications' windows are) and reads
text off the selection with the system's own recognizer: Vision on macOS,
Windows Media OCR on Windows, tesseract on Linux. Both read this computer's
screen, so only the local owner on it may call them.
"""
from __future__ import annotations

import asyncio
import platform

from fastapi import Request
from fastapi.responses import JSONResponse

MAX_PNG_BYTES = 64 * 1024 * 1024
MAX_EXCLUDED = 32


def _local_owner(request: Request) -> JSONResponse | None:
    from urllib.parse import urlsplit
    from openprogram.host.security.backend_endpoint import is_loopback_host
    from openprogram.operations.self_update.control.projection import ProjectionAccessError
    from .self_updates import require_owner
    try:
        require_owner(request)
        origin = request.headers.get("origin", "")
        forwarded = any(key.lower() == "forwarded" or key.lower().startswith("x-forwarded-")
                        for key in request.headers)
        if (not request.client or not is_loopback_host(request.client.host)
                or not is_loopback_host(request.url.hostname or "") or forwarded
                or (origin and not is_loopback_host(urlsplit(origin).hostname or ""))):
            raise ProjectionAccessError("Screenshots are taken on this computer.")
    except ProjectionAccessError:
        return JSONResponse({"error": "Screenshots are only available to the local owner on this computer."},
                            status_code=403)
    return None


def excluded_ids(value: str) -> set[int]:
    """``1,2,3`` as window ids; anything else is left out."""
    out = set()
    for part in (value or "").split(",")[:MAX_EXCLUDED]:
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


def png_size(png: bytes) -> tuple[int, int]:
    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


def read_text(png: bytes) -> list[dict]:
    """Lines of text in the PNG, each with its box in the PNG's pixels."""
    width, height = png_size(png)
    system = platform.system()
    if system == "Windows":
        from openprogram.host._platform import windows_ocr
        return windows_ocr.recognize(png)
    from openprogram.programs.tools.computer_use.computer_use.desktop import ocr
    boxes = ocr.recognize(png, width, height) if system == "Darwin" else ocr.recognize_tesseract(png)
    return [{"text": box.text, "box": [box.x, box.y, box.width, box.height]} for box in boxes]


def register(app):
    @app.get("/api/screenshot/windows")
    async def api_screenshot_windows(request: Request, exclude: str = ""):
        if (refused := _local_owner(request)) is not None:
            return refused
        from openprogram.host._platform.screen_windows import visible_windows
        return JSONResponse(await asyncio.to_thread(visible_windows, excluded_ids(exclude)))

    @app.post("/api/screenshot/ocr")
    async def api_screenshot_ocr(request: Request):
        if (refused := _local_owner(request)) is not None:
            return refused
        png = await request.body()
        if len(png) > MAX_PNG_BYTES:
            return JSONResponse({"error": "The image is too large."}, status_code=413)
        try:
            png_size(png)
        except ValueError:
            return JSONResponse({"error": "The body is not a PNG."}, status_code=400)
        return JSONResponse({"lines": await asyncio.to_thread(read_text, png)})


__all__ = ["excluded_ids", "png_size", "read_text", "register"]

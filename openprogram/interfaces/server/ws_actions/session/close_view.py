"""The browser stopped keeping a transcript view: ``close_view``."""
from __future__ import annotations


async def handle_close_view(ws, cmd: dict) -> None:
    from openprogram.interfaces.server.transcript import publisher

    view = cmd.get("view")
    if isinstance(view, str) and view:
        publisher.close_view(ws, view)

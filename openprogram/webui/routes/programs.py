"""``/api/programs/*`` — runtime detection of installed agentic programs.

A harness installed after boot (``git clone`` into ``functions/agentics/``
or ``openprogram programs install``) doesn't appear until its modules are
imported. This route re-runs discovery on demand so the new program's
functions go live without restarting the worker:

  * ``POST /api/programs/refresh`` — the manual "refresh" button. Re-scans
    and, if anything new registered, broadcasts ``programs:changed`` so
    every connected UI re-fetches ``/api/functions``.

The background watcher (``functions.watcher``) hits the same core
(``_registry.rescan``) + the same broadcast, so manual and automatic
detection are one code path with two triggers.
"""
from __future__ import annotations

import json as _json

from fastapi.responses import JSONResponse


def _emit(event: str, data: dict) -> None:
    """Broadcast a typed event to all connected WS clients. No-op if the
    server module isn't initialised yet (e.g. during tests). Mirrors
    ``routes/skills.py``'s helper."""
    try:
        from openprogram.webui import server as _server
        _server._broadcast(_json.dumps({"type": event, **data}, default=str))
    except Exception:
        pass


def register(app) -> None:
    @app.post("/api/programs/refresh")
    async def refresh_programs():
        """Re-scan ``functions/agentics/`` for newly-installed programs.

        Returns ``{"added": [...], "total": N}``. Broadcasts
        ``programs:changed`` when ``added`` is non-empty so the function
        list refreshes live in every open tab.
        """
        try:
            from openprogram.functions._registry import rescan
            result = rescan()
        except Exception as e:  # noqa: BLE001
            return JSONResponse(
                content={"ok": False, "error": f"{type(e).__name__}: {e}"},
                status_code=500,
            )
        if result.get("added"):
            _emit("programs:changed", {"added": result["added"]})
        return JSONResponse(content={"ok": True, **result})

"""Session export download endpoint.

``GET /api/sessions/{session_id}/export?format=md|html`` returns the
rendered session as an attachment. The rendering (DAG walk, tool-call
grouping, secret scrubbing) all lives in
``openprogram.store.session.export`` — this module only maps it onto a
download response.

Auth needs nothing here: ``OwnerAuthMiddleware`` guards every route that
is not on the public allowlist in ``owner_auth.py``.
"""
from __future__ import annotations

import re

from fastapi import HTTPException
from fastapi.responses import Response

_MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
}

# Session ids are generated (``local_abc123def0``), but the value arrives
# from the client and lands in a Content-Disposition filename, so keep it
# to the generated shape rather than trusting it.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def register(app):
    @app.get("/api/sessions/{session_id}/export")
    async def export_session_file(session_id: str, format: str = "md"):
        from openprogram.agent.session_db import default_db
        from openprogram.store.session.export import FORMATS, export_session

        if not _SAFE_ID.match(session_id):
            raise HTTPException(status_code=400, detail="invalid session id")
        if format not in FORMATS:
            raise HTTPException(
                status_code=400,
                detail=f"format must be one of {', '.join(FORMATS)}")

        db = default_db()
        if db.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")

        document = export_session(session_id, format, store=db)
        filename = f"{session_id}.{format}"
        return Response(
            content=document,
            media_type=_MEDIA_TYPES[format],
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

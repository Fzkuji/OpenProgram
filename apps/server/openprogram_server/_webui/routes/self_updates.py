"""Owner-authenticated, read-only self-update history and status."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from openprogram.self_update.projection import ProjectionAccessError, list_status, read_evidence, read_status
from openprogram.self_update.store import SelfUpdateStore
from openprogram.self_update.types import ConcurrentUpdateError, SelfUpdateError, UpdateNotFoundError


def require_owner(request: Request, session_id: str = "") -> None:
    from openprogram.programs.tools.system.self_update import _require_local_owner

    authority = getattr(request.state, "authority", None)
    if not isinstance(authority, dict):
        raise ProjectionAccessError("owner authentication required")
    try:
        _require_local_owner(SimpleNamespace(**authority, source="web", session_id=session_id))
    except (ValueError, RuntimeError):
        raise ProjectionAccessError("owner authentication required") from None


def _response(request, session_id, reader, **kwargs):
    try:
        require_owner(request, session_id)
        result = reader(SelfUpdateStore(), session_id=session_id, **kwargs)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except ProjectionAccessError:
        code, error = 403, "self-update access denied"
    except UpdateNotFoundError:
        code, error = 404, "self-update not found"
    except ConcurrentUpdateError:
        code, error = 503, "self-update snapshot temporarily unavailable"
    except (SelfUpdateError, OSError, ValueError, KeyError, TypeError):
        code, error = 409, "self-update state is invalid or inconsistent"
    return JSONResponse({"error": error}, status_code=code, headers={"Cache-Control": "no-store"})


def register(app):
    @app.get("/api/self-updates/{update_id}/evidence")
    def api_self_update_evidence(request: Request, update_id: str,
                                session_id: str = Query(min_length=1, max_length=256),
                                evidence_id: str = Query(min_length=1, max_length=256)):
        return _response(request, session_id, read_evidence, update_id=update_id, evidence_id=evidence_id)

    @app.get("/api/self-updates")
    def api_self_updates(request: Request, session_id: str = Query(min_length=1, max_length=256),
                         limit: int = Query(20, ge=1, le=50), cursor: str | None = Query(None, max_length=2048)):
        return _response(request, session_id, list_status, limit=limit, cursor=cursor)

    @app.get("/api/self-updates/{update_id}")
    def api_self_update(request: Request, update_id: str, session_id: str = Query(min_length=1, max_length=256)):
        return _response(request, session_id, read_status, update_id=update_id)

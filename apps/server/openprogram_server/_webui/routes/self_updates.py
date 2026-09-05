"""Owner-authenticated self-update projections and Desktop location ACKs."""
from __future__ import annotations

from types import SimpleNamespace
import json

from fastapi import Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from openprogram.self_update.projection import ProjectionAccessError, list_status, read_evidence, read_status
from openprogram.self_update.reopen import ReopenUnavailable, acknowledge_reopen, resolve_reopen
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


class ReopenAck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1, max_length=256)
    reopen_id: str = Field(pattern=r"^[0-9a-f]{64}$")


def _reopen_response(request, update_id, ack=None):
    try:
        require_owner(request)
        kwargs = dict(update_id=update_id, principal_id=request.state.authority["principal_id"])
        result = (resolve_reopen(SelfUpdateStore(), **kwargs) if ack is None
                  else acknowledge_reopen(SelfUpdateStore(), **kwargs, **ack.model_dump()))
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except ProjectionAccessError:
        code, reason = 403, "owner_mismatch"
    except UpdateNotFoundError:
        code, reason = 404, "update_missing"
    except ConcurrentUpdateError:
        code, reason = 503, "temporarily_unavailable"
    except ReopenUnavailable as exc:
        code, reason = 409, str(exc)
    except (SelfUpdateError, OSError, ValueError, KeyError, TypeError):
        code, reason = 409, "state_invalid"
    return JSONResponse({"reason": reason}, status_code=code, headers={"Cache-Control": "no-store"})


def register(app):
    @app.api_route("/api/self-updates/{update_id}/desktop-verification/{nonce}", methods=["GET", "POST"])
    async def api_desktop_verification(request: Request, update_id: str, nonce: str):
        from openprogram.self_update.ui_checks import MAX_CAPTURE_BYTES, exchange
        try:
            require_owner(request)
            body = None
            if request.method == "POST":
                raw = bytearray()
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > MAX_CAPTURE_BYTES:
                        raise ValueError("capture too large")
                body = SelfUpdateStore._loads_json(raw.decode())
                token = request.headers.get("authorization", "").removeprefix("Bearer ")
                if body is None or token and token in json.dumps(body, allow_nan=False):
                    raise ValueError("invalid capture")
            result = exchange(SelfUpdateStore(), update_id=update_id, nonce=nonce,
                              principal_id=request.state.authority["principal_id"], body=body)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except ProjectionAccessError:
            return JSONResponse({"reason": "owner_mismatch"}, status_code=403, headers={"Cache-Control": "no-store"})
        except (SelfUpdateError, OSError, ValueError, KeyError, TypeError):
            return JSONResponse({"reason": "capture_unavailable"}, status_code=409, headers={"Cache-Control": "no-store"})

    @app.get("/api/self-updates/{update_id}/desktop-reopen")
    def api_self_update_reopen(request: Request, update_id: str):
        return _reopen_response(request, update_id)

    @app.post("/api/self-updates/{update_id}/desktop-reopen/ack")
    def api_self_update_reopen_ack(request: Request, update_id: str, ack: ReopenAck):
        return _reopen_response(request, update_id, ack)

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

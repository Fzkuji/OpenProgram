"""Run ``computer_use`` calls a paired device forwards to this computer.

* ``POST /api/computer-use/call`` — one whole call (``type``, ``app``,
  ``display``, ``title``, ``action``, ``args``, ``actions``, ``code``,
  ``timeout``); the answer is ``{"text", "images", "is_error"}``, images in
  base64. While ``computer_use.paired_devices`` is off (the default) it
  answers 403 ``paired_devices_off`` and runs nothing.

The owner-auth middleware has already checked the caller's device
credential. The call runs on this computer only, under this computer's own
settings, operating-system permissions and permission rules
(``computer_use/providers/paired.py``).
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["devices"])


@router.post("/api/computer-use/call")
async def run_forwarded_call(request: Request) -> JSONResponse:
    from openprogram.programs.tools.computer_use.computer_use.providers import paired

    if not paired.allowed():
        return JSONResponse({"detail": paired.OFF}, status_code=403)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not str(body.get("type") or "").strip():
        return JSONResponse({"detail": "a call is an object with a type"}, status_code=400)
    device = str((request.scope.get("state") or {}).get("device_id") or "")
    result = await asyncio.to_thread(paired.serve, body, device)
    return JSONResponse(paired.encoded(result))

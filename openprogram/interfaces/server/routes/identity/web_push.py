"""Notifications on this device: the Web Push switch in Settings → General.

* ``GET /api/push`` — the key a browser subscribes with, and the categories
* ``PUT /api/push/subscription`` — ``{subscription, lang, categories?}``: add
  or update this browser; without ``categories`` it keeps what it had and
  says so, which is how a page reads its own switches back
* ``DELETE /api/push/subscription`` — ``{endpoint}``: stop
* ``POST /api/push/test`` — ``{endpoint}``: one notice to this browser

The sending side is ``interfaces/server/web_push.py``. A subscription made by
a paired phone carries that device, and goes when the device is revoked.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openprogram.interfaces.server import web_push

router = APIRouter(tags=["devices"])


class _Subscribe(BaseModel):
    subscription: dict
    lang: str = "en"
    categories: list[str] | None = None


class _Endpoint(BaseModel):
    endpoint: str


@router.get("/api/push")
async def push_settings() -> dict:
    key = await asyncio.to_thread(web_push.public_key)
    return {"public_key": key, "categories": list(web_push.CATEGORIES)}


@router.put("/api/push/subscription")
async def subscribe(body: _Subscribe, request: Request) -> dict:
    device_id = getattr(request.state, "device_id", None)
    try:
        return await asyncio.to_thread(
            web_push.save_subscription, body.subscription,
            device_id=device_id, lang=body.lang, categories=body.categories,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/push/subscription")
async def unsubscribe(body: _Endpoint) -> dict:
    await asyncio.to_thread(web_push.remove_subscription, body.endpoint)
    return {"ok": True}


@router.post("/api/push/test")
async def test(body: _Endpoint) -> dict:
    if not web_push.valid_endpoint(body.endpoint):
        raise HTTPException(status_code=400, detail="unsupported_push_service")
    accepted = await asyncio.to_thread(web_push.send, "test", only_endpoint=body.endpoint, tag="test")
    return {"accepted": accepted > 0}

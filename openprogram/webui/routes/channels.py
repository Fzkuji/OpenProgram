"""Channel adapter HTTP endpoints — health badge + accounts + bindings.

Health endpoint (status badge):
  GET /api/channels/{platform}/{account_id}/status

Accounts CRUD:
  GET    /api/channels/accounts
  POST   /api/channels/accounts          {channel, account_id, token}
  DELETE /api/channels/accounts/{channel}/{account_id}

Bindings CRUD:
  GET    /api/channels/bindings
  POST   /api/channels/bindings          {agent_id, channel, account_id?, peer?, peer_kind?}
  DELETE /api/channels/bindings/{id}

低频操作走 HTTP 比 WS 简洁 (一次 request 拿一次 response, 不用监听
specific envelope 类型). WeChat QR 登录因为是流式仍走 WS
``start_channel_login`` action (前端 WS 现有连接已经在, 不重复实现).

实际写凭据 / 路由表的逻辑跟 WS action 共享同一份代码 (channels.accounts
+ channels.bindings 模块), 所以两条 API 表面不同但语义一致.
"""
from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel


# A heartbeat older than this many seconds counts as "stale" (red).
# 30s gives the 5s watcher tick plenty of slack while still catching
# real adapter crashes / hangs reasonably quickly in the UI.
_STALE_AFTER_SEC = 30.0


class AddAccountRequest(BaseModel):
    channel: str
    account_id: str
    token: str


class AddBindingRequest(BaseModel):
    agent_id: str
    channel: str
    account_id: str | None = None
    peer: str | None = None
    peer_kind: str | None = "direct"


def register(app: FastAPI) -> None:
    # ----- health (heartbeat badge) -----------------------------------------
    @app.get("/api/channels/{platform}/{account_id}/status")
    def channel_status(platform: str, account_id: str):
        from openprogram.channels._heartbeats import get_last_seen

        last = get_last_seen(platform, account_id)
        now = time.time()
        if last is None:
            # Never seen — either the adapter isn't enabled/configured,
            # or the worker hasn't gotten around to spawning its
            # thread yet. UI shows yellow ("connecting") for this.
            return JSONResponse(
                content={
                    "alive": False,
                    "state": "unknown",
                    "last_seen_at": None,
                    "age_seconds": None,
                }
            )
        age = now - last
        alive = age < _STALE_AFTER_SEC
        return JSONResponse(
            content={
                "alive": alive,
                "state": "alive" if alive else "stale",
                "last_seen_at": last,
                "age_seconds": age,
            }
        )

    # ----- accounts CRUD ----------------------------------------------------
    @app.get("/api/channels/accounts")
    def list_accounts():
        from openprogram.channels import accounts as _acc
        rows = [
            {
                "channel": a.channel,
                "account_id": a.account_id,
                "name": a.name,
                "enabled": _acc.is_enabled(a.channel, a.account_id),
                "configured": _acc.is_configured(a.channel, a.account_id),
            }
            for a in _acc.list_all_accounts()
        ]
        return JSONResponse(content={"accounts": rows})

    @app.post("/api/channels/accounts")
    def add_account(req: AddAccountRequest):
        from openprogram.channels import accounts as _acc
        ch = req.channel.strip().lower()
        acct_id = req.account_id.strip()
        token = req.token.strip()
        if ch not in {"telegram", "discord", "slack"}:
            raise HTTPException(
                400,
                "HTTP add-account only supports token-based platforms "
                "(telegram/discord/slack). For WeChat use the QR login "
                "via WebSocket action 'start_channel_login' or the CLI.",
            )
        if not acct_id or not token:
            raise HTTPException(400, "account_id and token required")
        try:
            _acc.create(ch, acct_id)
        except ValueError:
            # already exists — fall through to credential update
            pass
        try:
            _acc.save_credentials(ch, acct_id, {"bot_token": token})
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"{type(e).__name__}: {e}")
        return JSONResponse(
            content={"ok": True, "channel": ch, "account_id": acct_id},
        )

    @app.delete("/api/channels/accounts/{channel}/{account_id}")
    def remove_account(channel: str, account_id: str):
        from openprogram.channels import accounts as _acc
        from openprogram.channels import bindings as _bindings_mod
        try:
            _bindings_mod.remove_for_account(channel, account_id)
            _acc.delete(channel, account_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"{type(e).__name__}: {e}")
        return JSONResponse(
            content={"ok": True, "channel": channel, "account_id": account_id},
        )

    # ----- bindings CRUD ----------------------------------------------------
    @app.get("/api/channels/bindings")
    def list_bindings():
        from openprogram.channels import bindings as _bindings_mod
        rows = _bindings_mod.list_all()
        return JSONResponse(content={"bindings": rows})

    @app.post("/api/channels/bindings")
    def add_binding(req: AddBindingRequest):
        from openprogram.channels import bindings as _bindings_mod
        match: dict = {"channel": req.channel}
        if req.account_id:
            match["account_id"] = req.account_id
        if req.peer:
            match["peer"] = {
                "kind": req.peer_kind or "direct",
                "id": req.peer,
            }
        try:
            entry = _bindings_mod.add(req.agent_id, match)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"{type(e).__name__}: {e}")
        return JSONResponse(content={"ok": True, "binding": entry})

    @app.delete("/api/channels/bindings/{binding_id}")
    def remove_binding(binding_id: str):
        from openprogram.channels import bindings as _bindings_mod
        removed = _bindings_mod.remove(binding_id)
        if not removed:
            raise HTTPException(404, f"no binding {binding_id!r}")
        return JSONResponse(content={"ok": True, "binding_id": binding_id})

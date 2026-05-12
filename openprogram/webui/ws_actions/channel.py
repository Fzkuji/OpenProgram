"""Channel & session-alias WS actions.

list_channel_accounts / start_channel_login / add_channel_account /
list_channel_bindings / add_binding / remove_binding /
list_session_aliases / attach_session / detach_session
"""
from __future__ import annotations

import asyncio
import json
import threading


async def handle_list_channel_accounts(ws, cmd: dict):
    try:
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
    except Exception:
        rows = []
    await ws.send_text(json.dumps({
        "type": "channel_accounts", "data": rows,
    }, default=str))


async def handle_start_channel_login(ws, cmd: dict):
    """QR-based login (wechat only) — spawns iLink flow on worker thread."""
    ch = (cmd.get("channel") or "").strip().lower()
    acct_id = (cmd.get("account_id") or "default").strip()
    if ch != "wechat":
        await ws.send_text(json.dumps({
            "type": "qr_login",
            "data": {"phase": "error",
                      "channel": ch, "account_id": acct_id,
                      "message": f"QR login not supported for {ch!r}"},
        }))
        return
    target_loop = asyncio.get_running_loop()
    target_ws = ws

    def _push(env: dict) -> None:
        env_full = {"type": "qr_login",
                     "data": {"channel": ch,
                               "account_id": acct_id,
                               **env}}
        payload = json.dumps(env_full, default=str)

        async def _send():
            try:
                await target_ws.send_text(payload)
            except Exception:
                pass

        target_loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(_send()))

    def _runner():
        try:
            from openprogram.channels.wechat import (
                login_account_event_driven,
            )
            login_account_event_driven(acct_id, _push)
        except Exception as e:
            _push({"phase": "error",
                    "message": f"{type(e).__name__}: {e}"})

    threading.Thread(target=_runner, daemon=True).start()


async def handle_add_channel_account(ws, cmd: dict):
    """Token-based registration for discord / telegram / slack."""
    from openprogram.channels import accounts as _acc
    ch = (cmd.get("channel") or "").strip().lower()
    acct_id = (cmd.get("account_id") or "").strip()
    token = cmd.get("token") or ""
    if ch not in {"discord", "telegram", "slack"} or not acct_id or not token:
        await ws.send_text(json.dumps({
            "type": "channel_account_added",
            "data": {"ok": False, "error": "channel/account_id/token required"},
        }))
        return
    try:
        _acc.create(ch, acct_id)
        if ch == "discord":
            creds = {"bot_token": token}
        elif ch == "telegram":
            creds = {"bot_token": token}
        else:  # slack
            creds = {"bot_token": token}
        _acc.save_credentials(ch, acct_id, creds)
        await ws.send_text(json.dumps({
            "type": "channel_account_added",
            "data": {"ok": True, "channel": ch, "account_id": acct_id},
        }))
    except Exception as e:
        await ws.send_text(json.dumps({
            "type": "channel_account_added",
            "data": {"ok": False, "error": f"{type(e).__name__}: {e}"},
        }))


async def handle_list_channel_bindings(ws, cmd: dict):
    try:
        from openprogram.channels import bindings as _bindings_mod
        rows = _bindings_mod.list_all()
    except Exception:
        rows = []
    await ws.send_text(json.dumps({
        "type": "channel_bindings", "data": rows,
    }, default=str))


async def handle_add_binding(ws, cmd: dict):
    from openprogram.webui import server as _s
    try:
        from openprogram.channels import bindings as _bindings_mod
        from openprogram.worker import current_worker_pid, spawn_detached
        match: dict = {"channel": cmd.get("channel") or ""}
        if cmd.get("account_id"):
            match["account_id"] = cmd["account_id"]
        if cmd.get("peer"):
            match["peer"] = cmd["peer"]
        entry = _bindings_mod.add(cmd.get("agent_id") or "", match)
        if current_worker_pid() is None:
            spawn_detached()
        _s._broadcast(json.dumps({
            "type": "binding_changed",
            "data": {"action": "added", "binding": entry},
        }, default=str))
    except Exception as e:  # noqa: BLE001
        await ws.send_text(json.dumps({
            "type": "error", "data": {"message": str(e)},
        }, default=str))


async def handle_remove_binding(ws, cmd: dict):
    from openprogram.webui import server as _s
    try:
        from openprogram.channels import bindings as _bindings_mod
        removed = _bindings_mod.remove(cmd.get("binding_id") or "")
        _s._broadcast(json.dumps({
            "type": "binding_changed",
            "data": {"action": "removed", "binding": removed},
        }, default=str))
    except Exception:
        pass


async def handle_list_session_aliases(ws, cmd: dict):
    try:
        from openprogram.agents import session_aliases as _sa
        rows = _sa.list_all()
    except Exception:
        rows = []
    await ws.send_text(json.dumps({
        "type": "session_aliases", "data": rows,
    }, default=str))


async def handle_attach_session(ws, cmd: dict):
    from openprogram.webui import server as _s
    try:
        from openprogram.agents import session_aliases as _sa
        from openprogram.webui import persistence as _p
        from openprogram.worker import current_worker_pid, spawn_detached
        session_id = cmd.get("session_id") or ""
        if not session_id:
            await ws.send_text(json.dumps({
                "type": "error",
                "data": {"message": "session_id required"},
            }, default=str))
            return
        owner = _p.resolve_agent_for_conv(session_id)
        if owner is None:
            owner = _s._default_agent_id()
            try:
                from openprogram.agent.session_db import default_db
                db = default_db()
                if db.get_session(session_id) is None:
                    db.create_session(
                        session_id, owner,
                        title="New conversation",
                        source="tui",
                    )
            except Exception:
                # Session creation is best-effort.
                pass
        row, replaced = _sa.attach(
            channel=cmd.get("channel") or "",
            account_id=cmd.get("account_id") or "default",
            peer_kind=cmd.get("peer_kind") or "direct",
            peer_id=cmd.get("peer_id") or "",
            agent_id=owner,
            session_id=session_id,
        )
        if current_worker_pid() is None:
            spawn_detached()
        _s._broadcast(json.dumps({
            "type": "session_alias_changed",
            "data": {
                "action": "attached",
                "alias": row,
                "replaced": replaced,
            },
        }, default=str))
    except Exception as e:  # noqa: BLE001
        await ws.send_text(json.dumps({
            "type": "error", "data": {"message": str(e)},
        }, default=str))


async def handle_detach_session(ws, cmd: dict):
    from openprogram.webui import server as _s
    try:
        from openprogram.agents import session_aliases as _sa
        removed = _sa.detach(
            channel=cmd.get("channel") or "",
            account_id=cmd.get("account_id") or "default",
            peer_kind=cmd.get("peer_kind") or "direct",
            peer_id=cmd.get("peer_id") or "",
        )
        _s._broadcast(json.dumps({
            "type": "session_alias_changed",
            "data": {"action": "detached", "alias": removed},
        }, default=str))
    except Exception:
        pass


ACTIONS = {
    "list_channel_accounts": handle_list_channel_accounts,
    "start_channel_login": handle_start_channel_login,
    "add_channel_account": handle_add_channel_account,
    "list_channel_bindings": handle_list_channel_bindings,
    "add_binding": handle_add_binding,
    "remove_binding": handle_remove_binding,
    "list_session_aliases": handle_list_session_aliases,
    "attach_session": handle_attach_session,
    "detach_session": handle_detach_session,
}

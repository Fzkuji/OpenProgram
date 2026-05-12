"""Agent management WS actions: list / add / delete / set_default."""
from __future__ import annotations

import json


async def handle_list_agents(ws, cmd: dict):
    try:
        from openprogram.agents import manager as _A
        rows = [a.to_dict() for a in _A.list_all()]
    except Exception:
        rows = []
    await ws.send_text(json.dumps({"type": "agents_list", "data": rows}, default=str))


async def handle_add_agent(ws, cmd: dict):
    from openprogram.webui import server as _s
    try:
        from openprogram.agents import manager as _A
        a = _A.create(
            cmd.get("id") or "",
            name=cmd.get("name") or "",
            provider=cmd.get("provider") or "",
            model_id=cmd.get("model") or "",
            thinking_effort=cmd.get("thinking_effort") or "medium",
            make_default=bool(cmd.get("default") or False),
        )
        _s._broadcast(json.dumps({
            "type": "agent_changed",
            "data": {"action": "created", "agent": a.to_dict()},
        }, default=str))
    except Exception as e:  # noqa: BLE001
        await ws.send_text(json.dumps({
            "type": "error", "data": {"message": str(e)},
        }, default=str))


async def handle_delete_agent(ws, cmd: dict):
    from openprogram.webui import server as _s
    try:
        from openprogram.agents import manager as _A
        _A.delete(cmd.get("id") or "")
        _s._broadcast(json.dumps({
            "type": "agent_changed",
            "data": {"action": "deleted", "agent_id": cmd.get("id")},
        }, default=str))
    except Exception:
        pass


async def handle_set_default_agent(ws, cmd: dict):
    from openprogram.webui import server as _s
    try:
        from openprogram.agents import manager as _A
        a = _A.set_default(cmd.get("id") or "")
        _s._broadcast(json.dumps({
            "type": "agent_changed",
            "data": {"action": "default_changed",
                     "agent_id": a.id, "agent": a.to_dict()},
        }, default=str))
    except Exception as e:  # noqa: BLE001
        await ws.send_text(json.dumps({
            "type": "error", "data": {"message": str(e)},
        }, default=str))


ACTIONS = {
    "list_agents": handle_list_agents,
    "add_agent": handle_add_agent,
    "delete_agent": handle_delete_agent,
    "set_default_agent": handle_set_default_agent,
}

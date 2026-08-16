"""Agent listing HTTP endpoint.

  GET /api/agents     — list all configured agents

WS action ``list_agents`` (openprogram/webui/ws_actions/agent.py) 已经
存在并返回同样的数据, 但 Web 端做下拉填充场景下用一次性 HTTP 比订阅
WS envelope 简洁。这里就是个 thin HTTP wrapper, 复用同一份数据源
(``openprogram.agent.management.manager.list_all``).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse


_TOOL_MODES = {"automatic", "selected", "none"}


def _validated_tools(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("tools must be an object")
    mode = raw.get("mode")
    if mode not in _TOOL_MODES:
        raise ValueError("tools.mode must be automatic, selected, or none")
    out: dict = {"mode": mode}
    if mode == "selected":
        preset = raw.get("preset")
        allowed = raw.get("allowed")
        if preset is not None:
            if not isinstance(preset, str) or not preset.strip():
                raise ValueError("tools.preset must be a non-empty string")
            out["preset"] = preset.strip()
        elif allowed is not None:
            if not isinstance(allowed, list) or not all(
                isinstance(name, str) and name.strip() for name in allowed
            ):
                raise ValueError("tools.allowed must be a list of names")
            out["allowed"] = list(dict.fromkeys(name.strip() for name in allowed))
        else:
            out["allowed"] = []
    return out


def register(app: FastAPI) -> None:
    @app.get("/api/agents")
    def list_agents():
        from openprogram.agent.management import manager as _A
        try:
            rows = [a.to_dict() for a in _A.list_all()]
        except Exception:
            rows = []
        return JSONResponse(content={"agents": rows})

    @app.get("/api/agents/{agent_id}")
    def get_agent(agent_id: str):
        from openprogram.agent.management import manager as _A
        agent = _A.get(agent_id)
        if agent is None:
            return JSONResponse(content={"error": "agent not found"}, status_code=404)
        return JSONResponse(content={"agent": agent.to_dict()})

    @app.patch("/api/agents/{agent_id}")
    def update_agent(agent_id: str, body: dict = None):
        from openprogram.agent.management import manager as _A
        if _A.get(agent_id) is None:
            return JSONResponse(content={"error": "agent not found"}, status_code=404)
        try:
            tools = _validated_tools((body or {}).get("tools"))
        except ValueError as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=400)
        agent = _A.replace_tools(agent_id, tools)
        return JSONResponse(content={"agent": agent.to_dict()})

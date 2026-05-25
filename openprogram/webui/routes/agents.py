"""Agent listing HTTP endpoint.

  GET /api/agents     — list all configured agents

WS action ``list_agents`` (openprogram/webui/ws_actions/agent.py) 已经
存在并返回同样的数据, 但 Web 端做下拉填充场景下用一次性 HTTP 比订阅
WS envelope 简洁。这里就是个 thin HTTP wrapper, 复用同一份数据源
(``openprogram.agents.manager.list_all``).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse


def register(app: FastAPI) -> None:
    @app.get("/api/agents")
    def list_agents():
        from openprogram.agents import manager as _A
        try:
            rows = [a.to_dict() for a in _A.list_all()]
        except Exception:
            rows = []
        return JSONResponse(content={"agents": rows})

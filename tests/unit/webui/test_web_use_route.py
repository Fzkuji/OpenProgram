from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_worker_web_use_route_preserves_images_and_server_owner(monkeypatch):
    from openprogram.programs import ToolReturn
    from openprogram.programs.agentic_functions import browser_agent
    from openprogram.webui.routes.web_use import register

    calls = []

    def execute(arguments, *, owner_id):
        calls.append((arguments, owner_id))
        return ToolReturn(
            text="viewport",
            images=[b"png-bytes"],
            json_data={"frame_id": "frame-1"},
        )

    monkeypatch.setattr(browser_agent, "execute_direct_web_use", execute)
    app = FastAPI()
    register(app)
    response = TestClient(app).post("/api/web-use", json={
        "arguments": {"command": "act"},
        "owner_id": "mcp:client:connection",
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert [item["type"] for item in payload["result"]["content"]] == [
        "text", "image",
    ]
    assert payload["result"]["details"]["json"] == {"frame_id": "frame-1"}
    assert calls == [({"command": "act"}, "mcp:client:connection")]


def test_worker_web_use_route_rejects_non_mcp_owner():
    from openprogram.webui.routes.web_use import register

    app = FastAPI()
    register(app)
    response = TestClient(app).post("/api/web-use", json={
        "arguments": {"command": "list_pages"},
        "owner_id": "turn:forged",
    })

    assert response.status_code == 400


def test_legacy_computer_use_route_is_hidden_and_maps_session_field(monkeypatch):
    from openprogram.programs.agentic_functions import browser_agent
    from openprogram.webui.routes.web_use import register

    calls = []

    def execute(arguments, *, owner_id):
        calls.append((arguments, owner_id))
        return {"ok": True, "web_session_id": "cs-legacy"}

    monkeypatch.setattr(browser_agent, "execute_direct_web_use", execute)
    app = FastAPI()
    register(app)
    response = TestClient(app).post("/api/computer-use", json={
        "arguments": {
            "command": "close", "computer_session_id": "cs-legacy",
        },
        "owner_id": "mcp:client:connection",
    })

    assert response.status_code == 200
    assert calls == [(
        {"command": "close", "web_session_id": "cs-legacy"},
        "mcp:client:connection",
    )]
    assert json.loads(response.json()["result"]["content"][0]["text"]) == {
        "ok": True, "computer_session_id": "cs-legacy",
    }
    assert "/api/computer-use" not in app.openapi()["paths"]

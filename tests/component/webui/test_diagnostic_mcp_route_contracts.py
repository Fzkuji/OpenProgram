from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_doctor_route_contract(monkeypatch):
    from openprogram._cli_cmds import doctor
    from openprogram.webui.routes import misc

    results = [
        {"ok": True, "label": "python", "detail": "3.14"},
        {"ok": False, "label": "worker", "detail": "not running"},
    ]
    monkeypatch.setattr(doctor, "run_checks", lambda: results)
    app = FastAPI()
    misc.register(app)

    response = TestClient(app).get("/api/doctor")

    assert response.status_code == 200
    assert response.json() == {"results": results, "all_ok": False}


def test_mcp_list_route_contract(monkeypatch):
    from openprogram.webui.routes import mcp

    status = {
        "name": "local-tools",
        "type": "local",
        "enabled": True,
        "timeout_seconds": 30,
        "always_load": False,
        "ready": True,
        "error": None,
        "error_kind": None,
        "source_catalog_url": None,
        "source_entry_hash": None,
        "tool_count": 1,
        "tools": ["lookup"],
        "registered_tool_names": ["mcp_local-tools_lookup"],
        "command": ["python", "server.py"],
        "env": {},
    }
    monkeypatch.setattr(mcp, "server_status", lambda: [status])
    app = FastAPI()
    mcp.register(app)

    response = TestClient(app).get("/api/mcp/servers")

    assert response.status_code == 200
    assert response.json() == {"servers": [status]}


def test_mcp_detail_route_contract_and_missing_status(monkeypatch):
    from openprogram.webui.routes import mcp

    detail = {
        "name": "remote-tools",
        "type": "http",
        "enabled": True,
        "timeout_seconds": 20,
        "always_load": False,
        "ready": True,
        "error": None,
        "error_kind": None,
        "source_catalog_url": None,
        "source_entry_hash": None,
        "tool_count": 1,
        "tools": ["search"],
        "registered_tool_names": ["mcp_remote-tools_search"],
        "url": "https://mcp.example.test",
        "headers": {},
        "auth": {"kind": "none", "authenticated": False},
        "tool_schemas": [
            {
                "name": "search",
                "title": None,
                "description": "Search",
                "input_schema": {"type": "object"},
            }
        ],
    }
    monkeypatch.setattr(
        mcp,
        "get_server",
        lambda name: detail if name == "remote-tools" else None,
    )
    app = FastAPI()
    mcp.register(app)
    client = TestClient(app)

    response = client.get("/api/mcp/servers/remote-tools")
    missing = client.get("/api/mcp/servers/missing")

    assert response.status_code == 200
    assert response.json() == detail
    assert missing.status_code == 404
    assert missing.json() == {"detail": "server 'missing' not loaded"}

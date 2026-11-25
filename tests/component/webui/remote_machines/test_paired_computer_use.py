"""The receiving end of computer_use from a paired device: ``POST /api/computer-use/call`` on this Worker."""
from __future__ import annotations

import base64

from openprogram.programs._host.execution import ToolReturn
from openprogram.programs.tools.computer_use.computer_use import tool
from openprogram.programs.tools.computer_use.computer_use.providers import paired

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


def _served(monkeypatch):
    ran: list[tuple[dict, str]] = []
    monkeypatch.setattr(tool, "run", lambda call, session: ran.append((call, session)) or ToolReturn(
        text="ran here", images=[PNG]))
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {"tools": {"permission_rules": {}}})
    return ran


def test_the_endpoint_refuses_while_the_switch_is_off(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openprogram.interfaces.server.remote_machines import computer_use as endpoint

    app = FastAPI()
    app.include_router(endpoint.router)
    client = TestClient(app)
    ran = _served(monkeypatch)
    config: dict = {}
    monkeypatch.setattr("openprogram.state.config.read_config_value", lambda key: config.get(key))
    answer = client.post("/api/computer-use/call", json={"type": "screen", "action": "screenshot"})
    assert answer.status_code == 403 and answer.json() == {"detail": paired.OFF} and ran == []

    config["computer_use"] = {"paired_devices": True}
    assert client.post("/api/computer-use/call", json={"action": "screenshot"}).status_code == 400
    answer = client.post("/api/computer-use/call", json={"type": "screen", "action": "screenshot"})
    assert answer.status_code == 200
    assert answer.json() == {"text": "ran here", "images": [base64.b64encode(PNG).decode()], "is_error": False}
    assert ran[0][0]["device"] == ""


def test_the_endpoint_needs_the_device_credential():
    """Only the owner-auth middleware lets a caller in: the endpoint is not one of its public paths."""
    from openprogram.interfaces.server import owner_auth
    for method in ("POST", "GET"):
        assert not owner_auth._public_shell({"type": "http", "method": method, "path": "/api/computer-use/call"})

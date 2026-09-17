"""Native ticket contract; adapters below are explicitly test doubles."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import httpx
import pytest
from fastapi import FastAPI

_PATH = Path(__file__).resolve().parents[3] / "openprogram" / "terminal_resources.py"
_SPEC = importlib.util.spec_from_file_location("terminal_ticket_contract", _PATH)
module = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = module
_SPEC.loader.exec_module(module)


def test_single_use_ticket_is_not_a_persistent_capability():
    broker = module.TerminalTickets()
    ticket = broker.issue({"window_id": "main", "action": "input"}, lambda: True)
    assert broker.claim(ticket, "main")["action"] == "input"
    with pytest.raises(PermissionError):
        broker.claim(ticket, "main")


@pytest.mark.parametrize("window,valid", [("other", True), ("main", False)])
def test_wrong_window_or_revocation_rejects_before_dispatch(window, valid):
    broker = module.TerminalTickets()
    ticket = broker.issue({"window_id": "main"}, lambda: valid)
    with pytest.raises(PermissionError):
        broker.claim(ticket, window)


def test_expired_tickets_and_bounded_admission():
    now = [0.0]
    broker = module.TerminalTickets(clock=lambda: now[0], limit=1)
    ticket = broker.issue({"window_id": "main"}, lambda: True)
    with pytest.raises(RuntimeError):
        broker.issue({"window_id": "main"}, lambda: True)
    now[0] = module.TTL_SECONDS + 1
    with pytest.raises(PermissionError):
        broker.claim(ticket, "main")
    broker.issue({"window_id": "main"}, lambda: True)


@pytest.mark.parametrize("value", [None, "", "a" * 63, "g" * 64, {}, 1])
def test_malformed_tickets_rejected(value):
    with pytest.raises(PermissionError):
        module.TerminalTickets().claim(value, "main")


def test_caller_mutation_cannot_change_an_issued_plan():
    broker = module.TerminalTickets()
    plan = {"window_id": "main", "session_id": "a", "nested": {"x": 1}}
    ticket = broker.issue(plan, lambda: True)
    plan["session_id"] = "b"
    plan["nested"]["x"] = 2
    claimed = broker.claim(ticket, "main")
    assert claimed["session_id"] == "a"
    assert claimed["nested"]["x"] == 1


@pytest.mark.parametrize("changes", [
    {"action": "exec"}, {"cursor": True}, {"cursor": -1}, {"cursor": 2**54},
    {"action": "input", "binding_id": "", "data": "pwd\r"},
    {"action": "input", "data": "x" * 16385}, {"generation": "old"},
    {"workdir": "bad\0path"},
])
def test_tool_argument_validation(changes):
    args = dict(action="observe", terminal_id="terminal:main:shell", generation="a" * 32,
                binding_id="b" * 32, data="", cursor=0, expected_input_revision=0, workdir=None)
    args.update(changes)
    with pytest.raises(ValueError):
        module._arguments(**args)


def test_raw_input_is_preserved_not_appended_or_shell_parsed():
    result = module._arguments("input", "id", "a" * 32, "b" * 32, "no newline", 0, 0, None)
    assert result["data"] == "no newline"


@pytest.mark.parametrize("authorization,peer,allowed", [
    (None, "127.0.0.1", False), ("Bearer wrong", "127.0.0.1", False),
    ("Bearer secret", "203.0.113.1", False), ("Bearer secret", "127.0.0.1", True),
])
def test_claim_route_requires_native_owner_bearer_and_loopback(monkeypatch, authorization, peer, allowed):
    # Only the installation's token verifier and loopback helper are replaced.
    endpoint = SimpleNamespace(is_loopback_host=lambda value: value == "127.0.0.1")
    monkeypatch.setitem(sys.modules, "openprogram.backend_endpoint", endpoint)
    broker = module.TerminalTickets()
    monkeypatch.setattr(module, "_tickets", broker)
    app = FastAPI()
    app.state.owner_auth = SimpleNamespace(verify_token=lambda value: value == "secret")
    module.register_routes(app)
    ticket = broker.issue({"window_id": "main"}, lambda: True)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(peer, 321)), base_url="http://127.0.0.1") as client:
            reply = await client.post("/api/terminal/claim", json={"ticket": ticket, "window_id": "main"},
                                      headers={"authorization": authorization} if authorization else {})
            assert reply.status_code == (200 if allowed else 403)
            assert reply.json()["ok"] is allowed
    asyncio.run(run())

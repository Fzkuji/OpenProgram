from __future__ import annotations

import asyncio
import importlib
import json

import pytest
from starlette.websockets import WebSocketDisconnect

from openprogram.webui import server


class _SequenceWS:
    def __init__(self, incoming: list[object]) -> None:
        self.incoming = list(incoming)
        self.sent: list[dict] = []

    async def accept(self) -> None:
        pass

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def receive_text(self) -> str:
        item = self.incoming.pop(0)
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, str)
        return item


@pytest.fixture(autouse=True)
def isolated_websocket_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "_ws_connections", [])
    monkeypatch.setattr(server, "_discover_functions", lambda: [])
    monkeypatch.setattr(server, "_get_provider_info", lambda: {})


@pytest.mark.parametrize(
    ("module_name", "attribute", "command"),
    [
        (
            "openprogram.agent.management.manager",
            "list_all",
            {"action": "list_agents"},
        ),
        (
            "openprogram.agent.management.manager",
            "delete",
            {"action": "delete_agent", "id": "agent-1"},
        ),
        (
            "openprogram.agent.management.manager",
            "create",
            {"action": "add_agent", "id": "agent-1"},
        ),
        (
            "openprogram.agent.management.manager",
            "set_default",
            {"action": "set_default_agent", "id": "agent-1"},
        ),
        (
            "openprogram.channels.accounts",
            "list_all_accounts",
            {"action": "list_channel_accounts"},
        ),
        (
            "openprogram.channels.bindings",
            "list_all",
            {"action": "list_channel_bindings"},
        ),
        (
            "openprogram.channels.accounts",
            "create",
            {
                "action": "add_channel_account",
                "channel": "telegram",
                "account_id": "account-1",
                "token": "not-a-real-token",
            },
        ),
        (
            "openprogram.channels.bindings",
            "remove_for_account",
            {
                "action": "remove_channel_account",
                "channel": "telegram",
                "account_id": "account-1",
            },
        ),
        (
            "openprogram.channels.bindings",
            "add",
            {"action": "add_binding", "agent_id": "agent-1"},
        ),
        (
            "openprogram.channels.bindings",
            "remove",
            {"action": "remove_binding", "binding_id": "binding-1"},
        ),
        (
            "openprogram.agent.management.session_aliases",
            "list_all",
            {"action": "list_session_aliases"},
        ),
        (
            "openprogram.agent.management.session_aliases",
            "detach",
            {"action": "detach_session", "channel": "wechat"},
        ),
        (
            "openprogram.agent.management.session_aliases",
            "attach",
            {
                "action": "attach_session",
                "session_id": "session-1",
                "channel": "wechat",
            },
        ),
        (
            "openprogram.config_schema",
            "get_settings",
            {"action": "get_settings"},
        ),
        (
            "openprogram.config_schema",
            "set_setting",
            {"action": "set_setting", "key": "language", "value": "en"},
        ),
    ],
)
def test_management_storage_failure_is_explicit_and_connection_continues(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    attribute: str,
    command: dict[str, object],
) -> None:
    secret = f"private-{command['action']}-failure"

    def fail(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(importlib.import_module(module_name), attribute, fail)
    request = {
        **command,
        "session_id": "session-1",
        "request_id": f"request-{command['action']}",
    }
    ws = _SequenceWS([
        json.dumps(request),
        "ping",
        WebSocketDisconnect(1000),
    ])

    asyncio.run(server._websocket_handler(ws))

    errors = [frame for frame in ws.sent if frame["type"] == "operation_error"]
    assert len(errors) == 1
    assert errors[0]["data"] == {
        "action": command["action"],
        "session_id": "session-1",
        "request_id": f"request-{command['action']}",
        "code": "handler_error",
        "message": "Action failed",
        "retryable": False,
    }
    assert ws.sent[-1] == {"type": "pong"}
    assert secret not in json.dumps(ws.sent)

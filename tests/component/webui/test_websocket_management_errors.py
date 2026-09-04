from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import importlib
import json
import re

import pytest
from starlette.websockets import WebSocketDisconnect

from openprogram.agent.authority import owner_authority
from openprogram.webui import server


class _SequenceWS:
    def __init__(self, incoming: list[object]) -> None:
        self.incoming = list(incoming)
        self.sent: list[dict] = []
        self.scope = {
            "state": {
                "authority": owner_authority("owner/install/0123456789abcdef"),
            },
        }

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


def _assert_operation_error_data(
    data: dict,
    *,
    action: str,
    scope: str,
) -> None:
    assert set(data) == {
        "error_id",
        "request_id",
        "scope",
        "code",
        "message",
        "action",
        "session_id",
        "operation_id",
        "retryable",
        "severity",
        "correlation_id",
        "occurred_at",
    }
    assert re.fullmatch(r"err_[0-9a-f]{32}", data["error_id"])
    assert re.fullmatch(r"corr_[0-9a-f]{32}", data["correlation_id"])
    assert data["occurred_at"].endswith("Z")
    occurred_at = datetime.fromisoformat(data["occurred_at"].replace("Z", "+00:00"))
    assert occurred_at.utcoffset() == timedelta(0)
    assert data["action"] == action
    assert data["session_id"] == "session-1"
    assert data["request_id"] == f"request-{action}"
    assert data["scope"] == scope
    assert data["code"] == "handler_error"
    assert data["message"] == "Action failed"
    assert data["operation_id"] is None
    assert data["retryable"] is False
    assert data["severity"] == "error"


@pytest.mark.parametrize(
    ("module_name", "attribute", "command", "scope"),
    [
        (
            "openprogram.agent.management.manager",
            "list_all",
            {"action": "list_agents"},
            "agent",
        ),
        (
            "openprogram.agent.management.manager",
            "delete",
            {"action": "delete_agent", "id": "agent-1"},
            "agent",
        ),
        (
            "openprogram.agent.management.manager",
            "create",
            {"action": "add_agent", "id": "agent-1"},
            "agent",
        ),
        (
            "openprogram.agent.management.manager",
            "set_default",
            {"action": "set_default_agent", "id": "agent-1"},
            "agent",
        ),
        (
            "openprogram.channels.accounts",
            "list_all_accounts",
            {"action": "list_channel_accounts"},
            "channel",
        ),
        (
            "openprogram.channels.bindings",
            "list_all",
            {"action": "list_channel_bindings"},
            "channel",
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
            "channel",
        ),
        (
            "openprogram.channels.bindings",
            "remove_for_account",
            {
                "action": "remove_channel_account",
                "channel": "telegram",
                "account_id": "account-1",
            },
            "channel",
        ),
        (
            "openprogram.channels.bindings",
            "add",
            {"action": "add_binding", "agent_id": "agent-1"},
            "channel",
        ),
        (
            "openprogram.channels.bindings",
            "remove",
            {"action": "remove_binding", "binding_id": "binding-1"},
            "channel",
        ),
        (
            "openprogram.agent.management.session_aliases",
            "list_all",
            {"action": "list_session_aliases"},
            "channel",
        ),
        (
            "openprogram.agent.management.session_aliases",
            "detach",
            {"action": "detach_session", "channel": "wechat"},
            "channel",
        ),
        (
            "openprogram.agent.management.session_aliases",
            "attach",
            {
                "action": "attach_session",
                "session_id": "session-1",
                "channel": "wechat",
            },
            "channel",
        ),
        (
            "openprogram.config_schema",
            "get_settings",
            {"action": "get_settings"},
            "settings",
        ),
        (
            "openprogram.config_schema",
            "set_setting",
            {"action": "set_setting", "key": "language", "value": "en"},
            "settings",
        ),
    ],
)
def test_management_storage_failure_is_explicit_and_connection_continues(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    attribute: str,
    command: dict[str, object],
    scope: str,
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
    _assert_operation_error_data(
        errors[0]["data"],
        action=str(command["action"]),
        scope=scope,
    )
    assert ws.sent[-1] == {"type": "pong"}
    assert secret not in json.dumps(ws.sent)

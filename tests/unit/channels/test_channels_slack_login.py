"""A Slack token that was rejected ends the run; a network failure does not."""
from __future__ import annotations

import sys
import threading
import types

import pytest

from openprogram.interfaces.channels import accounts as _accounts
from openprogram.interfaces.channels.implementations.slack import SlackChannel


class _Web:
    error: Exception | None = None

    def __init__(self, token: str) -> None:
        self.token = token

    def auth_test(self) -> dict:
        if _Web.error is not None:
            raise _Web.error
        return {"user_id": "U1", "user": "bot"}


class _Socket:
    error: Exception | None = None

    def __init__(self, app_token: str, web_client: _Web) -> None:
        self.socket_mode_request_listeners: list = []

    def connect(self) -> None:
        if _Socket.error is not None:
            raise _Socket.error

    def disconnect(self) -> None:
        return None


@pytest.fixture
def channel(tmp_path, monkeypatch: pytest.MonkeyPatch) -> SlackChannel:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    monkeypatch.setattr(
        "openprogram.host.security.safe_http.require_active_sdk_transport",
        lambda *_a, **_k: None,
    )
    modules = {
        "slack_sdk": types.ModuleType("slack_sdk"),
        "slack_sdk.web": types.SimpleNamespace(WebClient=_Web),
        "slack_sdk.socket_mode": types.SimpleNamespace(SocketModeClient=_Socket),
        "slack_sdk.socket_mode.request": types.SimpleNamespace(SocketModeRequest=object),
        "slack_sdk.socket_mode.response": types.SimpleNamespace(SocketModeResponse=object),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(_Web, "error", None)
    monkeypatch.setattr(_Socket, "error", None)
    _accounts.save_credentials("slack", "a1", {"bot_token": "xoxb", "app_token": "xapp"})
    return SlackChannel(account_id="a1")


@pytest.mark.parametrize("code", ["invalid_auth", "not_authed", "token_revoked", "account_inactive"])
def test_a_rejected_bot_token_returns(channel, code) -> None:
    _Web.error = RuntimeError(code)
    channel.run(threading.Event())


def test_a_rejected_app_token_returns(channel) -> None:
    _Socket.error = RuntimeError("invalid_auth")
    channel.run(threading.Event())


def test_a_network_failure_raises_so_the_adapter_reconnects(channel) -> None:
    _Socket.error = ConnectionError("network down")
    with pytest.raises(ConnectionError):
        channel.run(threading.Event())

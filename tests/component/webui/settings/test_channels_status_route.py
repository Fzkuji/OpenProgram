"""The channel status route says what state an account is really in."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.interfaces.channels import _heartbeats
from openprogram.interfaces.channels import accounts as accounts_module
from openprogram.interfaces.server.routes.settings import channels


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    enabled = {"value": True}
    configured = {"value": True}
    monkeypatch.setattr(accounts_module, "is_enabled", lambda *_: enabled["value"])
    monkeypatch.setattr(accounts_module, "is_configured", lambda *_: configured["value"])
    _heartbeats.clear("telegram", "status-test")
    _heartbeats.clear_stop("telegram", "status-test")
    app = FastAPI()
    channels.register(app)
    yield TestClient(app), enabled, configured
    _heartbeats.clear("telegram", "status-test")
    _heartbeats.clear_stop("telegram", "status-test")


def _state(test_client: TestClient) -> str:
    return test_client.get("/api/channels/telegram/status-test/status").json()["state"]


def test_a_usable_account_without_a_thread_is_unknown(client) -> None:
    assert _state(client[0]) == "unknown"


def test_a_fresh_heartbeat_is_alive_and_an_old_one_stale(client) -> None:
    _heartbeats.heartbeat("telegram", "status-test")
    assert _state(client[0]) == "alive"
    _heartbeats.heartbeat("telegram", "status-test", ts=1.0)
    assert _state(client[0]) == "stale"


@pytest.mark.parametrize("reason", [_heartbeats.STOP_SIGNED_OUT, _heartbeats.STOP_FAILED])
def test_a_stop_reason_beats_a_heartbeat_from_the_dying_thread(client, reason) -> None:
    _heartbeats.heartbeat("telegram", "status-test")
    _heartbeats.record_stop("telegram", "status-test", reason)
    response = client[0].get("/api/channels/telegram/status-test/status").json()
    assert response["state"] == reason
    assert response["alive"] is False


def test_turned_off_and_no_token_come_first(client) -> None:
    test_client, enabled, configured = client
    _heartbeats.record_stop("telegram", "status-test", _heartbeats.STOP_SIGNED_OUT)
    configured["value"] = False
    assert _state(test_client) == "no_token"
    enabled["value"] = False
    assert _state(test_client) == "off"


def test_a_crash_loop_is_reconnecting_though_the_thread_is_alive(client) -> None:
    _heartbeats.heartbeat("telegram", "status-test")
    _heartbeats.record_retry("telegram", "status-test", settles_in=60)
    response = client[0].get("/api/channels/telegram/status-test/status").json()
    assert response["state"] == "reconnecting"
    assert response["alive"] is False


def test_a_reconnect_that_settled_is_alive_again(client) -> None:
    _heartbeats.heartbeat("telegram", "status-test")
    _heartbeats.record_retry("telegram", "status-test", settles_in=-1)
    assert _state(client[0]) == "alive"


def test_a_stop_reason_beats_a_reconnect(client) -> None:
    _heartbeats.record_retry("telegram", "status-test", settles_in=60)
    _heartbeats.record_stop("telegram", "status-test", _heartbeats.STOP_SIGNED_OUT)
    assert _state(client[0]) == "signed_out"

"""WS ``start_channel_login`` — TUI-driven QR login for channels.

Real iLink calls would need a phone to complete; we monkey-patch
``login_account_event_driven`` to emit a scripted sequence of
phase envelopes and verify the WS handler forwards them as
``qr_login`` messages.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openprogram.agent.session_db import SessionDB
from openprogram.webui.messages import MessageStore, set_store_for_testing
from openprogram.webui.server import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.agent.session_db.default_db",
                        lambda: db)
    set_store_for_testing(MessageStore(persist_dir=tmp_path / "store"))
    app = create_app()
    with TestClient(app) as c:
        yield c
    set_store_for_testing(None)


def _drain_bootstrap(ws) -> None:
    for _ in range(4):
        ws.receive_text()


def _collect_qr_envelopes(ws, *, max_frames: int = 30) -> list[dict]:
    """Read frames until we see a terminal phase (done / expired / error)
    or hit ``max_frames``. ``receive_text`` blocks forever — counting
    on the worker thread to push at least one terminal frame; if it
    doesn't, the test will hang and pytest's per-test timeout fires."""
    out: list[dict] = []
    while len(out) < max_frames:
        raw = ws.receive_text()
        env = json.loads(raw)
        if env.get("type") == "qr_login":
            out.append(env)
            phase = env.get("data", {}).get("phase")
            if phase in ("done", "expired", "error"):
                return out
    return out


def test_qr_login_streams_phases_to_ws(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler should forward every event the inner login flow
    pushes: qr_ready → scanned → confirmed → done."""
    def _fake_login(account_id: str, on_event):
        on_event({"phase": "qr_ready", "url": "https://example.com/q",
                  "ascii": "[QR ASCII ART]"})
        on_event({"phase": "scanned"})
        on_event({"phase": "confirmed"})
        on_event({"phase": "done", "credentials": {"bot_token": "x",
                                                      "ilink_bot_id": "y"}})

    monkeypatch.setattr(
        "openprogram.channels.wechat.login_account_event_driven",
        _fake_login,
    )

    with client.websocket_connect("/ws") as ws:
        _drain_bootstrap(ws)
        ws.send_text(json.dumps({
            "action": "start_channel_login",
            "channel": "wechat",
            "account_id": "default",
        }))
        envs = _collect_qr_envelopes(ws)

    phases = [e["data"]["phase"] for e in envs]
    assert "qr_ready" in phases
    assert "scanned" in phases
    assert "confirmed" in phases
    assert "done" in phases
    qr = next(e for e in envs if e["data"]["phase"] == "qr_ready")
    assert qr["data"]["ascii"] == "[QR ASCII ART]"
    assert qr["data"]["channel"] == "wechat"
    assert qr["data"]["account_id"] == "default"


def test_qr_login_unsupported_channel_emits_error(
    client: TestClient,
) -> None:
    """Telegram has a token-based path, not QR. Asking for QR there
    should fail fast with an ``error`` envelope, not silently hang."""
    with client.websocket_connect("/ws") as ws:
        _drain_bootstrap(ws)
        ws.send_text(json.dumps({
            "action": "start_channel_login",
            "channel": "telegram",
            "account_id": "default",
        }))
        envs = _collect_qr_envelopes(ws)
    assert envs
    assert envs[0]["data"]["phase"] == "error"
    assert "telegram" in envs[0]["data"]["message"]


def test_qr_login_expired_propagates(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the QR expires before the user scans, we should see
    qr_ready followed by expired, not an open thread leaking."""
    def _fake_login(account_id: str, on_event):
        on_event({"phase": "qr_ready", "url": "u", "ascii": "Q"})
        on_event({"phase": "expired"})

    monkeypatch.setattr(
        "openprogram.channels.wechat.login_account_event_driven",
        _fake_login,
    )

    with client.websocket_connect("/ws") as ws:
        _drain_bootstrap(ws)
        ws.send_text(json.dumps({
            "action": "start_channel_login",
            "channel": "wechat",
            "account_id": "default",
        }))
        envs = _collect_qr_envelopes(ws)

    phases = [e["data"]["phase"] for e in envs]
    assert phases[0] == "qr_ready"
    assert phases[-1] == "expired"

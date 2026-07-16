"""``attach_session`` lazy-creates the SessionDB row when needed.

The TUI fires ``/channel`` before the user has sent any message —
so there's no SessionDB row yet. The handler should mint an empty
session owned by the default agent, then attach the channel alias to
it. Avoids forcing the user to send a dummy message just to bind a
channel.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openprogram.agent.session_db import SessionDB
from openprogram.webui.messages import MessageStore, set_store_for_testing
from openprogram.webui.server import create_app


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.agent.session_db.default_db",
                        lambda: db)
    set_store_for_testing(MessageStore(persist_dir=tmp_path / "store"))
    # The handler also probes the channel worker — short-circuit so
    # tests don't accidentally spawn the long-poll loop in tmp.
    monkeypatch.setattr("openprogram.channels.worker.current_worker_pid",
                        lambda: 1)   # pretend worker already up
    monkeypatch.setattr("openprogram.channels.worker.spawn_detached",
                        lambda: 0)
    # session_aliases reads/writes ~/.agentic/session_aliases.json by
    # default — without this redirect, attach_session would clobber
    # the developer's real binding file. Module re-imports
    # ``get_state_dir`` on every call, so a top-level patch is enough.
    state_root = tmp_path / "state"
    state_root.mkdir(exist_ok=True)
    monkeypatch.setattr("openprogram.paths.get_state_dir",
                        lambda: state_root)
    app = create_app()
    with TestClient(app) as c:
        yield c, db
    set_store_for_testing(None)




def test_attach_creates_missing_session(env) -> None:
    """User just opened the TUI, never sent a message. /channel
    fires attach_session with a freshly-minted local_xxx id. Server
    must lazy-create the SessionDB row."""
    client, db = env
    NEW_ID = "local_freshxx"
    assert db.get_session(NEW_ID) is None  # baseline

    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({
            "action": "attach_session",
            "session_id": NEW_ID,
            "channel": "wechat",
            "account_id": "default",
            "peer_kind": "direct",
            "peer_id": "*",
        }))
        # Read frames until we see session_alias_changed (success)
        # or error.
        for _ in range(50):  # tolerate any number of bootstrap frames
            env_msg = json.loads(ws.receive_text())
            t = env_msg.get("type")
            if t == "session_alias_changed":
                assert env_msg["data"]["action"] == "attached"
                break
            if t == "error":
                pytest.fail(
                    f"unexpected error: {env_msg['data'].get('message')}")

    # SessionDB row exists now
    sess = db.get_session(NEW_ID)
    assert sess is not None
    assert sess["title"] == "New conversation"
    assert sess["source"] == "tui"

    # session_aliases got the row too
    from openprogram.agent.management import session_aliases as _sa
    aliases = _sa.list_for_session(NEW_ID)
    assert len(aliases) == 1
    assert aliases[0]["channel"] == "wechat"
    assert aliases[0]["peer"] == {"kind": "direct", "id": "*"}


def test_attach_does_not_disturb_existing_session(env) -> None:
    """Existing session attaches normally without overwriting title /
    source / agent_id."""
    client, db = env
    db.create_session("c1", "research-bot", title="My investigation",
                       source="web")

    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({
            "action": "attach_session",
            "session_id": "c1",
            "channel": "telegram",
            "account_id": "default",
            "peer_kind": "direct",
            "peer_id": "*",
        }))
        for _ in range(50):  # tolerate any number of bootstrap frames
            env_msg = json.loads(ws.receive_text())
            if env_msg.get("type") == "session_alias_changed":
                break

    sess = db.get_session("c1")
    assert sess["title"] == "My investigation"
    assert sess["source"] == "web"
    assert sess["agent_id"] == "research-bot"


def test_attach_reports_replaced_binding(env) -> None:
    """Re-attaching the same (channel, account, peer) to a different
    session must surface the previous binding via ``replaced``,
    instead of silently dropping it."""
    client, db = env
    db.create_session("conv-a", "main", title="A", source="tui")
    db.create_session("conv-b", "main", title="B", source="tui")

    with client.websocket_connect("/ws") as ws:
        # First attach: peer=* → conv-a. No replaced.
        ws.send_text(json.dumps({
            "action": "attach_session",
            "session_id": "conv-a",
            "channel": "wechat",
            "account_id": "default",
            "peer_kind": "direct",
            "peer_id": "*",
        }))
        for _ in range(50):  # tolerate any number of bootstrap frames
            env_msg = json.loads(ws.receive_text())
            if env_msg.get("type") == "session_alias_changed":
                assert env_msg["data"].get("replaced") is None
                break

        # Second attach: same key → conv-b. ``replaced`` must point at
        # conv-a so the UI can warn the user.
        ws.send_text(json.dumps({
            "action": "attach_session",
            "session_id": "conv-b",
            "channel": "wechat",
            "account_id": "default",
            "peer_kind": "direct",
            "peer_id": "*",
        }))
        for _ in range(50):  # tolerate any number of bootstrap frames
            env_msg = json.loads(ws.receive_text())
            if env_msg.get("type") == "session_alias_changed":
                replaced = env_msg["data"].get("replaced")
                assert replaced is not None
                assert replaced["session_id"] == "conv-a"
                break


def test_attach_rejects_empty_session_id(env) -> None:
    client, _ = env
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({
            "action": "attach_session",
            "session_id": "",
            "channel": "wechat",
            "account_id": "default",
        }))
        for _ in range(50):  # tolerate any number of bootstrap frames
            env_msg = json.loads(ws.receive_text())
            if env_msg.get("type") == "error":
                assert "session_id" in env_msg["data"]["message"]
                return
        pytest.fail("expected an error envelope")

"""End-to-end test for the WebSocket reconnect + sync handshake.

Two scenarios covered here, matching what the real browser client does:

1. **Initial connect** — server pushes the four bootstrap frames in a
   stable order (full_tree, functions_list, history_list, provider_info)
   regardless of how many times the client reconnects.
2. **Sync after disconnect** — after a message has been committed into
   the MessageStore while the client was away, sending
   ``{"action": "sync", "conv_id", "known_seqs"}`` replays the frames
   the client missed. The client advertises what it already has via
   ``known_seqs``; the server returns deltas when the ring still holds
   them and a snapshot when it doesn't.

These use FastAPI's ``TestClient.websocket_connect``, which drives the
route in the same process without binding a real port.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from openprogram.webui.server import create_app
from openprogram.webui.messages import (
    Block,
    Message,
    MessageStore,
    set_store_for_testing,
)


# Four frame types the server guarantees on every /ws accept.
_BOOTSTRAP_TYPES = (
    "full_tree",
    "functions_list",
    "history_list",
    "provider_info",
)


@pytest.fixture
def client(tmp_path):
    """Fresh server app + isolated MessageStore per test.

    ``set_store_for_testing`` swaps in an empty store pointed at tmp_path,
    so commits in one test don't leak into the next.
    """
    store = MessageStore(persist_dir=tmp_path)
    set_store_for_testing(store)
    app = create_app()
    with TestClient(app) as c:
        yield c, store
    set_store_for_testing(None)


def _drain_bootstrap(ws) -> list[dict]:
    """Read exactly the initial four frames and return them."""
    frames = []
    for _ in _BOOTSTRAP_TYPES:
        raw = ws.receive_text()
        frames.append(json.loads(raw))
    return frames


def _seed_message(store: MessageStore, msg_id: str, conv_id: str, text: str) -> None:
    """Create + commit one finished assistant message into the store."""
    store.create(
        conv_id,
        "assistant",
        message_id=msg_id,
        content=[Block(type="text", text=text)],
    )
    store.commit(msg_id, status="complete")


def test_initial_connect_sends_bootstrap_frames(client):
    c, _ = client
    with c.websocket_connect("/ws") as ws:
        frames = _drain_bootstrap(ws)

    types = [f["type"] for f in frames]
    assert types == list(_BOOTSTRAP_TYPES), types


def test_reconnect_resends_bootstrap(client):
    """Closing and reopening re-issues the same bootstrap — no server-side
    memory that 'this client already got them'."""
    c, _ = client
    for _ in range(3):
        with c.websocket_connect("/ws") as ws:
            frames = _drain_bootstrap(ws)
        assert [f["type"] for f in frames] == list(_BOOTSTRAP_TYPES)


def test_ping_pong(client):
    c, _ = client
    with c.websocket_connect("/ws") as ws:
        _drain_bootstrap(ws)
        ws.send_text("ping")
        reply = json.loads(ws.receive_text())
    assert reply == {"type": "pong"}


def test_sync_returns_snapshot_when_client_knows_nothing(client):
    """Seed a committed message; sync with empty known_seqs returns a
    message.snapshot frame."""
    c, store = client
    _seed_message(store, "m1", "conv-abc", "hello")

    with c.websocket_connect("/ws") as ws:
        _drain_bootstrap(ws)
        ws.send_text(json.dumps({
            "action": "sync",
            "conv_id": "conv-abc",
            "known_seqs": {},
        }))
        frame = json.loads(ws.receive_text())

    assert frame["type"] == "chat_response"
    inner = frame["data"]
    assert inner["type"] == "message.snapshot"
    assert inner["message"]["id"] == "m1"


def test_sync_skips_messages_already_known(client):
    """If known_seqs includes this message at the current seq, server
    skips it. Verify via a follow-up ping that the channel is still live
    and nothing stale is buffered."""
    c, store = client
    _seed_message(store, "m2", "conv-xyz", "done")
    # After commit() the message's seq is whatever the store assigned —
    # read it back rather than assuming 0.
    committed = store.list_for_conv("conv-xyz")[0]

    with c.websocket_connect("/ws") as ws:
        _drain_bootstrap(ws)
        ws.send_text(json.dumps({
            "action": "sync",
            "conv_id": "conv-xyz",
            "known_seqs": {"m2": committed.seq},
        }))
        ws.send_text("ping")
        reply = json.loads(ws.receive_text())
    assert reply == {"type": "pong"}


def test_sync_with_unknown_conv_id_is_noop(client):
    """Sync against a conv the server has never seen must not crash."""
    c, _ = client
    with c.websocket_connect("/ws") as ws:
        _drain_bootstrap(ws)
        ws.send_text(json.dumps({
            "action": "sync",
            "conv_id": "conv-never-existed",
            "known_seqs": {},
        }))
        ws.send_text("ping")
        reply = json.loads(ws.receive_text())
    assert reply == {"type": "pong"}


def test_sync_missing_conv_id_is_ignored(client):
    """Malformed sync payload (no conv_id) must not break the session."""
    c, _ = client
    with c.websocket_connect("/ws") as ws:
        _drain_bootstrap(ws)
        ws.send_text(json.dumps({"action": "sync", "known_seqs": {}}))
        ws.send_text("ping")
        reply = json.loads(ws.receive_text())
    assert reply == {"type": "pong"}

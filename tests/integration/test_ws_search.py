"""WS action ``search_messages`` — FTS-backed cross-session search.

Wires the SessionDB FTS5 index to the frontend so TUI's /search and
the webui search bar can find old messages by content. Each result
carries enough context (session title, source, message id, preview)
for the UI to render a picker and resume into the matched conv.
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
    store = MessageStore(persist_dir=tmp_path / "store")
    set_store_for_testing(store)
    app = create_app()
    with TestClient(app) as client:
        yield client, db
    set_store_for_testing(None)


def _drain_bootstrap(ws) -> None:
    """Read until the final bootstrap frame (provider_info) — by type,
    not count, so bootstrap changes fail loudly instead of hanging."""
    import json as _json
    for _ in range(20):
        if _json.loads(ws.receive_text()).get("type") == "provider_info":
            return
    raise AssertionError("provider_info never arrived in bootstrap")


def test_search_returns_matching_messages(env) -> None:
    client, db = env
    db.create_session("c1", "main", title="Cooking notes")
    db.append_message("c1", {
        "id": "m1", "role": "user",
        "content": "How do I make 麻婆豆腐 from scratch?",
        "timestamp": 1.0, "predecessor": None,
    })
    db.append_message("c1", {
        "id": "m2", "role": "assistant",
        "content": "Heat oil, brown the pork mince, add doubanjiang...",
        "timestamp": 2.0, "predecessor": "m1",
    })
    db.create_session("c2", "main", title="Travel plans")
    db.append_message("c2", {
        "id": "m3", "role": "user",
        "content": "Best route from Beijing to Tokyo?",
        "timestamp": 3.0, "predecessor": None,
    })

    with client.websocket_connect("/ws") as ws:
        _drain_bootstrap(ws)
        ws.send_text(json.dumps({
            "action": "search_messages",
            "query": "doubanjiang",
        }))
        # The server may emit unrelated frames (e.g. bookkeeping).
        # Drain until we see search_results or run out of patience.
        frame = None
        for _ in range(10):
            raw = ws.receive_text()
            envelope = json.loads(raw)
            if envelope.get("type") == "search_results":
                frame = envelope
                break
        assert frame is not None, "expected a search_results frame"
        assert frame["data"]["query"] == "doubanjiang"
        assert frame["data"]["total"] == 1
        hit = frame["data"]["results"][0]
        assert hit["session_id"] == "c1"
        assert hit["session_title"] == "Cooking notes"
        assert hit["message_id"] == "m2"
        assert "doubanjiang" in hit["preview"]


def test_search_empty_query_returns_zero(env) -> None:
    client, db = env
    db.create_session("c1", "main")
    db.append_message("c1", {
        "id": "m1", "role": "user", "content": "hi",
        "timestamp": 1.0, "predecessor": None,
    })

    with client.websocket_connect("/ws") as ws:
        _drain_bootstrap(ws)
        ws.send_text(json.dumps({
            "action": "search_messages",
            "query": "   ",
        }))
        for _ in range(10):
            envelope = json.loads(ws.receive_text())
            if envelope.get("type") == "search_results":
                assert envelope["data"]["total"] == 0
                return
        pytest.fail("expected search_results")


def test_search_filters_by_agent(env) -> None:
    client, db = env
    db.create_session("c1", "main", title="A")
    db.append_message("c1", {
        "id": "m1", "role": "user", "content": "shared keyword zircon",
        "timestamp": 1.0, "predecessor": None,
    })
    db.create_session("c2", "research-bot", title="B")
    db.append_message("c2", {
        "id": "m2", "role": "user", "content": "shared keyword zircon",
        "timestamp": 2.0, "predecessor": None,
    })

    with client.websocket_connect("/ws") as ws:
        _drain_bootstrap(ws)
        ws.send_text(json.dumps({
            "action": "search_messages",
            "query": "zircon",
            "agent_id": "research-bot",
        }))
        for _ in range(10):
            envelope = json.loads(ws.receive_text())
            if envelope.get("type") == "search_results":
                assert envelope["data"]["total"] == 1
                assert envelope["data"]["results"][0]["session_id"] == "c2"
                return
        pytest.fail("expected search_results")

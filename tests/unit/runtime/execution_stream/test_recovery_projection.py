"""Public WebSocket recovery projection for nested LLM content."""

from __future__ import annotations

import asyncio
import json

from openprogram.state.store.session.nodes import Call, ROLE_LLM


class _WS:
    def __init__(self):
        self.frames: list[str] = []

    async def send_text(self, value: str) -> None:
        self.frames.append(value)


def test_owner_gone_subscription_returns_durable_ordered_snapshot(monkeypatch):
    from openprogram.interfaces.server.ws_actions import runtime as actions

    snapshot = {
        "generation": 2,
        "revision": 7,
        "phase": "running",
        "attempts": [{
            "attempt_id": "attempt-0",
            "attempt_index": 0,
            "status": "running",
            "blocks": [
                {"block_id": "before", "block_index": 0, "kind": "text",
                 "content": "Before", "status": "finished"},
                {"block_id": "tool", "block_index": 1, "kind": "tool_ref",
                 "tool_call_id": "call-1", "ref_node_id": "tool-1",
                 "status": "finished"},
                {"block_id": "after", "block_index": 2, "kind": "text",
                 "content": "After", "status": "running"},
            ],
        }],
    }
    node = Call(
        id="llm-1",
        role=ROLE_LLM,
        metadata={
            "status": "running",
            "stream": {
                "generation": 2,
                "revision": 7,
                "phase": "running",
                "snapshot": snapshot,
            },
        },
    )

    class _DB:
        def get_session(self, session_id):
            return {"id": session_id}

        def get_nodes(self, session_id):
            return [node]

    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: _DB())
    monkeypatch.setattr(
        "openprogram.runtime.agent.runtime.execution_stream.transport.lookup_owner",
        lambda *_args, **_kwargs: None,
    )
    ws = _WS()
    asyncio.run(actions.handle_subscribe_execution_stream(ws, {
        "session_id": "s1",
        "execution_id": "e1",
        "node_ids": ["llm-1"],
        "subscription_id": "sub-1",
    }))

    assert len(ws.frames) == 1
    data = json.loads(ws.frames[0])["data"]
    assert data["type"] == "execution_stream"
    assert data["op"] == "snapshot"
    assert data["durability"] == "checkpoint"
    assert data["snapshot"]["recovered_from_checkpoint"] is True
    blocks = data["snapshot"]["attempts"][0]["blocks"]
    assert [block["block_id"] for block in blocks] == ["before", "tool", "after"]
    assert blocks[1]["ref_node_id"] == "tool-1"

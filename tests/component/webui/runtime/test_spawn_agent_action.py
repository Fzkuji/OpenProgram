"""``spawn_agent`` answers at once and sends the result when the run ends."""
from __future__ import annotations

import asyncio
import json
import threading


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


def test_spawn_agent_does_not_hold_the_socket_for_the_run(monkeypatch):
    from openprogram.interfaces.server.ws_actions import sub_agent

    release = threading.Event()

    def _run(session_id, parent_msg_id, prompt, agent_id, label, context):
        assert release.wait(10)
        return {"context": context, "head_id": "h1", "final_text": "done",
                "failed": False, "error": None}

    monkeypatch.setattr(sub_agent, "_run", _run)
    observed: dict = {}

    async def _main():
        ws = _FakeWS()
        await sub_agent.handle_spawn_agent(ws, {
            "session_id": "s1", "parent_msg_id": "m1", "prompt": "look",
        })
        observed["after_return"] = list(ws.sent)
        release.set()
        await asyncio.gather(*sub_agent._spawn_tasks)
        observed["sent"] = ws.sent

    asyncio.run(_main())

    assert observed["after_return"] == []
    assert observed["sent"] == [{"type": "spawn_agent_result", "data": {
        "session_id": "s1", "parent_msg_id": "m1", "context": "inherit",
        "head_id": "h1", "final_text": "done", "failed": False, "error": None,
    }}]


def test_spawn_agent_rejects_a_bad_request_immediately():
    from openprogram.interfaces.server.ws_actions import sub_agent

    ws = _FakeWS()
    asyncio.run(sub_agent.handle_spawn_agent(ws, {"session_id": "s1", "prompt": ""}))

    assert ws.sent[0]["data"]["failed"] is True

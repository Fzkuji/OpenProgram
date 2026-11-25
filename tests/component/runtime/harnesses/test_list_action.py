"""``list_harnesses`` answers as a correlated reply the web can match.

The web's ``wsRequest`` with a request id takes a frame only when its data
echoes both the ``request_id`` and the ``action``; a reply missing either
waits out the timeout before the page falls back to HTTP.
"""
from __future__ import annotations

import asyncio
import json

from openprogram.interfaces.server.ws_actions import harnesses as action


class _Socket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


def test_the_reply_echoes_the_request_id_and_the_action(monkeypatch):
    import openprogram.runtime.harnesses.models as models

    monkeypatch.setattr(models, "list_harnesses", lambda refresh=False: [{"id": "codex"}])
    socket = _Socket()
    asyncio.run(action.handle_list_harnesses(socket, {"request_id": "r1", "session_id": "s1"}))
    [frame] = socket.sent
    assert frame["type"] == "harnesses_list"
    assert frame["data"]["request_id"] == "r1"
    assert frame["data"]["action"] == "list_harnesses"
    assert frame["data"]["harnesses"] == [{"id": "codex"}]

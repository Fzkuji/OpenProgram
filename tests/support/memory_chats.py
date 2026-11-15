"""A session store holding the chats memory tests cite.

Memory footnotes cite chat messages, and a new citation is checked against
the session store, so a test that writes memory needs the cited messages
to exist there. ``Chats`` stands in for it.
"""
from __future__ import annotations

from typing import Any

import pytest


class Chats:
    """In-memory sessions: ``say`` adds a message and returns its ref."""

    def __init__(self) -> None:
        self.sessions: dict[str, list[dict[str, Any]]] = {}

    def say(
        self, session_id: str, message_id: str, content: str, *,
        role: str = "user", timestamp: float = 1_786_000_000.0, **extra: Any,
    ) -> str:
        from openprogram.state.memory.chat_refs import ref

        self.sessions.setdefault(session_id, []).append({
            "id": message_id, "role": role, "content": content,
            "timestamp": timestamp, **extra,
        })
        return ref(session_id, message_id)

    def link(self, session_id: str, message_id: str, content: str = "said", **kw: Any) -> str:
        from openprogram.state.memory.chat_refs import link

        return link(self.say(session_id, message_id, content, **kw))

    def get_messages(self, session_id: str, *args: Any, **kwargs: Any):
        return list(self.sessions.get(session_id, []))

    def get_session(self, session_id: str):
        return {"id": session_id} if session_id in self.sessions else None

    def message_exists(self, session_id: str, message_id: str) -> bool:
        return any(row["id"] == message_id for row in self.sessions.get(session_id, []))


@pytest.fixture
def chats(monkeypatch) -> Chats:
    from openprogram.runtime.context import session_db

    store = Chats()
    monkeypatch.setattr(session_db, "default_db", lambda *a, **k: store)
    return store

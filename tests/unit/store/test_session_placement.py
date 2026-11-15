"""A chat dragged to a place in its project's list keeps that place's time.

``update_session_flags`` with ``sort_at`` stores the time and the activity
the chat was placed at (``sort_since``); the sidebar list carries both;
``sort_at`` 0 clears them.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openprogram.runtime.context.session_db import SessionDB
from openprogram.interfaces.server.ws_actions import session as ws_session


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    db = SessionDB(state / "projects")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: db)
    return db


def test_a_place_is_a_time_held_until_the_chats_next_activity(env, monkeypatch):
    from openprogram.interfaces.server import server as srv
    frames: list[dict] = []
    monkeypatch.setattr(srv, "_broadcast", lambda frame: frames.append(json.loads(frame)))
    env.create_session("s1", "main")
    env.append_message("s1", {"id": "u1", "role": "user", "content": "hi", "timestamp": 0, "predecessor": None})
    activity = env.get_session("s1")["updated_at"]
    assert activity

    asyncio.run(ws_session.handle_update_session_flags(FakeWS(), {"session_id": "s1", "sort_at": 1234.5}))
    row = env.get_session("s1")
    assert row["sort_at"] == 1234.5 and row["sort_since"] == activity
    assert row["updated_at"] == activity, "placing is not activity"
    listed = next(c for c in ws_session.build_sessions_list() if c["id"] == "s1")
    assert listed["sort_at"] == 1234.5 and listed["sort_since"] == activity
    assert frames[-1]["type"] == "session_updated" and frames[-1]["data"]["sort_at"] == 1234.5

    asyncio.run(ws_session.handle_update_session_flags(FakeWS(), {"session_id": "s1", "sort_at": 0}))
    row = env.get_session("s1")
    assert row["sort_at"] == 0 and row["sort_since"] == 0

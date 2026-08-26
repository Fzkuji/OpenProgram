from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from openprogram.agent import dispatcher as D
from openprogram.agent import steering
from openprogram.agent.session_db import SessionDB
from openprogram.providers.types import (
    AssistantMessage,
    EventDone,
    EventStart,
    Model,
    TextContent,
    Usage,
)


def _model() -> Model:
    return Model(
        id="stub",
        name="stub",
        api="completion",
        provider="openai",
        base_url="https://api.openai.com/v1",
    )


def _message(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        api="completion",
        provider="openai",
        model="stub",
        usage=Usage(input_tokens=1, output_tokens=1),
        stop_reason="stop",
        timestamp=int(time.time() * 1000),
    )


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    store = SessionDB(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store)
    monkeypatch.setattr(
        "openprogram.store.session.session_store.default_store",
        lambda: store,
    )
    monkeypatch.setattr("openprogram.store.default_store", lambda: store)
    monkeypatch.setattr(D, "_resolve_model", lambda profile, override=None: _model())
    monkeypatch.setattr(
        D,
        "_load_agent_profile",
        lambda agent_id: {"id": agent_id, "system_prompt": "help"},
    )
    return store


def test_running_chat_injects_and_persists_steering_user(db: SessionDB) -> None:
    db.create_session("chat-steer", "main", title="steer")
    assert steering.push("chat-steer", "use the shorter implementation")
    seen_user_texts: list[list[str]] = []

    async def stream(model, context, options):
        seen_user_texts.append([
            block.text
            for message in context.messages
            if getattr(message, "role", None) == "user"
            for block in getattr(message, "content", [])
            if isinstance(block, TextContent) and block.text
        ])
        partial = _message("")
        yield EventStart(partial=partial)
        yield EventDone(reason="stop", message=_message("done"))

    original = D._run_loop_blocking

    def run(**kwargs):
        return original(**kwargs, stream_fn=stream)

    events: list[dict] = []
    with patch.object(D, "_run_loop_blocking", run):
        result = D.process_user_turn(
            D.TurnRequest(
                session_id="chat-steer",
                user_text="start",
                agent_id="main",
                source="web",
            ),
            on_event=events.append,
        )

    assert result.failed is False
    assert seen_user_texts == [["start", "use the shorter implementation"]]
    branch = db.get_branch("chat-steer")
    assert [message["role"] for message in branch] == [
        "user",
        "user",
        "assistant",
    ]
    steered = branch[1]
    assert steered["content"] == "use the shorter implementation"
    assert steered["steering"] is True
    assert branch[2]["predecessor"] == steered["id"]
    pair = db._open("chat-steer")
    assert pair is not None
    _git, index = pair
    assert result.assistant_msg_id in index.children_by_predecessor[steered["id"]]
    assert result.assistant_msg_id not in index.children_by_predecessor.get(
        result.user_msg_id,
        [],
    )
    assert any(
        event.get("data", {}).get("type") == "user_message"
        and event["data"].get("steering") is True
        for event in events
    )
    assert steering.pending("chat-steer") is False


def test_turn_end_sweeps_leftovers_into_following_turns(db: SessionDB) -> None:
    calls: list[str] = []

    def run(**kwargs):
        req = kwargs["req"]
        calls.append(req.user_text)
        if len(calls) == 1:
            assert steering.push(req.session_id, "second")
            assert steering.push(req.session_id, "third")
        return f"answer {len(calls)}", {}, []

    with patch.object(D, "_run_loop_blocking", run):
        result = D.process_user_turn(
            D.TurnRequest(
                session_id="chat-sweep",
                user_text="first",
                agent_id="main",
                source="web",
            ),
        )

    assert result.final_text == "answer 1"
    assert calls == ["first", "second", "third"]
    assert [
        message["content"]
        for message in db.get_branch("chat-sweep")
        if message["role"] == "user"
    ] == ["first", "second", "third"]
    assert steering.pending("chat-sweep") is False

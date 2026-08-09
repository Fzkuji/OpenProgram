"""Memory sync runs AFTER the turn it is meant to count.

``sync_turn`` is the "a turn finished" trigger in
docs/reference/design/memory/overview.md. The provider deliberately
reads the turn back out of the session store rather than trusting the
arguments — the store is durable and ordered, which is what the write
cursor needs. That makes *when* the hook fires load-bearing: called
before the assistant row is persisted, the threshold check counts a
turn that is missing its reply, and every reply lands one turn late.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import patch

import pytest

from openprogram.agent import dispatcher as D
from openprogram.agent.session_db import SessionDB
from openprogram.providers.types import (
    AssistantMessage,
    AssistantMessageEvent,
    EventDone,
    EventStart,
    EventTextDelta,
    EventTextEnd,
    EventTextStart,
    Model,
    TextContent,
    Usage,
)

REPLY = "the answer is 42"


def _msg(text: str = "") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)] if text else [],
        api="completion", provider="openai", model="stub",
        timestamp=int(time.time() * 1000), usage=Usage(),
    )


async def _stream(model, context, options) -> AsyncGenerator[AssistantMessageEvent, None]:
    yield EventStart(partial=_msg(""))
    yield EventTextStart(content_index=0, partial=_msg(""))
    yield EventTextDelta(content_index=0, delta=REPLY, partial=_msg(REPLY))
    yield EventTextEnd(content_index=0, content=REPLY, partial=_msg(REPLY))
    final = _msg(REPLY)
    final.usage = Usage(input_tokens=1, output_tokens=1)
    yield EventDone(reason="stop", message=final)


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    return db


@pytest.fixture(autouse=True)
def stub_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(D, "_resolve_model", lambda profile, override=None: Model(
        id="stub", name="stub", api="completion", provider="openai",
        base_url="https://api.openai.com/v1",
    ))
    monkeypatch.setattr(D, "_load_agent_profile", lambda agent_id: {
        "id": agent_id, "system_prompt": "", "tools": [],
    })


def _run_turn(text: str, *, session_id: str):
    orig = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, stream_fn=None, **_extra):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=_stream)

    with patch.object(D, "_run_loop_blocking", _wrapped):
        return D.process_user_turn(D.TurnRequest(
            session_id=session_id, user_text=text, agent_id="main",
            source="tui",
        ))


@pytest.fixture
def spy(tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """A memory provider that snapshots the session store the moment
    ``sync_turn`` fires — the view the real provider goes on to read."""
    calls: list[dict] = []

    class _Provider:
        def prefetch(self, _text, **_kw): return ""
        def system_prompt_block(self): return ""

        def sync_turn(self, user, assistant, *, session_id=""):
            calls.append({
                "user": user, "assistant": assistant,
                "branch": tmp_db.get_branch(session_id),
            })

    monkeypatch.setattr("openprogram.memory.get_provider", lambda: _Provider())
    return calls


def _texts(branch: list[dict], role: str) -> list[str]:
    return [
        m.get("content") or "" for m in branch
        if m.get("role") == role and (m.get("content") or "").strip()
    ]


def test_this_turns_reply_is_already_in_the_store(spy: list[dict]):
    """The regression: the reply must count toward THIS turn's
    threshold check, not the next one's."""
    _run_turn("what is the answer", session_id="sync1")

    assert len(spy) == 1, "one memory offer per finished turn"
    branch = spy[0]["branch"]
    assert "what is the answer" in _texts(branch, "user")
    assert REPLY in _texts(branch, "assistant"), (
        "sync_turn fired while the assistant row was still the empty "
        "placeholder — the reply would be counted a turn late"
    )


def test_the_hook_still_carries_the_turns_text(spy: list[dict]):
    _run_turn("what is the answer", session_id="sync2")

    assert spy[0]["user"] == "what is the answer"
    assert spy[0]["assistant"] == REPLY


def test_no_session_id_no_memory_call(monkeypatch: pytest.MonkeyPatch):
    """Nothing to count a turn against — the hook stays out of it."""
    def _boom():
        raise AssertionError("memory must not be consulted without a session")

    monkeypatch.setattr("openprogram.memory.get_provider", _boom)
    D._memory_sync_turn("", "hi", "there")

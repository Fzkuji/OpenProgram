"""Multimodal + per-agent tool whitelist coverage for dispatcher.

Multimodal:
- ``TurnRequest.attachments`` carries image dicts; dispatcher attaches
  them as ImageContent blocks alongside the user text in the
  UserMessage handed to agent_loop.
- A lightweight attachment manifest lands in SessionDB so /resume
  and search can show "[N images]" badges without re-loading the
  base64 blob from rows.

Per-agent tool whitelist:
- ``agent.json``'s ``tools`` field already gates which tools the
  agent can see. Verified explicitly so a regression on
  ``_resolve_tools`` would be caught.
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
    ImageContent,
    Model,
    TextContent,
    Usage,
)


def _stub_model() -> Model:
    return Model(id="stub", name="stub", api="completion",
                 provider="openai", base_url="https://x")


def _build_partial(t: str = "") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=t)] if t else [],
        api="completion", provider="openai", model="stub",
        timestamp=int(time.time() * 1000),
    )


def _build_final(t: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=t)],
        api="completion", provider="openai", model="stub",
        usage=Usage(input=1, output=1), stop_reason="stop",
        timestamp=int(time.time() * 1000),
    )


def make_text_stream(text: str):
    async def _fn(model, ctx, opts) -> AsyncGenerator[AssistantMessageEvent, None]:
        yield EventStart(partial=_build_partial(""))
        yield EventTextStart(content_index=0, partial=_build_partial(""))
        yield EventTextDelta(content_index=0, delta=text, partial=_build_partial(text))
        yield EventTextEnd(content_index=0, content=text, partial=_build_partial(text))
        yield EventDone(reason="stop", message=_build_final(text))
    return _fn


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.agent.session_db.default_db",
                        lambda: db)
    return db


@pytest.fixture(autouse=True)
def stub_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(D, "_resolve_model",
                        lambda profile, override=None: _stub_model())


# ---------------------------------------------------------------------------
# Multimodal
# ---------------------------------------------------------------------------

def test_attachments_become_image_content_blocks(
    tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(D, "_load_agent_profile",
                        lambda agent_id: {"id": agent_id,
                                            "system_prompt": "",
                                            "tools": []})
    seen_messages: list = []

    async def _capturing(model, ctx, opts):
        seen_messages.append(list(ctx.messages))
        yield EventStart(partial=_build_partial(""))
        yield EventDone(reason="stop", message=_build_final("ack"))

    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, **_):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=_capturing)

    fake_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

    with patch.object(D, "_run_loop_blocking", _w):
        D.process_user_turn(
            D.TurnRequest(
                session_id="c1", user_text="What's in this image?",
                agent_id="main", source="tui",
                attachments=[{
                    "type": "image", "data": fake_b64,
                    "media_type": "image/png",
                }],
            ),
        )

    # The LLM context's user message should have a TextContent block
    # AND an ImageContent block.
    assert seen_messages
    msgs = seen_messages[0]
    user_msgs = [m for m in msgs if getattr(m, "role", "") == "user"]
    assert user_msgs
    last_user = user_msgs[-1]
    blocks = last_user.content
    types = [getattr(b, "type", None) for b in blocks]
    assert "text" in types
    assert "image" in types
    image_block = next(b for b in blocks if getattr(b, "type", "") == "image")
    assert image_block.data == fake_b64
    assert image_block.mime_type == "image/png"


def test_attachment_manifest_in_session_db(
    tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SessionDB stores a count + media_type manifest, NOT the base64
    payload itself. FTS5 index would explode if every image base64
    landed in messages.content."""
    monkeypatch.setattr(D, "_load_agent_profile",
                        lambda agent_id: {"id": agent_id,
                                            "system_prompt": "",
                                            "tools": []})
    fake = make_text_stream("ok")
    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, **_):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake)

    with patch.object(D, "_run_loop_blocking", _w):
        D.process_user_turn(
            D.TurnRequest(
                session_id="c1", user_text="caption pls",
                agent_id="main", source="tui",
                attachments=[
                    {"type": "image", "data": "AAAA", "media_type": "image/png"},
                    {"type": "image", "data": "BBBB", "media_type": "image/jpeg"},
                ],
            ),
        )
    msgs = tmp_db.get_messages("c1")
    user_msg = next(m for m in msgs if m["role"] == "user")
    # The base64 itself must NOT be in the row content (would bloat
    # FTS index)
    assert "AAAA" not in user_msg.get("content", "")
    assert "BBBB" not in user_msg.get("content", "")
    # _row_to_message hoists extra-JSON keys to top level, so the
    # manifest lives at user_msg["attachments"].
    manifest = user_msg.get("attachments")
    assert isinstance(manifest, list)
    assert len(manifest) == 2
    assert manifest[0]["media_type"] == "image/png"
    assert manifest[1]["media_type"] == "image/jpeg"


def test_malformed_attachment_skipped_gracefully(
    tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad attachment dict shouldn't abort the whole turn — just
    drop it and continue with the text."""
    monkeypatch.setattr(D, "_load_agent_profile",
                        lambda agent_id: {"id": agent_id,
                                            "system_prompt": "",
                                            "tools": []})
    fake = make_text_stream("ok")
    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, **_):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake)

    with patch.object(D, "_run_loop_blocking", _w):
        result = D.process_user_turn(
            D.TurnRequest(
                session_id="c1", user_text="hi",
                agent_id="main", source="tui",
                attachments=[
                    {"type": "image", "data": None},  # missing data
                    "not even a dict",
                    {"type": "unknown_kind", "data": "..."},
                ],
            ),
        )
    assert result.failed is False
    assert result.final_text == "ok"


# ---------------------------------------------------------------------------
# Per-agent tool whitelist
# ---------------------------------------------------------------------------

def test_agent_profile_tools_field_filters_registry(
    tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent.json declares ``tools: ["bash"]`` → dispatcher exposes
    only bash to agent_loop, even though the registry has 25+ tools."""
    monkeypatch.setattr(D, "_load_agent_profile",
                        lambda agent_id: {"id": agent_id,
                                            "system_prompt": "",
                                            "tools": ["bash"]})
    seen_tools: list = []

    async def _capturing(model, ctx, opts):
        seen_tools.append([t.name for t in (ctx.tools or [])])
        yield EventStart(partial=_build_partial(""))
        yield EventDone(reason="stop", message=_build_final("ok"))

    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, **_):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=_capturing)

    with patch.object(D, "_run_loop_blocking", _w):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="hi",
                          agent_id="main", source="tui",
                          permission_mode="bypass"),
        )
    assert seen_tools
    assert seen_tools[0] == ["bash"]


def test_per_turn_tools_override_beats_profile(
    tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A turn-level ``tools_override`` wins over agent.json. Channels
    use this to drop bash on per-message basis even when the agent
    profile would normally expose it."""
    monkeypatch.setattr(D, "_load_agent_profile",
                        lambda agent_id: {"id": agent_id,
                                            "system_prompt": "",
                                            "tools": ["bash", "read", "write"]})
    seen_tools: list = []

    async def _capturing(model, ctx, opts):
        seen_tools.append([t.name for t in (ctx.tools or [])])
        yield EventStart(partial=_build_partial(""))
        yield EventDone(reason="stop", message=_build_final("ok"))

    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, **_):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=_capturing)

    with patch.object(D, "_run_loop_blocking", _w):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="hi",
                          agent_id="main", source="tui",
                          tools_override=["read"],   # narrower than profile
                          permission_mode="bypass"),
        )
    assert seen_tools
    assert seen_tools[0] == ["read"]

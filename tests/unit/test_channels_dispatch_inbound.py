"""Channels' ``dispatch_inbound`` now runs through the unified
dispatcher. These tests prove the wiring without exercising any real
network channel.

Why this exists: before task #6, channels.dispatch_inbound called
``runtime.exec`` directly and bypassed agent_loop entirely — it had
no tools, no streaming, and used a separate JSON-file persistence
layer. We now route through ``agent.dispatcher.process_user_turn``
so wechat / telegram / discord / slack get the same capabilities as
the TUI and the web client.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import patch

import pytest

from openprogram.agent import dispatcher as D
from openprogram.agent.session_db import SessionDB
from openprogram.channels import _conversation as C
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


def _stub_model() -> Model:
    return Model(
        id="stub", name="stub", api="completion",
        provider="openai", base_url="https://x",
    )


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
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    return db


@pytest.fixture(autouse=True)
def stub_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(D, "_resolve_model",
                        lambda profile, override=None: _stub_model())


@pytest.fixture(autouse=True)
def stub_agent_profile(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(D, "_load_agent_profile",
                        lambda agent_id: {"id": agent_id,
                                            "system_prompt": "",
                                            "tools": []})


@pytest.fixture
def stub_routing(monkeypatch: pytest.MonkeyPatch):
    """Pretend wechat/<account>/<peer> always routes to ``main``,
    and ``main`` is a known agent with a ``per-account-channel-peer``
    session scope (the channels default)."""
    monkeypatch.setattr("openprogram.channels.bindings.route",
                        lambda channel, account_id, peer: "main")

    class _StubAgent:
        id = "main"
        session_scope = "per-account-channel-peer"
        session_daily_reset = ""
        session_idle_minutes = 0

    monkeypatch.setattr("openprogram.agents.manager.get",
                        lambda agent_id: _StubAgent())
    monkeypatch.setattr(
        "openprogram.agents.session_aliases.lookup",
        lambda channel, account_id, peer: None,
    )

    # The session-init helper writes a meta.json under the agent's
    # sessions dir; redirect that to tmp so we don't pollute ~/.
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr("openprogram.agents.manager.sessions_dir",
                        lambda agent_id: tmp / agent_id)


def test_dispatch_inbound_persists_via_session_db(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_stream = make_text_stream("Hello from agent")
    orig_run = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, **_):
        return orig_run(req=req, history=history, on_event=on_event,
                        cancel_event=cancel_event, stream_fn=fake_stream)

    with patch.object(D, "_run_loop_blocking", _wrapped):
        reply = C.dispatch_inbound(
            channel="wechat",
            account_id="acct1",
            peer_kind="direct",
            peer_id="alice",
            user_text="hi there",
            user_display="Alice",
        )
    assert reply == "Hello from agent"

    # Locate the session row that channels.dispatch_inbound created
    sessions = tmp_db.list_sessions()
    assert len(sessions) == 1
    sess = sessions[0]
    assert sess["agent_id"] == "main"
    assert sess["channel"] == "wechat"

    msgs = tmp_db.get_messages(sess["id"])
    # process_user_turn appends user + assistant; channels no longer
    # double-writes via the legacy path.
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant"]
    assert msgs[0]["content"] == "hi there"
    assert msgs[1]["content"] == "Hello from agent"


def test_dispatch_inbound_broadcasts_channel_turn(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The webui keeps a stale ``channel_turn`` envelope hook so an
    attached TUI updates without a /resume. Verify dispatch_inbound
    still emits it after the dispatcher refactor."""
    import sys, types
    broadcasts: list[str] = []
    fake_srv = types.SimpleNamespace(_broadcast=lambda payload: broadcasts.append(payload))
    monkeypatch.setitem(sys.modules, "openprogram.webui.server", fake_srv)

    fake_stream = make_text_stream("ok")
    orig_run = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, **_):
        return orig_run(req=req, history=history, on_event=on_event,
                        cancel_event=cancel_event, stream_fn=fake_stream)

    with patch.object(D, "_run_loop_blocking", _wrapped):
        C.dispatch_inbound(
            channel="wechat", account_id="acct1",
            peer_kind="direct", peer_id="alice",
            user_text="ping", user_display="Alice",
        )

    # At least one channel_turn envelope and one chat_response stream
    # event must have been broadcast through the webui.
    has_channel_turn = any('"channel_turn"' in p for p in broadcasts)
    has_chat_response = any('"chat_response"' in p for p in broadcasts)
    assert has_channel_turn, "channel_turn envelope missing — TUI live update will break"
    assert has_chat_response, "chat_response stream events missing"


def test_dispatch_inbound_replay_continues_session(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two inbound messages from the same peer end up in the same
    SessionDB row. (Before task #6 they did too, but via the legacy
    JSON-file path — confirm the behavior survived the rewrite.)"""
    fake = make_text_stream("ack")
    orig_run = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, **_):
        return orig_run(req=req, history=history, on_event=on_event,
                        cancel_event=cancel_event, stream_fn=fake)

    with patch.object(D, "_run_loop_blocking", _wrapped):
        C.dispatch_inbound(
            channel="wechat", account_id="a", peer_kind="direct",
            peer_id="bob", user_text="one", user_display="Bob",
        )
        C.dispatch_inbound(
            channel="wechat", account_id="a", peer_kind="direct",
            peer_id="bob", user_text="two", user_display="Bob",
        )

    sessions = tmp_db.list_sessions()
    assert len(sessions) == 1
    msgs = tmp_db.get_messages(sessions[0]["id"])
    assert [m["role"] for m in msgs] == [
        "user", "assistant", "user", "assistant",
    ]
    assert msgs[0]["content"] == "one"
    assert msgs[2]["content"] == "two"


def test_dispatch_inbound_uses_bound_session_run_config(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_db.create_session(
        "local_bound",
        "main",
        title="Bound session",
        tools_enabled=False,
        thinking_effort="high",
        permission_mode="bypass",
    )
    monkeypatch.setattr(
        "openprogram.agents.session_aliases.lookup",
        lambda channel, account_id, peer: ("main", "local_bound"),
    )

    seen: dict[str, object] = {}

    async def _capturing(model, ctx, opts):
        seen["tools"] = [t.name for t in (ctx.tools or [])]
        seen["reasoning"] = getattr(opts, "reasoning", None)
        yield EventStart(partial=_build_partial(""))
        yield EventDone(reason="stop", message=_build_final("configured"))

    orig_run = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, **_):
        seen["permission_mode"] = req.permission_mode
        return orig_run(req=req, history=history, on_event=on_event,
                        cancel_event=cancel_event, stream_fn=_capturing)

    with patch.object(D, "_run_loop_blocking", _wrapped):
        reply = C.dispatch_inbound(
            channel="wechat",
            account_id="acct1",
            peer_kind="direct",
            peer_id="alice",
            user_text="hi",
            user_display="Alice",
        )

    assert reply == "configured"
    assert seen["tools"] == []
    assert seen["reasoning"] == "high"
    assert seen["permission_mode"] == "bypass"
    msgs = tmp_db.get_messages("local_bound")
    assert [m["role"] for m in msgs] == ["user", "assistant"]

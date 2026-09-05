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

    monkeypatch.setattr("openprogram.agent.management.manager.get",
                        lambda agent_id: _StubAgent())
    monkeypatch.setattr(
        "openprogram.agent.management.session_aliases.lookup",
        lambda channel, account_id, peer: None,
    )

    # The session-init helper writes a meta.json under the agent's
    # sessions dir; redirect that to tmp so we don't pollute ~/.
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr("openprogram.agent.management.manager.sessions_dir",
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
            peer_id="group-42",
            user_text="hi there",
            user_display="Alice",
            speaker_id="user-7",
            speaker_display="Alice",
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
    assert msgs[0]["peer_id"] == "group-42"
    assert msgs[0]["speaker_id"] == "user-7"
    assert msgs[0]["speaker_display"] == "Alice"
    assert msgs[1]["content"] == "Hello from agent"


def test_turn_request_keeps_existing_positional_field_order() -> None:
    from openprogram.agent.dispatcher import TurnRequest

    request = TurnRequest(
        "session", "text", "agent", "tui", "Peer", "peer-id",
        "model-name", "high",
    )

    assert request.model_override == "model-name"
    assert request.thinking_effort == "high"
    assert request.speaker_id is None
    assert request.speaker_display is None


def test_dispatch_inbound_broadcasts_channel_turn(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The webui keeps a stale ``channel_turn`` envelope hook so an
    attached TUI updates without a /resume. Verify dispatch_inbound
    still emits it after the dispatcher refactor."""
    # channel_turn 与流式 chat_response 帧都走总线（emit_ws_frame →
    # ws.frame 事件），webui 订阅后原样广播 — channels 不再摸
    # webui.server._broadcast 私有函数。订阅总线抓全部帧。
    from openprogram.events import get_event_bus, WS_FRAME_EVENT
    frames: list[dict] = []
    unsub = get_event_bus().subscribe(
        lambda ev: frames.append(ev.payload.get("frame", {})),
        types={WS_FRAME_EVENT},
    )

    fake_stream = make_text_stream("ok")
    orig_run = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, **_):
        return orig_run(req=req, history=history, on_event=on_event,
                        cancel_event=cancel_event, stream_fn=fake_stream)

    try:
        with patch.object(D, "_run_loop_blocking", _wrapped):
            C.dispatch_inbound(
                channel="wechat", account_id="acct1",
                peer_kind="direct", peer_id="alice",
                user_text="ping", user_display="Alice",
                speaker_id="alice",
            )
    finally:
        unsub()

    # channel_turn 与 chat_response 都要在总线上。
    has_channel_turn = any(f.get("type") == "channel_turn" for f in frames)
    has_chat_response = any(f.get("type") == "chat_response" for f in frames)
    assert has_channel_turn, "channel_turn envelope missing — TUI live update will break"
    assert has_chat_response, "chat_response stream events missing from the bus"


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
            speaker_id="bob",
        )
        C.dispatch_inbound(
            channel="wechat", account_id="a", peer_kind="direct",
            peer_id="bob", user_text="two", user_display="Bob",
            speaker_id="bob",
        )

    sessions = tmp_db.list_sessions()
    assert len(sessions) == 1
    msgs = tmp_db.get_messages(sessions[0]["id"])
    assert [m["role"] for m in msgs] == [
        "user", "assistant", "user", "assistant",
    ]
    assert msgs[0]["content"] == "one"
    assert msgs[2]["content"] == "two"


def test_same_session_concurrent_turns_serialize(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two concurrent inbound messages for the SAME session must run
    one after the other — the dispatcher has no lock of its own, so
    interleaved turns corrupt the session history."""
    import threading
    from types import SimpleNamespace

    intervals: list[tuple[float, float]] = []

    def fake_turn(req, on_event=None):
        t0 = time.monotonic()
        time.sleep(0.25)
        intervals.append((t0, time.monotonic()))
        return SimpleNamespace(final_text="ok", user_msg_id="u",
                               assistant_msg_id="a")

    monkeypatch.setattr(D, "process_user_turn", fake_turn)

    def _one(i: int) -> None:
        C.dispatch_inbound(
            channel="wechat", account_id="a", peer_kind="direct",
            peer_id="carol", user_text=f"msg {i}", user_display="Carol",
            speaker_id="carol",
        )

    threads = [threading.Thread(target=_one, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads)

    assert len(intervals) == 2
    (a0, a1), (b0, b1) = sorted(intervals)
    assert a1 <= b0, (
        f"turns overlapped: first ran {a0:.3f}-{a1:.3f}, "
        f"second started at {b0:.3f}"
    )


def test_different_sessions_run_in_parallel(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-session lock must not serialize UNRELATED sessions: two
    peers' turns overlap (both reach the barrier while in-flight)."""
    import threading
    from types import SimpleNamespace

    barrier = threading.Barrier(2)
    met: list[bool] = []

    def fake_turn(req, on_event=None):
        barrier.wait(timeout=5)   # breaks if the other turn is locked out
        met.append(True)
        return SimpleNamespace(final_text="ok", user_msg_id="u",
                               assistant_msg_id="a")

    monkeypatch.setattr(D, "process_user_turn", fake_turn)

    def _one(peer: str) -> None:
        C.dispatch_inbound(
            channel="wechat", account_id="a", peer_kind="direct",
            peer_id=peer, user_text="hi", user_display=peer,
            speaker_id=peer,
        )

    threads = [threading.Thread(target=_one, args=(p,))
               for p in ("dave", "erin")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert met == [True, True], "different sessions were serialized"


def test_dispatch_inbound_uses_bound_session_run_config(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
) -> None:
    supported_model = _stub_model().model_copy(update={
        "reasoning": True,
        "thinking_levels": ["low", "high"],
    })
    monkeypatch.setattr(
        D,
        "_resolve_model",
        lambda profile, override=None: supported_model,
    )
    tmp_db.create_session(
        "local_bound",
        "main",
        title="Bound session",
        tools_enabled=False,
        thinking_effort="high",
        permission_mode="bypass",
    )
    monkeypatch.setattr(
        "openprogram.agent.management.session_aliases.lookup",
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
            speaker_id="alice",
        )

    assert reply == "configured"
    assert seen["tools"] == []
    assert seen["reasoning"] == "high"
    assert seen["permission_mode"] == "bypass"
    msgs = tmp_db.get_messages("local_bound")
    assert [m["role"] for m in msgs] == ["user", "assistant"]

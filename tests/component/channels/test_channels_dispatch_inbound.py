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

from openprogram.runtime.conversation import turn_runtime as D
from openprogram.runtime.agent_registry.manager import AgentModelRef
from openprogram.runtime.context.session_db import SessionDB
from openprogram.interfaces.channels import _conversation as C
from openprogram.interfaces.channels._message import ChannelMessage
from openprogram.interfaces.channels._transport import SendResult
from openprogram.interfaces.channels.base import Channel
from openprogram.models.providers.types import (
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
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
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
    monkeypatch.setattr("openprogram.interfaces.channels.bindings.route",
                        lambda channel, account_id, peer: "main")

    class _StubAgent:
        id = "main"
        session_scope = "per-account-channel-peer"
        session_daily_reset = ""
        session_idle_minutes = 0
        # Branch settings fall back to the Agent's own model, effort and
        # tool policy; this Agent names none of them.
        model = AgentModelRef()
        thinking_effort = None
        tools = None
        always_on: dict = {}

    monkeypatch.setattr("openprogram.runtime.agent_registry.manager.get",
                        lambda agent_id: _StubAgent())
    monkeypatch.setattr(
        "openprogram.runtime.agent_registry.session_aliases.lookup",
        lambda channel, account_id, peer: None,
    )

    # The session-init helper writes a meta.json under the agent's
    # sessions dir; redirect that to tmp so we don't pollute ~/.
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr("openprogram.runtime.agent_registry.manager.sessions_dir",
                        lambda agent_id: tmp / agent_id)


def _with_browser_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """The browser application is loaded, whatever earlier tests left: the
    conversation's own turn then carries the screen tool at the end of its
    tools array (refused without a Page)."""
    from openprogram.programs._host import services

    lookup = services.lookup
    monkeypatch.setattr(services, "lookup",
                        lambda name: object() if name == "browser" else lookup(name))


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
    from openprogram.runtime.agent.turn_request import TurnRequest

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
    from openprogram.runtime.events import get_event_bus, WS_FRAME_EVENT
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
                               assistant_msg_id="a", attached_files=[])

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
                               assistant_msg_id="a", attached_files=[])

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
        thinking_effort="high",
        permission_mode="bypass",
    )
    monkeypatch.setattr(
        "openprogram.runtime.agent_registry.session_aliases.lookup",
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
    # An Agent with no tools gets none, not even the screen tool.
    assert seen["tools"] == []
    assert seen["reasoning"] == "high"
    assert seen["permission_mode"] == "bypass"
    msgs = tmp_db.get_messages("local_bound")
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def make_delta_stream(parts: list[str]):
    text = "".join(parts)

    async def _fn(model, ctx, opts) -> AsyncGenerator[AssistantMessageEvent, None]:
        yield EventStart(partial=_build_partial(""))
        yield EventTextStart(content_index=0, partial=_build_partial(""))
        so_far = ""
        for part in parts:
            so_far += part
            yield EventTextDelta(content_index=0, delta=part,
                                 partial=_build_partial(so_far))
        yield EventTextEnd(content_index=0, content=text, partial=_build_partial(text))
        yield EventDone(reason="stop", message=_build_final(text))
    return _fn


def test_block_stream_sends_paragraphs_as_they_arrive(
    tmp_db: SessionDB, stub_routing, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    first = "第一段" * 80
    fake_stream = make_delta_stream([first, "\n\n第二段", "收尾。"])
    orig_run = D._run_loop_blocking
    posted: list[str] = []
    from openprogram.interfaces.channels._transport import SendResult
    monkeypatch.setattr(
        "openprogram.interfaces.channels._transport.post_message",
        lambda platform, account_id, target, text: posted.append(text)
        or SendResult.success(""),
    )

    def _wrapped(*, req, history, on_event, cancel_event, **_):
        return orig_run(req=req, history=history, on_event=on_event,
                        cancel_event=cancel_event, stream_fn=fake_stream)

    with patch.object(D, "_run_loop_blocking", _wrapped):
        reply = C.dispatch_inbound(
            channel="wechat", account_id="acct1", peer_kind="direct",
            peer_id="u-1", user_text="hi", block_stream=True,
            speaker_id="u-1",
        )
    assert reply is None
    assert posted == [first, "第二段收尾。"]
    [session] = tmp_db.list_sessions()
    assert tmp_db.get_messages(session["id"])[1]["content"] == first + "\n\n第二段收尾。"


def test_new_conversation_moves_the_peer_to_a_fresh_session(
    tmp_db: SessionDB, stub_routing, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    orig_run = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, **_):
        return orig_run(req=req, history=history, on_event=on_event,
                        cancel_event=cancel_event,
                        stream_fn=make_text_stream("ok"))

    def turn():
        return C.dispatch_inbound(channel="wechat", account_id="acct1",
                                  peer_kind="direct", peer_id="u-1",
                                  user_text="hi", speaker_id="u-1")

    with patch.object(D, "_run_loop_blocking", _wrapped):
        turn()
        receipt = C.start_new_conversation(
            channel="wechat", account_id="acct1",
            peer_kind="direct", peer_id="u-1")
        turn()
    assert receipt.startswith("✓")
    ids = sorted(s["id"] for s in tmp_db.list_sessions())
    assert ids == ["acct1_direct_u-1", "acct1_direct_u-1_n1"]


def test_ambient_room_event_speaks_only_through_chat_send(
    tmp_db: SessionDB, stub_routing, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A room event's final reply stays in the transcript; the model's
    chat_send call is what reaches the room, addressed to this turn's chat.
    The room's /memory setting narrows the turn's memory."""
    from openprogram.interfaces.channels import _active_turns
    from openprogram.interfaces.channels._transport import SendResult
    from openprogram.models.providers.types import ToolCall

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(D, "_load_agent_profile", lambda agent_id: {
        "id": agent_id, "system_prompt": "", "tools": ["chat_send"]})
    _with_browser_app(monkeypatch)
    posted: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(
        "openprogram.interfaces.channels._transport.post_message",
        lambda platform, account_id, target, text: posted.append(
            (platform, account_id, target, text)) or SendResult.success(""),
    )
    calls: list[list[str]] = []

    async def _scripted(model, ctx, opts):
        calls.append([t.name for t in (ctx.tools or [])])
        yield EventStart(partial=_build_partial(""))
        if len(calls) == 1:
            message = AssistantMessage(
                content=[ToolCall(id="c1", name="chat_send",
                                  arguments={"text": "Lunch is at noon."})],
                api="completion", provider="openai", model="stub",
                usage=Usage(input=1, output=1), stop_reason="toolUse",
                timestamp=int(time.time() * 1000),
            )
            yield EventDone(reason="toolUse", message=message)
        else:
            yield EventDone(reason="stop", message=_build_final("answered the lunch question"))

    seen: dict[str, object] = {}
    orig_run = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, **_):
        seen["memory"] = req.memory_policy_override
        return orig_run(req=req, history=history, on_event=on_event,
                        cancel_event=cancel_event, stream_fn=_scripted)

    with patch.object(D, "_run_loop_blocking", _wrapped):
        reply = C.dispatch_inbound(
            channel="telegram", account_id="acct1", peer_kind="group",
            peer_id="-100", user_text="[room event]\nwhen is lunch?",
            speaker_id="701", speaker_display="Ada",
            progress_stream=True, ambient=True, memory_mode="off",
        )

    assert reply is None
    # The screen tool stays at the end of the array, refused without a Page.
    assert calls[0] == ["chat_send", "computer_use_agent"]
    assert posted == [("telegram", "acct1", "-100", "Lunch is at noon.")]
    assert seen["memory"] == {"mode": "off"}
    [session] = tmp_db.list_sessions()
    contents = [m.get("content") for m in tmp_db.get_messages(session["id"])]
    assert "answered the lunch question" in contents
    assert _active_turns.get(session["id"]) is None


def _capture_turn(monkeypatch: pytest.MonkeyPatch) -> dict:
    seen: dict = {}
    orig_run = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, **_):
        seen["req"] = req
        return orig_run(req=req, history=history, on_event=on_event,
                        cancel_event=cancel_event,
                        stream_fn=make_text_stream("ok"))

    monkeypatch.setattr(D, "_run_loop_blocking", _wrapped)
    return seen


def test_chat_senders_act_as_owner_in_direct_messages(
    tmp_db: SessionDB, stub_routing, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bound sender acts as the owner unless the account says otherwise. In a
    group the caller says whether the sender has full access; without that the
    turn stays on the limited tier."""
    from openprogram.runtime.permissions.authority import owner_principal_id
    from openprogram.runtime.conversation.session_config import load_session_run_config

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    seen = _capture_turn(monkeypatch)

    C.dispatch_inbound(channel="wechat", account_id="acct1",
                       peer_kind="direct", peer_id="u-0", user_text="hi",
                       speaker_id="u-0", speaker_display="Friend")
    assert (seen["req"].authority_tier, seen["req"].speaker_kind) == ("owner", "owner")

    C.dispatch_inbound(channel="wechat", account_id="acct1",
                       peer_kind="direct", peer_id="u-1", user_text="hi",
                       speaker_id="u-1", speaker_display="Me")
    req = seen["req"]
    assert (req.authority_tier, req.speaker_kind, req.interaction) == (
        "owner", "owner", "interactive")
    assert req.principal_id == owner_principal_id()
    assert (req.speaker_id, req.speaker_display) == ("u-1", "Me")
    assert load_session_run_config(req.session_id).sandbox_enabled is None

    C.dispatch_inbound(channel="telegram", account_id="acct1",
                       peer_kind="group", peer_id="-100", user_text="hi",
                       speaker_id="701", speaker_display="Ada")
    room = seen["req"]
    assert room.authority_tier == "paired"
    assert room.peer_kind == "group"
    assert load_session_run_config(room.session_id).sandbox_enabled is None

    C.dispatch_inbound(channel="telegram", account_id="acct1",
                       peer_kind="group", peer_id="-100", user_text="again",
                       speaker_id="701", speaker_display="Ada",
                       room_full_access=True)
    assert (seen["req"].authority_tier, seen["req"].speaker_kind) == ("owner", "owner")


def test_paired_account_caps_full_access_in_a_group(
    tmp_db: SessionDB, stub_routing, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    monkeypatch.setattr("openprogram.interfaces.channels.accounts.sender_access",
                        lambda channel, account_id: "paired")
    seen = _capture_turn(monkeypatch)
    C.dispatch_inbound(channel="telegram", account_id="acct1",
                       peer_kind="group", peer_id="-100", user_text="hi",
                       speaker_id="701", speaker_display="Ada",
                       room_full_access=True)
    assert seen["req"].authority_tier == "paired"


class _GroupChannel(Channel):
    """A real inbound path with no network: replies are recorded."""

    def __init__(self, platform: str) -> None:
        super().__init__(account_id="acct1")
        self.platform_id = platform
        self.sent: list[str] = []

    def run(self, stop) -> None:  # pragma: no cover
        pass

    def send_text_full(self, target, text, thread_id=None):
        self.sent.append(text)
        return SendResult.success("mid")


@pytest.mark.parametrize("platform,chat_type", [("telegram", "group"), ("discord", "channel")])
def test_group_sender_outcomes_reach_the_turn_with_the_right_tier(
    tmp_db: SessionDB, stub_routing, tmp_path: Path, wire,
    monkeypatch: pytest.MonkeyPatch, platform: str, chat_type: str,
) -> None:
    from openprogram.interfaces.channels import _access, _room

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    _room.reset_state()
    _access.approve_user(platform, "acct1", "100", display="Me")
    _access.add_group_user(platform, "acct1", "200", "full")
    _access.add_group_user(platform, "acct1", "300", "chat")
    seen = _capture_turn(monkeypatch)
    channel = _GroupChannel(platform)

    def send(user: str) -> None:
        channel._dispatch_and_reply(ChannelMessage(
            text="hi", chat_id="9000", user_id=user, user_display=f"user{user}",
            chat_type=chat_type, message_id=f"m{user}",
        ))

    for user, tier in (("100", "owner"), ("200", "owner"), ("300", "paired")):
        send(user)
        assert seen["req"].speaker_id == user
        assert seen["req"].authority_tier == tier

    seen.clear()
    sessions_before = tmp_db.list_sessions()
    posts_before, sent_before = len(wire["post"]), list(channel.sent)
    send("400")
    assert "req" not in seen
    assert tmp_db.list_sessions() == sessions_before
    assert len(wire["post"]) == posts_before
    assert channel.sent == sent_before
    assert _access.describe(platform, "acct1")["pending"] == {}
    _room.reset_state()


def test_paired_sender_access_keeps_the_paired_tier(
    tmp_db: SessionDB, stub_routing, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.runtime.conversation.session_config import load_session_run_config

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    monkeypatch.setattr("openprogram.interfaces.channels.accounts.sender_access",
                        lambda channel, account_id: "paired")
    seen = _capture_turn(monkeypatch)
    C.dispatch_inbound(channel="telegram", account_id="acct1",
                       peer_kind="group", peer_id="-100", user_text="hi",
                       speaker_id="701", speaker_display="Ada")
    req = seen["req"]
    assert (req.authority_tier, req.speaker_kind) == ("paired", "human")
    assert load_session_run_config(req.session_id).sandbox_enabled is None


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch):
    """Fake transport: placeholder posts succeed, edits fail unless told."""
    from openprogram.interfaces.channels._transport import SendResult
    calls: dict = {"post": [], "patch": [],
                   "patch_result": SendResult.fail("bad_target", "gone")}

    def post(platform, account_id, target, text, thread_id=None):
        calls["post"].append((text, thread_id))
        return SendResult.success(f"m{len(calls['post'])}")

    def patch_(platform, account_id, target, message_id, text):
        calls["patch"].append(text)
        return calls["patch_result"]

    monkeypatch.setattr("openprogram.interfaces.channels._transport.post_message", post)
    monkeypatch.setattr("openprogram.interfaces.channels._transport.patch_message", patch_)
    return calls


def test_reply_reaches_the_thread_when_the_edit_and_broadcast_fail(
    tmp_db: SessionDB, stub_routing, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, wire,
) -> None:
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)

    def broken(*a, **k):
        raise RuntimeError("webui gone")

    monkeypatch.setattr(C, "_broadcast_channel_turn", broken)
    monkeypatch.setattr(C, "_poke_live_webui", broken)
    orig_run = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, **_):
        return orig_run(req=req, history=history, on_event=on_event,
                        cancel_event=cancel_event,
                        stream_fn=make_text_stream("the answer"))

    with patch.object(D, "_run_loop_blocking", _wrapped):
        reply = C.dispatch_inbound(
            channel="slack", account_id="acct1", peer_kind="channel",
            peer_id="C1_U1", user_text="hi", speaker_id="U1",
            progress_stream=True, thread_id="1.5",
        )
    assert reply is None
    assert wire["patch"][-1] == "the answer"          # tried the placeholder
    assert wire["post"] == [("⏳ working...", "1.5"), ("the answer", "1.5")]
    # The thread does not change which session the turn lands in.
    [session] = tmp_db.list_sessions()
    assert session["id"] == "slack_acct1_channel_C1_U1"


def test_a_failure_after_the_placeholder_is_edited_into_it(
    tmp_db: SessionDB, stub_routing, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, wire,
) -> None:
    from openprogram.interfaces.channels._transport import SendResult
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    wire["patch_result"] = SendResult.success("m1")

    def boom(mode):
        raise RuntimeError("boom")

    monkeypatch.setattr("openprogram.interfaces.channels._room.memory_override", boom)
    reply = C.dispatch_inbound(
        channel="telegram", account_id="acct1", peer_kind="direct",
        peer_id="42", user_text="hi", speaker_id="42", progress_stream=True,
    )
    assert reply is None
    assert wire["patch"] == ["[error] RuntimeError: boom"]
    assert wire["post"] == [("⏳ working...", None)]


def test_a_session_load_failure_comes_back_as_an_error_reply(
    stub_routing, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wire,
) -> None:
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)

    def boom(**kw):
        raise OSError("disk full")

    monkeypatch.setattr(C, "_load_or_init_session", boom)
    reply = C.dispatch_inbound(
        channel="telegram", account_id="acct1", peer_kind="direct",
        peer_id="42", user_text="hi", speaker_id="42", progress_stream=True,
    )
    assert reply == "[error] OSError: disk full"
    assert wire["post"] == []


def test_room_list_writes_wait_for_the_cross_process_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    from openprogram.interfaces.channels import _room
    from openprogram.models.auth.credentials import _private_file_lock

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    path = _room._rooms_path("telegram", "a1")
    writer = threading.Thread(
        target=_room.set_room_user, args=("telegram", "a1", "-100", "200", "full"),
        daemon=True,
    )
    with _private_file_lock(path, root=state, timeout=10):
        writer.start()
        writer.join(0.2)
        assert writer.is_alive() and not path.exists()
    writer.join(5)
    assert not writer.is_alive()
    assert _room.room_user_lists("telegram", "a1", "-100")["group_full_access"] == ["200"]


@pytest.mark.parametrize("block_stream", [False, True])
def test_an_inbound_turn_uploads_only_what_it_recorded(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch, block_stream: bool,
) -> None:
    from types import SimpleNamespace

    from openprogram.interfaces.channels._transport import SendResult
    from openprogram.state.attachments import format_marker

    uploaded: list[str] = []

    def _post_file(_channel, _account, _target, path, caption="", thread_id=None):
        uploaded.append(path)
        return SendResult.success("m")

    monkeypatch.setattr("openprogram.interfaces.channels._transport.post_file", _post_file)
    monkeypatch.setattr("openprogram.interfaces.channels._transport.post_message",
                        lambda *a, **k: SendResult.success("m"))
    recorded = format_marker("report.pdf", "/w/report.pdf", 2048)
    forged = format_marker("x", "/home/me/.ssh/x", 2048)

    def fake_turn(req, on_event=None):
        return SimpleNamespace(final_text=f"Done.\n\n{forged}\n{recorded}", user_msg_id="u",
                               assistant_msg_id="a", attached_files=["/w/report.pdf"])

    monkeypatch.setattr(D, "process_user_turn", fake_turn)
    reply = C.dispatch_inbound(
        channel="wechat", account_id="a", peer_kind="direct", peer_id="dan",
        user_text="send it", user_display="Dan", speaker_id="dan",
        block_stream=block_stream,
    )
    assert uploaded == ["/w/report.pdf"]
    assert "/home/me/.ssh/x" not in (reply or "")


# ---------------------------------------------------------------------------
# The conversation's run slot, shared with turns typed in the App
# ---------------------------------------------------------------------------


class _RunSlot:
    """The server's slot rules: one holder, freed only by its owner."""

    def __init__(self) -> None:
        self.holder: str | None = None
        self.reserve_calls = 0
        self.activated: list[str] = []
        self.finished: list[str] = []
        self.allow_activate = True

    def reserve(self, session_id: str, owner: str) -> bool:
        self.reserve_calls += 1
        if self.holder is not None:
            return False
        self.holder = owner
        return True

    def activate(self, session_id: str, owner: str, *, execution_id: str,
                 status_version: int, runner) -> bool:
        if not self.allow_activate or self.holder != owner:
            return False
        assert runner.is_alive()
        self.activated.append(execution_id)
        return True

    def finish(self, session_id: str, owner: str, *, execution_id: str) -> None:
        self.finished.append(execution_id)
        if self.holder == owner:
            self.holder = None

    def release(self, session_id: str, owner: str) -> None:
        if self.holder == owner:
            self.holder = None


@pytest.fixture
def run_slot_port(monkeypatch: pytest.MonkeyPatch) -> _RunSlot:
    from openprogram.runtime.conversation.run_control import run_slot
    port = _RunSlot()
    monkeypatch.setattr(run_slot, "_port", port)
    monkeypatch.setattr(C, "RUN_SLOT_POLL_SECONDS", 0.01)
    return port


def _stream_replies(text: str = "done"):
    fake = make_text_stream(text)
    orig_run = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, **_):
        return orig_run(req=req, history=history, on_event=on_event,
                        cancel_event=cancel_event, stream_fn=fake)
    return patch.object(D, "_run_loop_blocking", _wrapped)


def _inbound(**overrides) -> str | None:
    arguments = dict(
        channel="wechat", account_id="a", peer_kind="direct", peer_id="bob",
        user_text="hello", user_display="Bob", speaker_id="bob",
    )
    arguments.update(overrides)
    return C.dispatch_inbound(**arguments)


def test_inbound_during_an_app_turn_waits_then_says_busy(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
    run_slot_port: _RunSlot,
) -> None:
    monkeypatch.setattr(C, "RUN_SLOT_WAIT_SECONDS", 0.05)
    run_slot_port.holder = "typed-in-app"
    assert _inbound(progress_stream=True) == C.BUSY_REPLY
    assert run_slot_port.reserve_calls > 1
    assert run_slot_port.holder == "typed-in-app"
    assert tmp_db.list_sessions() == []


def test_inbound_runs_once_the_app_turn_ends(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
    run_slot_port: _RunSlot,
) -> None:
    import threading
    monkeypatch.setattr(C, "RUN_SLOT_WAIT_SECONDS", 5.0)
    run_slot_port.holder = "typed-in-app"
    timer = threading.Timer(0.05, run_slot_port.release,
                            args=("", "typed-in-app"))
    timer.start()
    with _stream_replies():
        assert _inbound() == "done"
    timer.join()


def test_a_message_typed_during_a_channel_turn_is_refused(
    tmp_db: SessionDB, stub_routing, run_slot_port: _RunSlot,
) -> None:
    typed_refused: list[bool] = []
    fake = make_text_stream("done")
    orig_run = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, **_):
        typed_refused.append(not run_slot_port.reserve(req.session_id, "typed"))
        return orig_run(req=req, history=history, on_event=on_event,
                        cancel_event=cancel_event, stream_fn=fake)

    with patch.object(D, "_run_loop_blocking", _wrapped):
        assert _inbound() == "done"
    assert typed_refused == [True]
    assert len(run_slot_port.activated) == 1
    assert run_slot_port.finished == run_slot_port.activated
    assert run_slot_port.holder is None


def test_an_ambient_event_does_not_wait_for_the_slot(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
    run_slot_port: _RunSlot,
) -> None:
    monkeypatch.setattr(C, "RUN_SLOT_WAIT_SECONDS", 60.0)
    run_slot_port.holder = "typed-in-app"
    started = time.monotonic()
    assert _inbound(peer_kind="group", ambient=True) is None
    assert time.monotonic() - started < 5
    assert run_slot_port.reserve_calls == 1


def test_losing_the_slot_at_activation_says_busy_and_frees_it(
    tmp_db: SessionDB, stub_routing, run_slot_port: _RunSlot,
) -> None:
    run_slot_port.allow_activate = False
    with _stream_replies():
        assert _inbound() == C.BUSY_REPLY
    assert run_slot_port.holder is None
    assert run_slot_port.finished == []


def test_a_turn_that_fails_before_admission_frees_the_slot(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
    run_slot_port: _RunSlot,
) -> None:
    def _boom(**_):
        raise RuntimeError("no session")
    monkeypatch.setattr(C, "_load_or_init_session", _boom)
    assert _inbound().startswith("[error] RuntimeError")
    assert run_slot_port.holder is None


def test_a_real_typed_message_is_refused_during_a_channel_turn(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server's own slot, not a stand-in."""
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.server_runtime.session_lifecycle import ServerRunSlot
    from openprogram.runtime.conversation.run_control import run_slot
    monkeypatch.setattr(run_slot, "_port", ServerRunSlot())
    during: list[tuple[bool, bool]] = []
    session_ids: list[str] = []
    fake = make_text_stream("done")
    orig_run = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, **_):
        session_ids.append(req.session_id)
        during.append((server._is_run_active(req.session_id),
                       server._try_reserve_run(req.session_id, "typed")))
        return orig_run(req=req, history=history, on_event=on_event,
                        cancel_event=cancel_event, stream_fn=fake)

    with patch.object(D, "_run_loop_blocking", _wrapped):
        assert _inbound() == "done"
    assert during == [(True, False)]
    assert not server._is_run_active(session_ids[0])
    assert server._try_reserve_run(session_ids[0], "typed-later")
    server._release_run_reservation(session_ids[0], "typed-later")


def test_a_turn_that_cannot_start_does_not_keep_the_conversation(
    tmp_db: SessionDB, stub_routing, run_slot_port: _RunSlot,
) -> None:
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.foreground import active_foreground_task

    async def _no_start(self, admission):
        raise RuntimeError("no owner")

    with patch.object(CanonicalAgentAdapter, "activate", _no_start):
        assert _inbound().startswith("[error] RuntimeError")
    session_id = tmp_db.list_sessions()[0]["id"]
    assert active_foreground_task(default_store(), session_id) is None
    assert run_slot_port.holder is None


def test_busy_reports_back_so_room_history_survives(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
    run_slot_port: _RunSlot,
) -> None:
    calls: list[str] = []
    run_slot_port.holder = "typed-in-app"
    assert _inbound(peer_kind="group", ambient=True,
                    on_busy=lambda: calls.append("busy")) is None
    assert calls == ["busy"]


def test_a_turn_that_runs_does_not_report_busy(
    tmp_db: SessionDB, stub_routing, run_slot_port: _RunSlot,
) -> None:
    calls: list[str] = []
    with _stream_replies():
        assert _inbound(on_busy=lambda: calls.append("busy")) == "done"
    assert calls == []


class _LockedSlot(_RunSlot):
    """A slot behind a conversation lock that is not had in time."""

    def __init__(self) -> None:
        super().__init__()
        self.lock_waits: list[float | None] = []

    def reserve(self, session_id: str, owner: str, *, lock_wait: float | None = None) -> bool:
        from openprogram.state.store.session.session_lock import SessionLockTimeout
        self.reserve_calls += 1
        self.lock_waits.append(lock_wait)
        raise SessionLockTimeout(session_id, lock_wait or 0.0, "another process")


def test_a_message_whose_conversation_stays_locked_is_not_delivered_and_says_so(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing was taken or written: the sender is told to send it again,
    not that a turn is running, and the wait for the lock fits the slot's."""
    from openprogram.runtime.conversation.run_control import run_slot
    port = _LockedSlot()
    monkeypatch.setattr(run_slot, "_port", port)
    monkeypatch.setattr(C, "RUN_SLOT_WAIT_SECONDS", 0.5)
    busy: list[str] = []
    assert _inbound(on_busy=lambda: busy.append("busy")) == C.LOCKED_REPLY
    assert busy == ["busy"]
    assert port.reserve_calls == 1 and 0 < port.lock_waits[0] <= 0.5
    assert port.holder is None


def test_an_ambient_event_does_not_wait_for_a_locked_conversation(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.runtime.conversation.run_control import run_slot
    port = _LockedSlot()
    monkeypatch.setattr(run_slot, "_port", port)
    busy: list[str] = []
    started = time.monotonic()
    assert _inbound(peer_kind="group", ambient=True,
                    on_busy=lambda: busy.append("busy")) is None
    assert time.monotonic() - started < 1
    assert port.lock_waits == [0.0]
    assert busy == ["busy"]


def test_an_ambient_event_skips_a_conversation_another_channel_turn_holds(
    tmp_db: SessionDB, stub_routing, monkeypatch: pytest.MonkeyPatch,
    run_slot_port: _RunSlot,
) -> None:
    """The room event does not queue behind the channel turn running in its
    conversation; a direct message would."""
    import threading
    held = threading.Lock()
    held.acquire()
    monkeypatch.setattr(C, "_session_lock_for", lambda _key: held)
    busy: list[str] = []
    started = time.monotonic()
    assert _inbound(peer_kind="group", ambient=True,
                    on_busy=lambda: busy.append("busy")) is None
    assert time.monotonic() - started < 1
    assert busy == ["busy"]
    assert run_slot_port.reserve_calls == 0
    held.release()

"""The room layer in ``Channel._dispatch_and_reply``: which group messages
start a turn, which wait as pending history, the room commands, loop and
flood protection, the typing indicator and the ack reaction.
"""
from __future__ import annotations

import json
import threading
from dataclasses import replace

import pytest

from openprogram.interfaces.channels import _room
from openprogram.interfaces.channels._message import ChannelMessage
from openprogram.interfaces.channels._transport import SendResult
from openprogram.interfaces.channels.base import Channel, _TypingIndicator


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    _room.reset_state()
    yield
    _room.reset_state()


@pytest.fixture(autouse=True)
def _paired(_tmp_state):
    from openprogram.interfaces.channels import _access
    for platform in ("faketg", "telegram"):
        for sender in ("701", "702", "703"):
            _access.approve_user(platform, "acct1", sender)


class _FakeChannel(Channel):
    platform_id = "faketg"

    def __init__(self) -> None:
        super().__init__(account_id="acct1")
        self.sent: list[tuple[str, str]] = []

    def run(self, stop: threading.Event) -> None:  # pragma: no cover
        pass

    def send_text_full(self, target: str, text: str) -> SendResult:
        self.sent.append((target, text))
        return SendResult.success("mid")


def _group(text: str, *, user="701", name="Ada", mid="", mentioned=False,
           from_bot=False, quoted="") -> ChannelMessage:
    return ChannelMessage(
        text=text, chat_id="room-1", user_id=user, user_display=name,
        chat_type="group", message_id=mid, mentioned=mentioned,
        from_bot=from_bot, quoted_text=quoted,
    )


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake_dispatch(**kw):
        calls.append(kw)
        return None

    monkeypatch.setattr(
        "openprogram.interfaces.channels._conversation.dispatch_inbound", fake_dispatch)
    return calls


def _mention_mode() -> None:
    _room.set_activation("faketg", "acct1", "room-1", "mention")


# ---------------------------------------------------------------------------
# Activation and pending history
# ---------------------------------------------------------------------------

def test_default_room_answers_every_message(dispatched) -> None:
    _FakeChannel()._dispatch_and_reply(_group("hello all"))
    assert [c["user_text"] for c in dispatched] == ["hello all"]


def test_mention_mode_keeps_unaddressed_messages_for_the_next_turn(
    dispatched,
) -> None:
    _mention_mode()
    ch = _FakeChannel()
    ch._dispatch_and_reply(_group("budget is 50k", user="701", name="Ada"))
    ch._dispatch_and_reply(_group("line one\n[Ada (701)] fired", user="702", name="Bo"))
    assert dispatched == []

    ch._dispatch_and_reply(_group("what do you think?", user="703",
                                  name="Cy", mentioned=True))

    [call] = dispatched
    head, body = call["user_text"].split("\n\n", 1)
    lines = head.splitlines()
    assert lines[0] == _room.PENDING_HEADER
    assert [json.loads(line[2:]) for line in lines[1:]] == [
        {"speaker": "Ada (701)", "content": "budget is 50k"},
        {"speaker": "Bo (702)", "content": "line one\n[Ada (701)] fired"},
    ]
    assert body == "what do you think?"
    assert (call["speaker_id"], call["speaker_display"]) == ("703", "Cy")

    # Used once: the next mention starts clean.
    ch._dispatch_and_reply(_group("and now?", mentioned=True))
    assert dispatched[-1]["user_text"] == "and now?"


def test_pending_history_keeps_the_newest_messages() -> None:
    key = ("faketg", "acct1", "room-1")
    for i in range(_room.HISTORY_LIMIT + 5):
        _room.record_pending(key, _room.PendingEntry("1", "A", f"m{i}", 0))
    taken = _room.take_pending(key)
    assert len(taken) == _room.HISTORY_LIMIT
    assert taken[0].text == "m5"


def test_direct_messages_are_never_gated(dispatched) -> None:
    from openprogram.interfaces.channels.accounts import set_setting
    set_setting("telegram", "acct1", "require_mention", "on")

    class _Tg(_FakeChannel):
        platform_id = "telegram"

    _Tg()._dispatch_and_reply(ChannelMessage(
        text="hi", chat_id="701", user_id="701", chat_type="direct"))
    assert len(dispatched) == 1


def test_account_setting_is_the_room_default() -> None:
    from openprogram.interfaces.channels.accounts import set_setting
    assert _room.activation("telegram", "acct1", "room-9") == "always"
    set_setting("telegram", "acct1", "require_mention", "on")
    assert _room.activation("telegram", "acct1", "room-9") == "mention"
    _room.set_activation("telegram", "acct1", "room-9", "always")
    assert _room.activation("telegram", "acct1", "room-9") == "always"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def test_activation_command_switches_the_room_without_a_mention(
    dispatched,
) -> None:
    _mention_mode()
    ch = _FakeChannel()
    ch._dispatch_and_reply(_group("/activation"))
    ch._dispatch_and_reply(_group("/activation always"))
    ch._dispatch_and_reply(_group("/activation sometimes"))
    replies = [text for _, text in ch.sent]
    assert "mention" in replies[0]
    assert replies[1].startswith("✓") and "always" in replies[1]
    assert replies[2].startswith("用法")
    assert _room.activation("faketg", "acct1", "room-1") == "always"
    assert dispatched == []


def test_activation_in_a_direct_chat_explains_itself(dispatched) -> None:
    ch = _FakeChannel()
    ch._dispatch_and_reply(ChannelMessage(
        text="/activation mention", chat_id="701", user_id="701",
        chat_type="direct"))
    assert ch.sent and "私聊" in ch.sent[0][1]
    assert dispatched == []


def test_new_starts_a_fresh_conversation_and_drops_pending(
    dispatched, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict] = []
    monkeypatch.setattr(
        "openprogram.interfaces.channels._conversation.start_new_conversation",
        lambda **kw: seen.append(kw) or "fresh")
    _mention_mode()
    ch = _FakeChannel()
    ch._dispatch_and_reply(_group("old chatter"))
    ch._dispatch_and_reply(_group("/new"))
    ch._dispatch_and_reply(_group("hi", mentioned=True))

    assert seen == [{"channel": "faketg", "account_id": "acct1",
                     "peer_kind": "group", "peer_id": "room-1"}]
    assert ch.sent == [("room-1", "fresh")]
    assert dispatched[-1]["user_text"] == "hi"


def test_answer_passes_the_gate_untouched(dispatched) -> None:
    _mention_mode()
    ch = _FakeChannel()
    ch._dispatch_and_reply(_group("chatter"))
    ch._dispatch_and_reply(_group("/answer q1 2", quoted="the question"))
    assert [c["user_text"] for c in dispatched] == ["/answer q1 2"]
    # The chatter is still waiting for a real turn.
    assert _room.take_pending(("faketg", "acct1", "room-1"))


# ---------------------------------------------------------------------------
# Loop and flood protection
# ---------------------------------------------------------------------------

def test_bot_messages_and_redeliveries_are_dropped(dispatched) -> None:
    ch = _FakeChannel()
    ch._dispatch_and_reply(_group("beep", from_bot=True))
    ch._dispatch_and_reply(_group("once", mid="m1"))
    ch._dispatch_and_reply(_group("once", mid="m1"))
    assert [c["user_text"] for c in dispatched] == ["once"]


def test_a_busy_room_stops_starting_turns(dispatched) -> None:
    ch = _FakeChannel()
    for i in range(_room.TURN_LIMIT + 3):
        ch._dispatch_and_reply(_group(f"msg {i}"))
    assert len(dispatched) == _room.TURN_LIMIT
    pending = _room.take_pending(("faketg", "acct1", "room-1"))
    assert [p.text for p in pending] == [
        f"msg {i}" for i in range(_room.TURN_LIMIT, _room.TURN_LIMIT + 3)]


def test_turn_window_slides() -> None:
    key = ("faketg", "acct1", "room-2")
    for _ in range(_room.TURN_LIMIT):
        assert _room.allow_turn(key, now=0.0)
    assert not _room.allow_turn(key, now=1.0)
    assert _room.allow_turn(key, now=_room.TURN_WINDOW_SECONDS + 1)


# ---------------------------------------------------------------------------
# Typing indicator and ack reaction
# ---------------------------------------------------------------------------

def test_typing_runs_for_the_turn_and_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    started = threading.Event()

    def send(platform, account_id, target):
        events.append(f"typing {target}")
        started.set()
        return SendResult.success("")

    monkeypatch.setattr("openprogram.interfaces.channels._transport.send_typing", send)
    monkeypatch.setattr(
        "openprogram.interfaces.channels._transport.stop_typing",
        lambda p, a, t: events.append(f"stop {t}") or SendResult.success(""))
    typing = _TypingIndicator("wechat", "acct1", "user-1")
    typing.start()
    assert started.wait(30)
    typing.stop()
    assert events == ["typing user-1", "stop user-1"]


def test_typing_gives_up_quietly_where_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "openprogram.interfaces.channels._transport.send_typing",
        lambda *a: calls.append("t") or SendResult.fail("not_supported", "x"))
    monkeypatch.setattr(
        "openprogram.interfaces.channels._transport.stop_typing",
        lambda *a: calls.append("stop") or SendResult.success(""))
    typing = _TypingIndicator("wechat", "acct1", "user-1")
    typing.start()
    typing.stop()
    assert calls == ["t"]
    # Slack has no typing API at all: nothing is sent.
    _TypingIndicator("slack", "acct1", "C1").start()


def test_typing_follows_the_thread_the_message_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple] = []
    started = threading.Event()

    def send(platform, account_id, target, **kw):
        seen.append((target, kw))
        started.set()
        return SendResult.fail("not_supported", "stop after one")

    def dispatch(**kw):
        assert started.wait(30)

    monkeypatch.setattr("openprogram.interfaces.channels._transport.send_typing", send)
    monkeypatch.setattr(
        "openprogram.interfaces.channels._conversation.dispatch_inbound", dispatch)

    class _Tg(_FakeChannel):
        platform_id = "telegram"

    _Tg()._dispatch_and_reply(replace(_group("hello", mid="30"), thread_id="42"))
    assert seen == [("room-1", {"thread_id": "42"})]


def test_ack_reaction_follows_the_account_setting(
    dispatched, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.interfaces.channels.accounts import set_setting
    reactions: list[tuple] = []
    monkeypatch.setattr(
        "openprogram.interfaces.channels._transport.add_reaction",
        lambda *a: reactions.append(a) or SendResult.success(""))
    monkeypatch.setattr(
        "openprogram.interfaces.channels._transport.send_typing",
        lambda *a: SendResult.fail("not_supported", "x"))
    monkeypatch.setattr(
        "openprogram.interfaces.channels._conversation.start_new_conversation",
        lambda **kw: "fresh")

    class _Tg(_FakeChannel):
        platform_id = "telegram"

    _Tg()._dispatch_and_reply(_group("one", mid="10"))
    set_setting("telegram", "acct1", "ack_reaction", "on")
    _Tg()._dispatch_and_reply(_group("two", mid="11"))
    _Tg()._dispatch_and_reply(_group("/new", mid="12"))
    assert reactions == [("telegram", "acct1", "room-1", "11")]


# ---------------------------------------------------------------------------
# Ambient rooms and room memory
# ---------------------------------------------------------------------------

def test_ambient_room_turns_unaddressed_messages_into_quiet_room_events(
    dispatched, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.interfaces.channels.accounts import set_setting
    touched: list[str] = []
    monkeypatch.setattr("openprogram.interfaces.channels._transport.add_reaction",
                        lambda *a: touched.append("ack") or SendResult.success(""))
    monkeypatch.setattr("openprogram.interfaces.channels._transport.send_typing",
                        lambda *a: touched.append("typing") or SendResult.success(""))

    class _Tg(_FakeChannel):
        platform_id = "telegram"

    set_setting("telegram", "acct1", "ack_reaction", "on")
    _room.set_activation("telegram", "acct1", "room-1", "ambient")
    ch = _Tg()
    ch._dispatch_and_reply(_group("lunch at noon?", mid="20"))
    [event] = dispatched
    assert event["ambient"] is True
    assert event["user_text"] == f"{_room.ROOM_EVENT_HEADER}\nlunch at noon?"
    assert touched == []

    # Addressing the bot in an ambient room is an ordinary turn.
    ch._dispatch_and_reply(_group("bot, book it", mid="21", mentioned=True))
    assert dispatched[-1]["ambient"] is False
    assert dispatched[-1]["user_text"] == "bot, book it"
    assert touched[0] == "ack"


def test_activation_accepts_ambient(dispatched) -> None:
    ch = _FakeChannel()
    ch._dispatch_and_reply(_group("/activation ambient"))
    assert ch.sent[-1][1].startswith("✓") and "ambient" in ch.sent[-1][1]
    assert _room.activation("faketg", "acct1", "room-1") == "ambient"


def test_memory_command_narrows_the_room(dispatched) -> None:
    ch = _FakeChannel()
    ch._dispatch_and_reply(_group("hello"))
    assert dispatched[-1]["memory_mode"] == "on"

    ch._dispatch_and_reply(_group("/memory"))
    assert "on" in ch.sent[-1][1]
    ch._dispatch_and_reply(_group("/memory read"))
    assert ch.sent[-1][1].startswith("✓")
    ch._dispatch_and_reply(_group("/memory everything"))
    assert ch.sent[-1][1].startswith("用法")
    ch._dispatch_and_reply(_group("hello again"))
    assert dispatched[-1]["memory_mode"] == "read"
    assert _room.memory_override("read") == {"mode": "read_only"}
    assert _room.memory_override("off") == {"mode": "off"}
    assert _room.memory_override("on") is None

    ch._dispatch_and_reply(ChannelMessage(
        text="/memory off", chat_id="701", user_id="701", chat_type="direct"))
    assert "私聊" in ch.sent[-1][1]
    assert len(dispatched) == 2


def test_restored_pending_goes_ahead_of_newer_messages() -> None:
    key = ("faketg", "acct1", "room-restore")
    _room.clear_pending(key)
    first = _room.PendingEntry(speaker_id="a", speaker_display="A", text="first", ts=1.0)
    newer = _room.PendingEntry(speaker_id="b", speaker_display="B", text="newer", ts=2.0)
    _room.record_pending(key, newer)
    _room.restore_pending(key, [first])
    assert [entry.text for entry in _room.take_pending(key)] == ["first", "newer"]


def test_busy_ambient_event_stays_as_room_history(
    monkeypatch: pytest.MonkeyPatch, dispatched,
) -> None:
    def busy(**kwargs):
        kwargs["on_busy"]()
        return None

    monkeypatch.setattr(
        "openprogram.interfaces.channels._conversation.dispatch_inbound", busy)
    key = ("faketg", "acct1", "room-1")
    _room.clear_pending(key)
    _room.set_activation("faketg", "acct1", "room-1", "ambient")
    older = _room.PendingEntry(speaker_id="a", speaker_display="A", text="older", ts=1.0)
    _room.record_pending(key, older)
    ch = _FakeChannel()
    ch._dispatch_and_reply(_group("unaddressed"))
    assert [entry.text for entry in _room.take_pending(key)] == ["older", "unaddressed"]

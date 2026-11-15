"""``/stop`` and ``/help`` routing and access: in a group they need full
access. ``tests/component/channels/test_channel_stop.py`` runs ``/stop``
against a live turn.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from openprogram.interfaces.channels import _active_turns, _conversation, _room
from openprogram.interfaces.channels._message import ChannelMessage
from openprogram.interfaces.channels._transport import SendResult
from openprogram.interfaces.channels.base import Channel
from openprogram.runtime.execution.model import ExecutionStatus

SESSION = "faketg_session"


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    monkeypatch.setattr(
        "openprogram.interfaces.channels._conversation._route",
        lambda channel, account_id, peer, *, rotate=True: ("main", SESSION, "base"),
    )
    _room.reset_state()
    from openprogram.interfaces.channels import _access
    _access.approve_user("faketg", "acct1", "701")
    yield
    _room.reset_state()


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


def _direct(text: str) -> ChannelMessage:
    return ChannelMessage(
        text=text, chat_id="701", user_id="701", user_display="Ada",
        chat_type="direct",
    )


def _group(text: str, user: str = "701") -> ChannelMessage:
    return ChannelMessage(
        text=text, chat_id="room-1", user_id=user, user_display="Ada",
        chat_type="group",
    )


def _set_group_level(monkeypatch: pytest.MonkeyPatch, level: str) -> None:
    monkeypatch.setattr(
        "openprogram.interfaces.channels._access.group_sender_access",
        lambda *args, **kwargs: level,
    )


def _no_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(**_kwargs):
        raise AssertionError("a command must not start a turn")

    monkeypatch.setattr(_conversation, "dispatch_inbound", refuse)


def test_stop_finds_a_turn_on_an_older_session_key(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "openprogram.runtime.execution.run_control.cancel_session_executions",
        lambda session_id: calls.append(session_id) or [],
    )
    _active_turns.begin("yesterday", _active_turns.ActiveTurn(
        channel="faketg", account_id="acct1", peer_id="701",
        peer_kind="direct", speaker_id="701",
    ))
    try:
        reply = _conversation.stop_current_work(
            channel="faketg", account_id="acct1", peer_kind="direct", peer_id="701",
        )
    finally:
        _active_turns.end("yesterday")

    assert sorted(calls) == sorted([SESSION, "yesterday"])
    assert reply == "现在没有正在进行的任务。"


def test_stop_never_starts_a_new_generation(monkeypatch) -> None:
    routed: list[bool] = []
    monkeypatch.setattr(
        "openprogram.interfaces.channels._conversation._route",
        lambda channel, account_id, peer, *, rotate=True: routed.append(rotate) or ("main", SESSION, "base"),
    )
    monkeypatch.setattr(
        "openprogram.runtime.execution.run_control.cancel_session_executions",
        lambda session_id: [],
    )
    _conversation.stop_current_work(
        channel="faketg", account_id="acct1", peer_kind="direct", peer_id="701",
    )
    assert routed == [False]


@pytest.mark.parametrize(("status", "text"), [
    (ExecutionStatus.CANCELLING, "已停止。"),
    (ExecutionStatus.CANCELLED, "已停止。"),
    (ExecutionStatus.COMPLETED, "(empty reply)"),
])
def test_a_stopped_turn_with_no_text_says_it_stopped(status, text) -> None:
    store = SimpleNamespace(get_execution=lambda _id: SimpleNamespace(status=status))
    assert _conversation._empty_reply_text(store, "e1") == text


@pytest.mark.parametrize("command", ["/stop", "/help"])
def test_group_member_without_full_access_is_refused(command, monkeypatch) -> None:
    _no_dispatch(monkeypatch)
    _set_group_level(monkeypatch, "chat")
    monkeypatch.setattr(
        "openprogram.runtime.execution.run_control.cancel_session_executions",
        lambda session_id: pytest.fail("must not cancel"),
    )
    channel = _FakeChannel()
    channel._dispatch_and_reply(_group(command, user="702"))
    assert len(channel.sent) == 1
    assert "只能聊天" in channel.sent[0][1]


def test_group_member_with_full_access_stops_without_a_mention(monkeypatch) -> None:
    _no_dispatch(monkeypatch)
    _set_group_level(monkeypatch, "full")
    calls: list[str] = []
    monkeypatch.setattr(
        "openprogram.runtime.execution.run_control.cancel_session_executions",
        lambda session_id: calls.append(session_id) or [],
    )
    monkeypatch.setattr(_room, "activation", lambda *args: "mention")
    channel = _FakeChannel()
    channel._dispatch_and_reply(_group("/stop"))
    assert calls == [SESSION]
    assert channel.sent == [("room-1", "现在没有正在进行的任务。")]


def test_help_lists_every_command(monkeypatch) -> None:
    _no_dispatch(monkeypatch)
    channel = _FakeChannel()
    channel._dispatch_and_reply(_direct("/help"))
    assert len(channel.sent) == 1
    for command in _room.COMMANDS:
        assert command in channel.sent[0][1]

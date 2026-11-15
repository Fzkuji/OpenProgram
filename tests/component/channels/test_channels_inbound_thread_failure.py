"""A channel message whose worker thread cannot start must not hold its chat's queue."""
from __future__ import annotations

import threading

import pytest

from openprogram.interfaces.channels._message import ChannelMessage
from openprogram.interfaces.channels.base import _INBOUND_ORDER, Channel


class _FakeChannel(Channel):
    platform_id = "faketg"

    def __init__(self) -> None:
        super().__init__(account_id="acct1")

    def run(self, stop: threading.Event) -> None:  # pragma: no cover
        pass


class _ThreadThatCannotStart:
    def __init__(self, **keyword_arguments) -> None:
        pass

    def start(self) -> None:
        raise RuntimeError("can't start new thread")


def test_a_thread_that_cannot_start_gives_its_place_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = ChannelMessage(
        text="hello", chat_id="42", user_id="7", user_display="Bob", chat_type="direct",
    )
    channel = _FakeChannel()

    with monkeypatch.context() as patch, pytest.raises(RuntimeError):
        patch.setattr(
            "openprogram.interfaces.channels.base.threading.Thread",
            _ThreadThatCannotStart,
        )
        channel.handle_inbound(message)

    assert ("faketg", "acct1", "42") not in _INBOUND_ORDER._queues

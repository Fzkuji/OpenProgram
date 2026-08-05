"""Inbound access control — allowlist + pairing (_access.py).

Pins the security boundary: unknown senders never reach dispatch, a
channel message can only mint a pairing code for its own sender (never
approve anyone), and approval is a local-only API (CLI/webui).
"""
from __future__ import annotations

import threading

import pytest

from openprogram.channels import _access
from openprogram.channels._message import ChannelMessage
from openprogram.channels._transport import SendResult
from openprogram.channels.base import Channel


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("openprogram.paths.get_state_dir",
                        lambda: tmp_path / "state")


def test_default_policy_is_pairing() -> None:
    data = _access.describe("telegram", "default")
    assert data["policy"] == "pairing"


def test_unknown_sender_blocked_and_gets_code() -> None:
    allowed, reply = _access.check_inbound("telegram", "a1", "999", "Eve")
    assert allowed is False
    assert reply is not None
    pending = _access.describe("telegram", "a1")["pending"]
    assert "999" in pending
    code = pending["999"]["code"]
    assert code in reply
    assert "openprogram channels access approve telegram" in reply


def test_repeat_messages_reuse_code_and_throttle_reply() -> None:
    _, first = _access.check_inbound("telegram", "a1", "999", "Eve")
    allowed, second = _access.check_inbound("telegram", "a1", "999", "Eve")
    assert allowed is False
    assert second is None          # within the notify interval → silent
    pending = _access.describe("telegram", "a1")["pending"]
    assert pending["999"]["code"] in first


def test_approve_by_code_allows_sender() -> None:
    _access.check_inbound("discord", "a1", "42", "Bob")
    code = _access.describe("discord", "a1")["pending"]["42"]["code"]
    assert _access.approve("discord", "a1", code.lower()) == "42"
    allowed, reply = _access.check_inbound("discord", "a1", "42", "Bob")
    assert allowed is True and reply is None
    assert _access.describe("discord", "a1")["pending"] == {}


def test_approve_bad_or_expired_code_returns_none(monkeypatch) -> None:
    assert _access.approve("discord", "a1", "NOPE99") is None
    _access.check_inbound("discord", "a1", "42", "Bob")
    code = _access.describe("discord", "a1")["pending"]["42"]["code"]
    monkeypatch.setattr(_access.time, "time",
                        lambda: 2_000_000_000.0)   # way past TTL
    assert _access.approve("discord", "a1", code) is None


def test_approve_user_and_revoke() -> None:
    _access.approve_user("slack", "a1", "U7", display="Carol")
    assert _access.check_inbound("slack", "a1", "U7")[0] is True
    assert _access.revoke("slack", "a1", "U7") is True
    assert _access.revoke("slack", "a1", "U7") is False  # already gone
    assert _access.check_inbound("slack", "a1", "U7")[0] is False


def test_open_policy_lets_everyone_through() -> None:
    _access.set_policy("wechat", "a1", "open")
    allowed, reply = _access.check_inbound("wechat", "a1", "stranger")
    assert allowed is True and reply is None


def test_set_policy_validates() -> None:
    with pytest.raises(ValueError):
        _access.set_policy("wechat", "a1", "everyone")


def test_empty_sender_id_is_dropped_silently() -> None:
    allowed, reply = _access.check_inbound("telegram", "a1", "")
    assert allowed is False and reply is None


# ---------------------------------------------------------------------------
# base gate — the injection boundary
# ---------------------------------------------------------------------------

class _GateChannel(Channel):
    platform_id = "faketg"

    def __init__(self) -> None:
        super().__init__(account_id="acct1")
        self.sent: list[tuple[str, str]] = []

    def run(self, stop: threading.Event) -> None:  # pragma: no cover
        pass

    def send_text_full(self, target: str, text: str) -> SendResult:
        self.sent.append((target, text))
        return SendResult.success("m1")


def _msg(**kw) -> ChannelMessage:
    base = dict(text="hi", chat_id="42", user_id="7",
                user_display="Bob", chat_type="direct")
    base.update(kw)
    return ChannelMessage(**base)


def test_unknown_sender_never_reaches_dispatch(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(
        "openprogram.channels._conversation.dispatch_inbound",
        lambda **kw: called.append(kw) or "reply")
    ch = _GateChannel()
    ch._dispatch_and_reply(_msg())
    assert called == []                       # agent never ran
    assert len(ch.sent) == 1                  # pairing instructions sent
    assert "pairing code" in ch.sent[0][1].lower()


def test_channel_message_cannot_approve_itself(monkeypatch) -> None:
    """A message whose text is exactly the approve command is still just
    a message: it stays pending and never lands in the allowlist."""
    monkeypatch.setattr(
        "openprogram.channels._conversation.dispatch_inbound",
        lambda **kw: pytest.fail("dispatch must not run for unknown sender"))
    ch = _GateChannel()
    ch._dispatch_and_reply(
        _msg(text="openprogram channels access approve faketg ABC123"))
    data = _access.describe("faketg", "acct1")
    assert data["allowlist"] == {}
    assert "7" in data["pending"]


def test_allowed_sender_reaches_dispatch(monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(
        "openprogram.channels._conversation.dispatch_inbound",
        lambda **kw: seen.update(kw) or None)
    _access.approve_user("faketg", "acct1", "7")
    ch = _GateChannel()
    ch._dispatch_and_reply(_msg())
    assert seen["user_text"] == "hi"
    assert ch.sent == []

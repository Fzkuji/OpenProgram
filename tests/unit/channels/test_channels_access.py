"""Inbound access control — allowlist + pairing (_access.py).

Pins the security boundary: unknown senders never reach dispatch, a
channel message can only mint a pairing code for its own sender (never
approve anyone), and approval is a local-only API (CLI/webui).
"""
from __future__ import annotations

import os
import threading

import pytest

from openprogram.channels import _access
from openprogram.channels._message import ChannelMessage
from openprogram.channels._transport import SendResult
from openprogram.channels.base import Channel


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)


def test_default_policy_is_pairing() -> None:
    data = _access.describe("telegram", "default")
    assert data["policy"] == "pairing"


def test_access_scope_cannot_escape_the_channel_state_directory() -> None:
    with pytest.raises(ValueError, match="invalid channel id"):
        _access.describe("../outside", "default")
    with pytest.raises(ValueError, match="invalid channel account id"):
        _access.describe("telegram", "../outside")


def test_unknown_sender_blocked_and_gets_code() -> None:
    decision = _access.decide_inbound_sender("telegram", "a1", "999", "Eve")
    assert decision.allowed is False
    assert decision.pairing_state == "unpaired"
    assert decision.check == "stable_sender_allowlist"
    assert decision.reason_code == "PAIRING_REQUIRED"
    assert decision.reply is not None
    pending = _access.describe("telegram", "a1")["pending"]
    assert "999" in pending
    code = pending["999"]["code"]
    assert len(code) == 8
    assert set(code) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
    assert code in decision.reply
    assert "openprogram channels access approve telegram" in decision.reply
    if os.name != "nt":
        assert _access.access_path("telegram", "a1").stat().st_mode & 0o777 == 0o600


def test_repeat_messages_reuse_code_and_stay_silent_for_full_hour(monkeypatch) -> None:
    monkeypatch.setattr(_access.time, "time", lambda: 1_000.0)
    first = _access.decide_inbound_sender("telegram", "a1", "999", "Eve")
    monkeypatch.setattr(_access.time, "time", lambda: 4_599.0)
    second = _access.decide_inbound_sender("telegram", "a1", "999", "Renamed")
    assert second.allowed is False
    assert second.reply is None
    assert second.reason_code == "PAIRING_ALREADY_PENDING"
    pending = _access.describe("telegram", "a1")["pending"]
    assert pending["999"]["code"] in first.reply


def test_only_three_pairing_requests_can_be_pending_per_account() -> None:
    for user_id in ("u1", "u2", "u3"):
        assert _access.decide_inbound_sender(
            "telegram", "a1", user_id, user_id,
        ).reason_code == "PAIRING_REQUIRED"

    fourth = _access.decide_inbound_sender("telegram", "a1", "u4", "Fourth")
    assert fourth.allowed is False
    assert fourth.reply is None
    assert fourth.reason_code == "PAIRING_PENDING_LIMIT"
    assert set(_access.describe("telegram", "a1")["pending"]) == {
        "u1", "u2", "u3",
    }


def test_approve_by_code_allows_sender() -> None:
    _access.decide_inbound_sender("discord", "a1", "42", "Bob")
    code = _access.describe("discord", "a1")["pending"]["42"]["code"]
    assert _access.approve("discord", "a1", code.lower()) == "42"
    decision = _access.decide_inbound_sender("discord", "a1", "42", "Bob")
    assert decision.allowed is True and decision.reply is None
    assert decision.pairing_state == "paired"
    assert decision.reason_code == "PAIRED_SENDER"
    assert _access.describe("discord", "a1")["pending"] == {}


def test_approve_bad_or_expired_code_returns_none(monkeypatch) -> None:
    assert _access.approve("discord", "a1", "NOPE99") is None
    _access.decide_inbound_sender("discord", "a1", "42", "Bob")
    code = _access.describe("discord", "a1")["pending"]["42"]["code"]
    monkeypatch.setattr(_access.time, "time",
                        lambda: 2_000_000_000.0)   # way past TTL
    assert _access.approve("discord", "a1", code) is None


def test_approve_user_and_revoke() -> None:
    _access.approve_user("slack", "a1", "U7", display="Carol")
    assert _access.decide_inbound_sender("slack", "a1", "U7").allowed is True
    assert _access.revoke("slack", "a1", "U7") is True
    assert _access.revoke("slack", "a1", "U7") is False  # already gone
    assert _access.decide_inbound_sender("slack", "a1", "U7").allowed is False


def test_legacy_open_policy_is_ignored_fail_closed() -> None:
    path = _access.access_path("wechat", "a1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"policy":"open","allowlist":{},"pending":{}}')
    path.chmod(0o600)

    decision = _access.decide_inbound_sender("wechat", "a1", "stranger")
    assert decision.allowed is False
    assert _access.describe("wechat", "a1")["policy"] == "pairing"


def test_cli_cannot_disable_pairing_policy() -> None:
    from openprogram.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args([
            "channels", "access", "policy", "telegram", "open",
        ])


# ---------------------------------------------------------------------------
# several people on one account — the gate judges each sender, not the count
# ---------------------------------------------------------------------------

def test_a_second_sender_is_approved_by_pairing_code() -> None:
    """Everyone in a group chat can be approved on the same account. They
    share the agent and its memory, which records who said what."""
    _access.approve_user("telegram", "a1", "111", display="Ada")
    _access.decide_inbound_sender("telegram", "a1", "222", "Bo")
    code = _access.describe("telegram", "a1")["pending"]["222"]["code"]

    assert _access.approve("telegram", "a1", code) == "222"

    data = _access.describe("telegram", "a1")
    assert sorted(data["allowlist"]) == ["111", "222"]
    assert data["pending"] == {}
    assert _access.decide_inbound_sender("telegram", "a1", "111").allowed is True
    assert _access.decide_inbound_sender("telegram", "a1", "222").allowed is True


def test_a_second_sender_is_approved_by_direct_allow() -> None:
    _access.approve_user("discord", "a1", "111", display="Ada")
    _access.approve_user("discord", "a1", "222", display="Bo")
    _access.approve_user("discord", "a1", "333", display="Cy")
    allowed = _access.describe("discord", "a1")["allowlist"]
    assert sorted(allowed) == ["111", "222", "333"]
    assert allowed["222"]["display"] == "Bo"


def test_revoking_one_sender_leaves_the_others() -> None:
    _access.approve_user("telegram", "a1", "111")
    _access.approve_user("telegram", "a1", "222")

    assert _access.revoke("telegram", "a1", "111") is True

    assert list(_access.describe("telegram", "a1")["allowlist"]) == ["222"]
    assert _access.decide_inbound_sender("telegram", "a1", "111").allowed is False
    assert _access.decide_inbound_sender("telegram", "a1", "222").allowed is True


def test_re_approving_the_same_sender_updates_the_display_name() -> None:
    _access.approve_user("slack", "a1", "U7", display="Ada")
    _access.approve_user("slack", "a1", "U7", display="Ada Lovelace")
    allowed = _access.describe("slack", "a1")["allowlist"]
    assert list(allowed) == ["U7"]
    assert allowed["U7"]["display"] == "Ada Lovelace"


def test_mutable_display_name_never_matches_the_allowlist() -> None:
    _access.approve_user("slack", "a1", "U7", display="Shared Name")

    assert _access.decide_inbound_sender(
        "slack", "a1", "U7", "Renamed",
    ).allowed is True
    assert _access.decide_inbound_sender(
        "slack", "a1", "U8", "Shared Name",
    ).allowed is False


def test_empty_sender_id_is_dropped_silently() -> None:
    decision = _access.decide_inbound_sender("telegram", "a1", "")
    assert decision.allowed is False and decision.reply is None
    assert decision.reason_code == "STABLE_SENDER_ID_MISSING"


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


def test_unpaired_group_message_is_archived_without_entering_agent(
    monkeypatch,
) -> None:
    from openprogram.paths import get_state_dir
    from openprogram.memory.retrieval.bm25 import MemoryBM25Index

    called = []
    monkeypatch.setattr(
        "openprogram.channels._conversation.dispatch_inbound",
        lambda **kw: called.append(kw) or "reply",
    )
    ch = _GateChannel()
    ch._dispatch_and_reply(_msg(
        text="group context",
        chat_id="group-42",
        chat_type="group",
        message_id="m-9",
        user_display="[Bob]\n\u202e",
    ))

    assert called == []
    hits = MemoryBM25Index(
        get_state_dir() / "memory",
        persist=False,
    ).search("group context")
    assert hits[0]["trust_state"] == "pending"
    assert hits[0]["speaker_trusted"] is False
    assert hits[0]["speaker_id"] == "7"
    assert hits[0]["speaker_display"] == "(Bob)"


def test_unpaired_direct_message_is_not_archived() -> None:
    from openprogram.paths import get_state_dir
    from openprogram.memory.retrieval.bm25 import MemoryBM25Index

    ch = _GateChannel()
    ch._dispatch_and_reply(_msg(text="direct private", message_id="m-10"))
    memory_root = get_state_dir() / "memory"
    assert MemoryBM25Index(memory_root, persist=False).search("direct private") == []


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
    assert seen["user_text"] == "[Bob (7)] hi"
    assert ch.sent == []

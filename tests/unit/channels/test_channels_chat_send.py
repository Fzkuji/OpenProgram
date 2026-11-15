"""chat_send — the agent posting into the chat a channel turn came from —
and the rule that only the speaker whose turn raised a question may
answer it in a group.
"""
from __future__ import annotations

import pytest

from openprogram.runtime.permissions.authority import paired_channel_authority
from openprogram.runtime.permissions.tool_authority import decide_tool_authority
from openprogram.interfaces.channels import _active_turns
from openprogram.interfaces.channels._transport import SendResult
from openprogram.programs.tools.interaction import chat_send as tool


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    out: list[tuple] = []
    monkeypatch.setattr(
        "openprogram.interfaces.channels._transport.post_message",
        lambda platform, account_id, target, text: out.append(
            (platform, account_id, target, text)) or SendResult.success(""))
    return out


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("openprogram.runtime.execution.run_control.get_current_session_id",
                        lambda: "s1")
    _active_turns.begin("s1", _active_turns.ActiveTurn(
        channel="telegram", account_id="acct1", peer_id="-100",
        peer_kind="group", speaker_id="701", ambient=True))
    yield "s1"
    _active_turns.end("s1")


def test_posts_to_the_chat_of_the_running_turn(session, posted) -> None:
    assert tool._chat_send_impl("  noon works  ") == "Posted to the telegram chat."
    assert posted == [("telegram", "acct1", "-100", "noon works")]


def test_refuses_outside_a_channel_turn(posted, monkeypatch) -> None:
    monkeypatch.setattr("openprogram.runtime.execution.run_control.get_current_session_id",
                        lambda: "web-session")
    assert tool._chat_send_impl("hi").startswith("Error:")
    assert tool._chat_send_impl("") == "Error: text is required."
    assert posted == []


def test_a_turn_has_a_send_quota(session, posted) -> None:
    for _ in range(_active_turns.MAX_SENDS_PER_TURN):
        assert not tool._chat_send_impl("x").startswith("Error:")
    assert tool._chat_send_impl("one more").startswith("Error: already posted")
    assert len(posted) == _active_turns.MAX_SENDS_PER_TURN


def test_a_failed_post_is_an_error(session, monkeypatch) -> None:
    monkeypatch.setattr(
        "openprogram.interfaces.channels._transport.post_message",
        lambda *a: SendResult.fail("rate_limit", "429"))
    assert tool._chat_send_impl("hi") == (
        "Error: the telegram message was not sent (rate_limit).")


def test_a_paired_speaker_may_use_it_and_only_channels_see_it() -> None:
    from openprogram.runtime.permissions.classifier import SAFE_AUTO_ALLOWLIST
    auth = paired_channel_authority("telegram", "acct1", "701", "Ada")
    decision = decide_tool_authority(auth, "chat_send")
    assert decision.allowed and decision.capability == "reply"
    assert "chat_send" in SAFE_AUTO_ALLOWLIST
    assert tool.CHANNEL_SOURCES == {"telegram", "discord", "slack", "wechat"}


def test_only_the_asker_answers_in_a_group(monkeypatch) -> None:
    from openprogram.interfaces.channels import _conversation as C
    monkeypatch.setattr(C, "_route", lambda *a: ("main", "s1", "s1"))
    handled: list[str] = []
    monkeypatch.setattr(
        "openprogram.interfaces.channels._question_commands.try_handle_question_command",
        lambda text, key, actor: handled.append(text) or "✓ 已记录你的回答。")
    _active_turns.begin("s1", _active_turns.ActiveTurn(
        channel="telegram", account_id="acct1", peer_id="-100",
        peer_kind="group", speaker_id="701"))
    try:
        def answer(speaker: str) -> str | None:
            return C.dispatch_inbound(
                channel="telegram", account_id="acct1", peer_kind="group",
                peer_id="-100", user_text="/answer q1 1", speaker_id=speaker)
        assert answer("702") == "只有发起这个问题的人可以回答它。"
        assert handled == []
        assert answer("701") == "✓ 已记录你的回答。"
    finally:
        _active_turns.end("s1")


def test_posts_into_the_thread_the_turn_is_in(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "openprogram.interfaces.channels._transport.post_message",
        lambda platform, account_id, target, text, **kw: calls.append(kw)
        or SendResult.success(""))
    monkeypatch.setattr("openprogram.runtime.execution.run_control.get_current_session_id",
                        lambda: "s-thread")
    _active_turns.begin("s-thread", _active_turns.ActiveTurn(
        channel="slack", account_id="acct1", peer_id="C1", peer_kind="group",
        speaker_id="U1", thread_id="171.5"))
    try:
        assert tool._chat_send_impl("one moment") == "Posted to the slack chat."
    finally:
        _active_turns.end("s-thread")
    assert calls == [{"thread_id": "171.5"}]


def test_telegram_typing_stays_in_the_forum_topic(monkeypatch) -> None:
    from openprogram.interfaces.channels import _transport
    payloads: list[dict] = []
    monkeypatch.setattr(_transport._accounts, "load_credentials",
                        lambda *a: {"bot_token": "t"})
    monkeypatch.setattr(_transport, "_tg_call",
                        lambda token, method, payload, **kw: payloads.append(payload)
                        or SendResult.success(""))
    assert _transport.send_typing("telegram", "acct1", "-100", thread_id="42").ok
    assert _transport.send_typing("telegram", "acct1", "-100").ok
    assert payloads[0]["message_thread_id"] == 42
    assert "message_thread_id" not in payloads[1]


def test_typing_indicator_passes_its_thread(monkeypatch) -> None:
    from openprogram.interfaces.channels.base import _TypingIndicator
    seen: list[dict] = []

    def send(platform, account_id, target, **kw):
        seen.append(kw)
        return SendResult.fail("not_supported", "stop after one")

    monkeypatch.setattr("openprogram.interfaces.channels._transport.send_typing", send)
    typing = _TypingIndicator("telegram", "acct1", "-100", "42")
    typing.start()
    typing.stop()
    assert seen == [{"thread_id": "42"}]


def test_a_channel_turn_remembers_its_thread(monkeypatch) -> None:
    from openprogram.interfaces.channels import _conversation
    seen: list = []
    monkeypatch.setattr(_conversation, "_reserve_run_slot", lambda *a, **kw: True)
    monkeypatch.setattr(_conversation, "_run_turn_body",
                        lambda **kw: seen.append(_active_turns.get("s-wired")))
    _conversation._run_session_turn(
        channel="telegram", account_id="acct1", peer={"kind": "group", "id": "-100"},
        peer_id="-100", user_text="hi", user_display="A", speaker_id="7",
        speaker_display="A", progress_stream=False, agent_id="main",
        session_key="s-wired", thread_id="42",
    )
    assert seen[0].thread_id == "42"
    assert _active_turns.get("s-wired") is None


def _question_posts(monkeypatch) -> list[tuple]:
    from openprogram.interfaces.channels import _question_bridge
    posts: list[tuple] = []
    monkeypatch.setattr(_question_bridge, "_channel_target_for_session",
                        lambda session_id: ("telegram", "acct1", "first-peer"))
    monkeypatch.setattr(
        "openprogram.interfaces.channels._transport.post_message",
        lambda platform, account_id, target, text, **kw: posts.append((target, kw))
        or SendResult.success(""))
    return posts


def _ask(session_id: str) -> None:
    from types import SimpleNamespace
    from openprogram.interfaces.channels import _question_bridge
    _question_bridge._on_question_asked(SimpleNamespace(
        payload={"session_id": session_id, "id": "q1", "prompt": "Which one?"}))


def test_a_question_goes_to_the_chat_and_thread_of_the_running_turn(monkeypatch) -> None:
    posts = _question_posts(monkeypatch)
    _active_turns.begin("s-asks", _active_turns.ActiveTurn(
        channel="telegram", account_id="acct1", peer_id="-100", peer_kind="group",
        speaker_id="7", thread_id="42"))
    try:
        _ask("s-asks")
    finally:
        _active_turns.end("s-asks")
    assert posts == [("-100", {"thread_id": "42"})]


def test_a_question_without_a_running_turn_uses_the_session_chat(monkeypatch) -> None:
    posts = _question_posts(monkeypatch)
    _ask("s-idle")
    assert posts == [("first-peer", {})]


@pytest.mark.parametrize("always_on, sent", [
    (True, "a: 1 · b: 2"),
    (False, "| a | b |\n|---|---|\n| 1 | 2 |"),
])
def test_only_an_always_on_agent_posts_its_tables_flat(session, posted, monkeypatch, always_on, sent) -> None:
    monkeypatch.setattr("openprogram.runtime.conversation.always_on.runtime.agent_for_session",
                        lambda session_id: "agent" if always_on else None)
    tool._chat_send_impl("| a | b |\n|---|---|\n| 1 | 2 |")
    assert posted[0][3] == sent


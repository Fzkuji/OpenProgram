"""Who the Agent answers in a group: the bound contact and the full-access list
run as the owner, the chat list gets the limited tier, and anyone else gets no
reaction at all. Covers the Telegram and Discord adapters and the shared lists.
"""
from __future__ import annotations

import argparse
import json
import threading
from types import SimpleNamespace

import pytest

from openprogram.interfaces.channels import _access, _room, accounts
from openprogram.interfaces.channels._message import ChannelMessage
from openprogram.interfaces.channels._transport import SendResult
from openprogram.interfaces.channels.base import Channel
from openprogram.interfaces.channels.implementations.discord import DiscordChannel
from openprogram.interfaces.channels.implementations.telegram import TelegramChannel


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    _room.reset_state()
    yield state
    _room.reset_state()


class _Recording:
    """Mixin: record replies instead of reaching a platform."""

    def send_text_full(self, target, text, thread_id=None):
        self.sent.append((target, text))
        return SendResult.success("mid")


class _Telegram(_Recording, TelegramChannel):
    def __init__(self) -> None:
        accounts.save_credentials("telegram", "a1", {"bot_token": "TOK"})
        TelegramChannel.__init__(self, account_id="a1")
        self.sent: list = []


class _Discord(_Recording, DiscordChannel):
    def __init__(self) -> None:
        Channel.__init__(self, account_id="a1")
        self.sent: list = []


@pytest.fixture(params=["telegram", "discord"])
def adapter(request):
    return _Telegram() if request.param == "telegram" else _Discord()


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake(**kw):
        calls.append(kw)

    monkeypatch.setattr(
        "openprogram.interfaces.channels._conversation.dispatch_inbound", fake)
    return calls


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch):
    """Any platform call in these tests is a failure."""

    def refuse(*args, **kwargs):
        raise AssertionError("platform call made")

    for name in ("post_message", "add_reaction", "send_typing"):
        monkeypatch.setattr(
            f"openprogram.interfaces.channels._transport.{name}", refuse)


def _group(adapter, user: str, text="hello", **kw) -> ChannelMessage:
    chat_type = "group" if adapter.platform_id == "telegram" else "channel"
    return ChannelMessage(text=text, chat_id="9000", user_id=user,
                          user_display=f"user{user}", chat_type=chat_type, **kw)


def _approve(adapter, user: str) -> None:
    _access.approve_user(adapter.platform_id, "a1", user)


def _list(adapter, user: str, access: str, **kw) -> None:
    _access.add_group_user(adapter.platform_id, "a1", user, access, **kw)


def test_bound_contact_runs_with_full_access(adapter, dispatched) -> None:
    _approve(adapter, "100")
    adapter._dispatch_and_reply(_group(adapter, "100"))
    assert [c["room_full_access"] for c in dispatched] == [True]
    assert adapter.sent == []


def test_listed_user_can_use_it(adapter, dispatched) -> None:
    _list(adapter, "200", "full")
    adapter._dispatch_and_reply(_group(adapter, "200"))
    assert [c["room_full_access"] for c in dispatched] == [True]


def test_chat_only_user_gets_the_limited_tier(adapter, dispatched) -> None:
    _list(adapter, "300", "chat")
    adapter._dispatch_and_reply(_group(adapter, "300"))
    assert [c["room_full_access"] for c in dispatched] == [False]


def test_unknown_sender_gets_no_reaction(adapter, dispatched) -> None:
    adapter._dispatch_and_reply(_group(adapter, "400"))
    assert dispatched == []
    assert adapter.sent == []
    data = _access.describe(adapter.platform_id, "a1")
    assert data["pending"] == {} and data["allowlist"] == {}
    assert not any(
        entry for key, entry in _room._pending.items() if key[0] == adapter.platform_id
    )


def test_sender_without_an_id_gets_no_reaction(adapter, dispatched) -> None:
    adapter._dispatch_and_reply(_group(adapter, ""))
    assert dispatched == [] and adapter.sent == []


def test_a_group_command_from_an_unknown_sender_is_ignored_too(adapter, dispatched) -> None:
    adapter._dispatch_and_reply(_group(adapter, "400", text="/activation always"))
    adapter._dispatch_and_reply(_group(adapter, "400", text="/answer q1 yes"))
    assert dispatched == [] and adapter.sent == []
    assert _room.activation(adapter.platform_id, "a1", "9000") == "always"
    assert _room._read_rooms(adapter.platform_id, "a1") == {}


def test_chat_only_user_cannot_change_the_room_or_reset_it(adapter, dispatched) -> None:
    _list(adapter, "300", "chat")
    for command in ("/activation mention", "/memory off", "/new"):
        adapter._dispatch_and_reply(_group(adapter, "300", text=command))
    assert dispatched == []
    assert _room._read_rooms(adapter.platform_id, "a1") == {}
    assert len(adapter.sent) == 3


def test_full_access_user_can_change_the_room(adapter, dispatched) -> None:
    _list(adapter, "200", "full")
    adapter._dispatch_and_reply(_group(adapter, "200", text="/memory off"))
    assert _room.memory_mode(adapter.platform_id, "a1", "9000") == "off"


def test_mention_gate_still_applies_to_listed_people(adapter, dispatched) -> None:
    _list(adapter, "300", "chat")
    accounts.set_setting(adapter.platform_id, "a1", "require_mention", "on")
    adapter._dispatch_and_reply(_group(adapter, "300"))
    assert dispatched == []
    adapter._dispatch_and_reply(_group(adapter, "300", mentioned=True))
    assert len(dispatched) == 1


def test_direct_message_still_goes_through_pairing(adapter, dispatched) -> None:
    _list(adapter, "200", "full")
    adapter._dispatch_and_reply(ChannelMessage(
        text="hi", chat_id="200", user_id="200", chat_type="direct"))
    assert dispatched == []
    assert len(adapter.sent) == 1 and "pairing code" in adapter.sent[0][1]


def test_a_group_with_its_own_lists_ignores_the_account_lists(dispatched) -> None:
    adapter = _Telegram()
    _list(adapter, "200", "full")
    _list(adapter, "201", "full", chat_id="9000")
    adapter._dispatch_and_reply(_group(adapter, "200"))
    assert dispatched == []
    adapter._dispatch_and_reply(_group(adapter, "201"))
    assert [c["room_full_access"] for c in dispatched] == [True]


def test_the_bound_contact_stays_in_a_group_with_its_own_lists(dispatched) -> None:
    adapter = _Telegram()
    _approve(adapter, "100")
    _list(adapter, "201", "chat", chat_id="9000")
    adapter._dispatch_and_reply(_group(adapter, "100"))
    assert [c["room_full_access"] for c in dispatched] == [True]


def test_a_discord_thread_uses_its_parent_channels_lists(dispatched) -> None:
    adapter = _Discord()
    _list(adapter, "201", "chat", chat_id="9000")
    adapter._dispatch_and_reply(ChannelMessage(
        text="hi", chat_id="9500", user_id="201", user_display="x",
        chat_type="channel", parent_chat_id="9000"))
    assert [c["room_full_access"] for c in dispatched] == [False]


def test_removing_the_last_user_keeps_the_group_closed_until_reset() -> None:
    _access.add_group_user("telegram", "a1", "200", "full")
    _access.add_group_user("telegram", "a1", "201", "chat", chat_id="-100")
    assert _access.remove_group_user("telegram", "a1", "201", chat_id="-100") is True
    assert _access.group_sender_access("telegram", "a1", "-100", "200") is None
    assert _access.reset_group_users("telegram", "a1", "-100") is True
    assert _access.group_sender_access("telegram", "a1", "-100", "200") == "full"


def test_adding_a_user_moves_them_between_lists() -> None:
    _access.add_group_user("telegram", "a1", "200", "chat")
    _access.add_group_user("telegram", "a1", "200", "full")
    users = _access.group_users("telegram", "a1")
    assert set(users["group_full_access"]) == {"200"}
    assert users["group_chat_only"] == {}
    assert _access.group_sender_access("telegram", "a1", "-100", "200") == "full"


@pytest.mark.parametrize("channel,bad", [
    ("telegram", "@alice"), ("telegram", "-1001234"), ("discord", "alice#1234"),
    ("slack", "alice"), ("telegram", ""),
])
def test_names_are_refused_as_user_ids(channel: str, bad: str) -> None:
    with pytest.raises(ValueError):
        _access.add_group_user(channel, "a1", bad, "full")


def test_lists_survive_the_pairing_writes() -> None:
    _access.add_group_user("telegram", "a1", "200", "chat")
    _access.decide_inbound_sender("telegram", "a1", "999", "x")
    _access.approve_user("telegram", "a1", "998")
    assert set(_access.group_users("telegram", "a1")["group_chat_only"]) == {"200"}


def test_retired_group_access_setting_is_dropped_once_read(_tmp_state) -> None:
    accounts.create("telegram", "a1")
    path = accounts.account_meta_path("telegram", "a1")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["settings"] = {"group_access": "owner", "require_mention": "on"}
    path.write_text(json.dumps(raw), encoding="utf-8")

    assert accounts.get_settings("telegram", "a1") == {"require_mention": "on"}
    assert "group_access" not in json.loads(path.read_text(encoding="utf-8"))["settings"]
    with pytest.raises(ValueError):
        accounts.set_setting("telegram", "a1", "group_access", "owner")


def _cli(*argv: str) -> None:
    from openprogram.interfaces.cli.commands.channels import _dispatch_accounts_verb
    from openprogram.interfaces.cli.parser import build_parser

    args = build_parser().parse_args(["channels", "accounts", *argv])
    _dispatch_accounts_verb(args, argparse.ArgumentParser())


def test_cli_manages_the_lists(capsys) -> None:
    accounts.create("telegram", "default")
    _cli("group-users", "add", "telegram", "200", "--access", "chat")
    _cli("group-users", "add", "telegram", "201", "--access", "full", "--group", "-100")
    assert _access.group_sender_access("telegram", "default", "-200", "200") == "chat"
    assert _access.group_sender_access("telegram", "default", "-100", "201") == "full"
    assert _access.group_sender_access("telegram", "default", "-100", "200") is None
    capsys.readouterr()
    _cli("group-users", "list", "telegram", "--group", "-100")
    assert "201" in capsys.readouterr().out
    _cli("group-users", "reset", "telegram", "--group", "-100")
    assert _access.group_sender_access("telegram", "default", "-100", "200") == "chat"
    _cli("group-users", "remove", "telegram", "200")
    assert _access.group_sender_access("telegram", "default", "-200", "200") is None


def test_cli_refuses_a_name_and_an_unknown_account(capsys) -> None:
    accounts.create("telegram", "default")
    with pytest.raises(SystemExit):
        _cli("group-users", "add", "telegram", "@alice", "--access", "full")
    with pytest.raises(SystemExit):
        _cli("group-users", "add", "telegram", "200", "--access", "full", "--id", "nope")


def test_telegram_group_messages_identify_senders_by_numeric_id(dispatched) -> None:
    adapter = _Telegram()
    _list(adapter, "777", "chat")
    got: list = []
    adapter.handle_inbound = got.append  # type: ignore[method-assign]
    adapter._handle_update({"update_id": 1, "message": {
        "chat": {"id": -100123, "type": "group", "title": "T"},
        "from": {"id": 777, "username": "alice"},
        "date": 1716000000, "text": "hello",
    }})
    assert got[0].user_id == "777"
    assert _access.group_sender_access("telegram", "a1", got[0].chat_id, "alice") is None
    assert _access.group_sender_access("telegram", "a1", got[0].chat_id, got[0].user_id) == "chat"


def _discord_thread_message(user: str, content: str = "hello"):
    """The parts of a discord.Message the adapter reads: a post in a guild thread."""
    return SimpleNamespace(
        content=content, mentions=[], attachments=[], reference=None, id=77,
        created_at=None, guild=object(),
        author=SimpleNamespace(id=int(user), bot=False),
        channel=SimpleNamespace(id=9500, parent_id=9000, type="public_thread"),
    )


def test_discord_adapter_reads_stable_ids_and_the_parent_channel() -> None:
    message = _Discord()._channel_message(_discord_thread_message("201"), None)
    assert (message.user_id, message.chat_id, message.chat_type) == ("201", "9500", "channel")
    assert (message.parent_chat_id, message.thread_id) == ("9000", "9500")


@pytest.mark.parametrize("user,expected", [("100", [True]), ("201", [True]), ("202", [False]), ("400", [])])
def test_discord_thread_messages_get_the_three_outcomes(dispatched, user, expected) -> None:
    adapter = _Discord()
    _approve(adapter, "100")
    _list(adapter, "201", "full", chat_id="9000")
    _list(adapter, "202", "chat", chat_id="9000")
    adapter._dispatch_and_reply(adapter._channel_message(_discord_thread_message(user), None))
    assert [c["room_full_access"] for c in dispatched] == expected
    assert adapter.sent == []


@pytest.mark.parametrize("chat_id", ["", "-100"])
def test_a_bound_contact_cannot_be_listed_as_chat_only(chat_id: str) -> None:
    _access.approve_user("telegram", "a1", "555")
    with pytest.raises(ValueError, match="access revoke telegram 555 --id a1"):
        _access.add_group_user("telegram", "a1", "555", "chat", chat_id=chat_id)
    assert _access.group_users("telegram", "a1", chat_id=chat_id)["group_chat_only"] == {}
    assert _access.add_group_user("telegram", "a1", "555", "full", chat_id=chat_id) == "555"


def test_after_revoke_a_former_contact_can_be_listed_as_chat_only() -> None:
    _access.approve_user("telegram", "a1", "555")
    _access.revoke("telegram", "a1", "555")
    _access.add_group_user("telegram", "a1", "555", "chat")
    assert _access.group_sender_access("telegram", "a1", "-100", "555") == "chat"


def test_cli_reports_a_bound_contact_listed_as_chat_as_an_error(capsys) -> None:
    accounts.create("telegram", "default")
    _access.approve_user("telegram", "default", "555")
    with pytest.raises(SystemExit):
        _cli("group-users", "add", "telegram", "555", "--access", "chat")
    out = capsys.readouterr().out
    assert "[error]" in out and "access revoke telegram 555" in out


@pytest.mark.parametrize("via_code", [False, True])
def test_binding_someone_takes_them_off_the_chat_lists(via_code: bool) -> None:
    _access.add_group_user("telegram", "a1", "555", "chat")
    _access.add_group_user("telegram", "a1", "555", "chat", chat_id="-100")
    _access.add_group_user("telegram", "a1", "556", "chat", chat_id="-100")
    if via_code:
        _access.decide_inbound_sender("telegram", "a1", "555", "x")
        code = _access.describe("telegram", "a1")["pending"]["555"]["code"]
        assert _access.approve("telegram", "a1", code) == "555"
    else:
        _access.approve_user("telegram", "a1", "555")
    assert _access.group_users("telegram", "a1")["group_chat_only"] == {}
    assert set(_access.group_users("telegram", "a1", chat_id="-100")["group_chat_only"]) == {"556"}


def test_room_writes_leave_no_shared_temporary_file(_tmp_state) -> None:
    accounts.create("telegram", "a1")
    _room.set_activation("telegram", "a1", "-100", "mention")
    assert not _room._rooms_path("telegram", "a1").with_suffix(".tmp").exists()


def test_a_room_refusal_names_both_causes_and_full_commands() -> None:
    from openprogram.runtime.permissions import refusal

    decision = SimpleNamespace(reason_code="AUTHORITY_X", capability="run commands")
    text = refusal.authority("bash", None, decision, "Ann", room=True).text
    assert "group-users add <channel> <user id> --access full" in text
    assert "set <channel> sender_access owner" in text

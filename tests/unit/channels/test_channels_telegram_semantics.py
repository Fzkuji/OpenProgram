"""Telegram adapter semantics: explicit group-session config, mention
detection, attachment/quote parsing, and the per-user target split in
_transport. Whether an unmentioned group message starts a turn is the
room layer's call (test_channels_rooms.py).
"""
from __future__ import annotations

import os
import sys
import threading
import types

import httpx
import pytest

from openprogram.interfaces.channels import accounts as _accounts
from openprogram.interfaces.channels.implementations import telegram as telegram_module
from openprogram.interfaces.channels.implementations import wechat as wechat_module
from openprogram.interfaces.channels.implementations.slack import SlackChannel
from openprogram.interfaces.channels.implementations.telegram import TelegramChannel


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)


def _adapter(**settings) -> TelegramChannel:
    _accounts.save_credentials("telegram", "a1", {"bot_token": "TOK"})
    for k, v in settings.items():
        _accounts.set_setting("telegram", "a1", k, v)
    return TelegramChannel(account_id="a1")


def _update(text="hello", *, chat_type="group", reply_from_id=None,
            photo=False, document=False, caption=None):
    msg = {
        "chat": {"id": -100123, "type": chat_type, "title": "Team"},
        "from": {"id": 777, "username": "alice"},
        "date": 1716000000,
    }
    if text is not None:
        msg["text"] = text
    if caption is not None:
        msg["caption"] = caption
    if reply_from_id is not None:
        msg["reply_to_message"] = {
            "message_id": 5, "from": {"id": reply_from_id},
            "text": "earlier words",
        }
    if photo:
        msg["photo"] = [
            {"file_id": "small", "file_size": 10},
            {"file_id": "big", "file_size": 999},
        ]
    if document:
        msg["document"] = {"file_id": "doc1", "file_name": "notes.pdf",
                           "mime_type": "application/pdf", "file_size": 5}
    return {"update_id": 1, "message": msg}


def _capture(ch: TelegramChannel, upd: dict):
    got: list = []
    ch.handle_inbound = lambda m: got.append(m)  # type: ignore[method-assign]
    ch._handle_update(upd)
    return got


# ---------------------------------------------------------------------------
# settings are explicit config
# ---------------------------------------------------------------------------

def test_settings_validation() -> None:
    _accounts.save_credentials("telegram", "a1", {"bot_token": "TOK"})
    with pytest.raises(ValueError):
        _accounts.set_setting("telegram", "a1", "group_sessions", "sometimes")
    with pytest.raises(ValueError):
        _accounts.set_setting("telegram", "a1", "unknown_key", "on")
    with pytest.raises(ValueError):
        _accounts.set_setting("discord", "a1", "group_sessions", "shared")


def test_defaults_shared_session_every_message_reaches_base() -> None:
    ch = _adapter()
    assert ch.group_sessions == "shared"
    got = _capture(ch, _update("plain group chatter"))
    assert len(got) == 1
    assert got[0].chat_type == "group"
    assert got[0].mentioned is False


def test_per_user_group_sessions_scope_peer_id() -> None:
    ch = _adapter(group_sessions="per-user")
    got = _capture(ch, _update("hi"))
    assert ch.peer_id_for(got[0]) == "-100123_777"
    # DM unaffected
    dm = _capture(ch, _update("hi", chat_type="private"))
    assert ch.peer_id_for(dm[0]) == "-100123"


@pytest.mark.parametrize(("sender", "expected"), [
    ({"id": 666, "username": "alice", "first_name": "Ignored"}, "alice"),
    ({"id": 777, "first_name": "Alice", "last_name": "Ng"}, "Alice Ng"),
    ({"id": 888}, "888"),
])
def test_group_sender_display_never_falls_back_to_the_chat(
    sender: dict, expected: str,
) -> None:
    ch = _adapter()
    update = _update("hi")
    update["message"]["from"] = sender
    update["message"]["chat"]["username"] = "team_handle"

    got = _capture(ch, update)

    assert got[0].user_display == expected


def test_transport_splits_per_user_target() -> None:
    from openprogram.interfaces.channels._transport import _tg_chat_id
    assert _tg_chat_id("-100123_777") == -100123
    assert _tg_chat_id("42") == 42
    assert _tg_chat_id("@channelname") == "@channelname"


# ---------------------------------------------------------------------------
# mention detection
# ---------------------------------------------------------------------------

def test_unaddressed_group_message_is_not_mentioned() -> None:
    ch = _adapter(require_mention="on")
    ch.bot_username = "mybot"
    ch.bot_user_id = "999"
    got = _capture(ch, _update("just chatting"))
    assert [m.mentioned for m in got] == [False]


def test_mention_is_detected_and_stripped() -> None:
    ch = _adapter()
    ch.bot_username = "mybot"
    got = _capture(ch, _update("@MyBot run the report"))
    assert (got[0].text, got[0].mentioned) == ("run the report", True)


def test_group_command_suffix_counts_as_mention() -> None:
    ch = _adapter()
    ch.bot_username = "mybot"
    got = _capture(ch, _update("/activation@mybot always"))
    assert (got[0].text, got[0].mentioned) == ("/activation always", True)


def test_reply_to_bot_counts_as_mention() -> None:
    ch = _adapter()
    ch.bot_username = "mybot"
    ch.bot_user_id = "999"
    got = _capture(ch, _update("and this too", reply_from_id=999))
    assert (got[0].text, got[0].mentioned) == ("and this too", True)


def test_bot_sender_is_flagged() -> None:
    ch = _adapter()
    update = _update("beep")
    update["message"]["from"] = {"id": 5, "username": "otherbot", "is_bot": True}
    assert _capture(ch, update)[0].from_bot is True


# ---------------------------------------------------------------------------
# attachments + quoted parse
# ---------------------------------------------------------------------------

def test_photo_and_document_parsed_with_caption() -> None:
    ch = _adapter()
    got = _capture(ch, _update(text=None, caption="see these",
                               photo=True, document=True))
    assert len(got) == 1
    m = got[0]
    assert m.text == "see these"
    kinds = [(a.file_id, a.mime, a.name) for a in m.attachments]
    assert kinds == [
        ("big", "image/jpeg", "photo.jpg"),      # largest photo size
        ("doc1", "application/pdf", "notes.pdf"),
    ]


def test_attachment_only_message_not_dropped() -> None:
    ch = _adapter()
    got = _capture(ch, _update(text=None, photo=True))
    assert len(got) == 1
    assert got[0].text == ""
    assert got[0].attachments


def test_quoted_text_from_reply() -> None:
    ch = _adapter()
    got = _capture(ch, _update("what about that?", reply_from_id=123))
    assert got[0].quoted_text == "earlier words"
    assert got[0].reply_to_id == "5"


def test_text_free_update_ignored() -> None:
    ch = _adapter()
    assert _capture(ch, _update(text=None)) == []


# --- Adapter loops: revoked tokens, old edits, WeChat, Slack -----------------

class _Client:
    """Stands in for the safe HTTP client; answers polls from a script."""

    def __init__(self, responses: list, stop: threading.Event):
        self.responses = responses
        self.stop = stop
        self.polls = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _next(self, url):
        self.polls += 1
        status, body = self.responses.pop(0)
        if not self.responses:
            self.stop.set()
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    def get(self, url, **_kw):
        if url.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 1}},
                                  request=httpx.Request("GET", url))
        return self._next(url)

    def post(self, url, **_kw):
        return self._next(url)


def _run_telegram(monkeypatch, responses, stop=None):
    ch = _adapter()
    stop = stop or threading.Event()
    client = _Client(responses, stop)
    monkeypatch.setattr("openprogram.host.security.safe_http.safe_client",
                        lambda *_a, **_k: client)
    sleeps: list[float] = []
    monkeypatch.setattr(telegram_module.time, "sleep", sleeps.append)
    ch.run(stop)
    return client, sleeps


@pytest.mark.parametrize("status", [401, 404])
def test_telegram_stops_when_the_token_is_rejected(monkeypatch, status):
    # A second answer stays queued: the adapter must not ask again.
    client, sleeps = _run_telegram(
        monkeypatch, [(status, {"ok": False}), (200, {"ok": True, "result": []})],
    )
    assert client.polls == 1
    assert sleeps == []


def test_telegram_keeps_polling_through_a_conflict(monkeypatch):
    client, sleeps = _run_telegram(
        monkeypatch, [(409, {"ok": False}), (200, {"ok": True, "result": []})],
    )
    assert client.polls == 2
    assert sleeps == [5]


def _edit_update(ch: TelegramChannel, sent_at: float) -> dict:
    upd = _update("changed", chat_type="private")
    upd["edited_message"] = {**upd.pop("message"), "date": sent_at,
                             "edit_date": ch.started_at + 10, "message_id": 9}
    return upd


def test_telegram_ignores_an_edit_of_a_message_from_before_start():
    ch = _adapter()
    assert _capture(ch, _edit_update(ch, ch.started_at - 60)) == []


def test_telegram_still_takes_an_edit_of_a_message_from_this_run():
    ch = _adapter()
    got = _capture(ch, _edit_update(ch, ch.started_at + 1))
    assert [m.text for m in got] == ["changed"]


def test_telegram_new_message_is_not_judged_by_its_date():
    ch = _adapter()
    upd = _update("queued while down", chat_type="private")
    upd["message"]["date"] = ch.started_at - 60
    assert len(_capture(ch, upd)) == 1


def _run_wechat(monkeypatch, responses):
    _accounts.save_credentials(
        "wechat", "a1", {"bot_token": "TOK", "ilink_bot_id": "BOT"},
    )
    stop = threading.Event()
    client = _Client(responses, stop)
    monkeypatch.setattr(wechat_module, "_wechat_client", lambda _base: client)
    sleeps: list[float] = []
    monkeypatch.setattr(wechat_module.time, "sleep", sleeps.append)
    wechat_module.WechatChannel(account_id="a1").run(stop)
    return sleeps


def test_wechat_backs_off_on_non_2xx(monkeypatch):
    sleeps = _run_wechat(monkeypatch, [(503, {}), (503, {}), (500, {})])
    assert sleeps == [3, 6, 12]


def test_wechat_ok_poll_does_not_sleep_and_resets_backoff(monkeypatch):
    sleeps = _run_wechat(
        monkeypatch,
        [(503, {}), (200, {"msgs": []}), (200, {"msgs": []}), (503, {})],
    )
    assert sleeps == [3, 3]


def test_wechat_cursor_write_is_atomic_and_round_trips(monkeypatch):
    wechat_module._save_cursor_for_account("a1", "first")
    wechat_module._save_cursor_for_account("a1", "second")
    assert wechat_module._load_cursor_for_account("a1") == "second"

    def fail(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", fail)
    wechat_module._save_cursor_for_account("a1", "third")
    assert wechat_module._load_cursor_for_account("a1") == "second"


def _slack_handler(monkeypatch):
    """Run the Slack adapter against stand-in SDK modules; return its listener."""
    listeners: list = []

    class Web:
        def __init__(self, token):
            self.token = token

        def auth_test(self):
            return {"user_id": "UBOT", "user": "bot"}

        def conversations_replies(self, **_kw):
            return {"messages": [{"text": "the parent"}]}

    class Socket:
        socket_mode_request_listeners = listeners

        def __init__(self, app_token, web_client):
            self.app_token = app_token

        def send_socket_mode_response(self, _response):
            return None

        def connect(self):
            return None

        def disconnect(self):
            return None

    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)

    module("slack_sdk")
    module("slack_sdk.web", WebClient=Web)
    module("slack_sdk.socket_mode", SocketModeClient=Socket)
    module("slack_sdk.socket_mode.request", SocketModeRequest=object)
    module("slack_sdk.socket_mode.response",
           SocketModeResponse=lambda envelope_id: envelope_id)
    monkeypatch.setattr(
        "openprogram.host.security.safe_http.require_active_sdk_transport",
        lambda *_a, **_k: None,
    )
    _accounts.save_credentials(
        "slack", "a1", {"bot_token": "xoxb", "app_token": "xapp"},
    )
    ch = SlackChannel(account_id="a1")
    got: list = []
    ch.handle_inbound = got.append  # type: ignore[method-assign]
    stop = threading.Event()
    stop.set()
    ch.run(stop)
    return listeners[0], got


def _slack_event(**event):
    base = {"type": "message", "channel": "C1", "user": "U1", "text": "hi",
            "ts": "200.0"}
    return types.SimpleNamespace(
        type="events_api", envelope_id="e", payload={"event": {**base, **event}},
    )


def test_slack_takes_a_thread_broadcast(monkeypatch):
    handle, got = _slack_handler(monkeypatch)
    handle(None, _slack_event(subtype="thread_broadcast", thread_ts="100.0"))
    assert [(m.text, m.thread_id, m.quoted_text) for m in got] == [
        ("hi", "100.0", "the parent"),
    ]


def test_slack_still_ignores_other_subtypes(monkeypatch):
    handle, got = _slack_handler(monkeypatch)
    handle(None, _slack_event(subtype="message_changed"))
    assert got == []


@pytest.mark.parametrize("field,expected_name,expected_mime", [
    ("voice", "voice.ogg", "audio/ogg"),
    ("audio", "audio.mp3", "audio/mpeg"),
    ("video", "video.mp4", "video/mp4"),
    ("video_note", "video_note.mp4", "video/mp4"),
    ("animation", "animation.mp4", "video/mp4"),
])
def test_audio_and_video_become_attachments(field, expected_name, expected_mime) -> None:
    [attachment] = TelegramChannel._parse_attachments(
        {field: {"file_id": "f1", "file_size": 9}})
    assert (attachment.name, attachment.mime, attachment.file_id, attachment.size) == (
        expected_name, expected_mime, "f1", 9)
    assert attachment.voice is (field == "voice")


def test_gif_counts_once_though_telegram_sends_animation_and_document() -> None:
    attachments = TelegramChannel._parse_attachments({
        "animation": {"file_id": "same", "file_name": "cat.mp4", "mime_type": "video/mp4"},
        "document": {"file_id": "same", "file_name": "cat.mp4", "mime_type": "video/mp4"},
    })
    assert [a.name for a in attachments] == ["cat.mp4"]


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300

    def json(self) -> dict:
        return {"ok": self.is_success, "result": []}


def _poll_with(monkeypatch: pytest.MonkeyPatch, status_code: int, stop: threading.Event) -> list[str]:
    calls: list[str] = []

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get(self, url: str, **_):
            calls.append(url)
            if len([u for u in calls if u.endswith("/getUpdates")]) > 1:
                stop.set()
            return _FakeResponse(status_code)

    monkeypatch.setattr(
        "openprogram.host.security.safe_http.safe_client", lambda *_a, **_k: _Client(),
    )
    monkeypatch.setattr(
        "openprogram.interfaces.channels.implementations.telegram.time.sleep", lambda _: None,
    )
    return calls


@pytest.mark.parametrize("status_code", [401, 404])
def test_a_rejected_token_ends_the_run_for_good(monkeypatch, status_code) -> None:
    stop = threading.Event()
    calls = _poll_with(monkeypatch, status_code, stop)
    _adapter().run(stop)
    assert len([u for u in calls if u.endswith("/getUpdates")]) == 1
    assert not stop.is_set()


def test_a_server_error_keeps_polling(monkeypatch) -> None:
    stop = threading.Event()
    calls = _poll_with(monkeypatch, 502, stop)
    _adapter().run(stop)
    assert len([u for u in calls if u.endswith("/getUpdates")]) == 2

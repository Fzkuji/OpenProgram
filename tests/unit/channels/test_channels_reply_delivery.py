"""Reply delivery on edit-capable platforms: the final reply survives a
failed placeholder edit, long Telegram replies split on rendered length
at a line boundary, and replies go back into the inbound thread (Slack
thread / Telegram forum topic).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.interfaces.channels import _conversation as C
from openprogram.interfaces.channels import _format
from openprogram.interfaces.channels import _transport
from openprogram.interfaces.channels import accounts as _accounts
from openprogram.interfaces.channels._message import ChannelMessage
from openprogram.interfaces.channels._transport import SendResult
from openprogram.interfaces.channels.base import Channel, MessageHandle


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch):
    """Fake post/patch: records every call, patch result is settable."""
    calls: dict = {"post": [], "patch": [],
                   "patch_result": SendResult.success("m1")}

    def post(platform, account_id, target, text, thread_id=None):
        calls["post"].append((text, thread_id))
        return SendResult.success("m2")

    def patch(platform, account_id, target, message_id, text):
        calls["patch"].append(text)
        return calls["patch_result"]

    monkeypatch.setattr(_transport, "post_message", post)
    monkeypatch.setattr(_transport, "patch_message", patch)
    return calls


# ---------------------------------------------------------------------------
# 1. A failed final edit still delivers the reply
# ---------------------------------------------------------------------------

def test_final_reply_is_posted_when_the_placeholder_edit_fails(wire) -> None:
    wire["patch_result"] = SendResult.fail("bad_target", "message to edit not found")
    handle = MessageHandle("telegram", "a1", "42", "m1")
    C._finish_placeholder(handle, "the answer")
    assert wire["patch"] == ["the answer"]
    assert wire["post"] == [("the answer", None)]


def test_successful_final_edit_posts_nothing(wire) -> None:
    C._finish_placeholder(MessageHandle("slack", "a1", "C_U", "1.2"), "short")
    assert wire["patch"] == ["short"]
    assert wire["post"] == []


# ---------------------------------------------------------------------------
# 2. Telegram long replies split on rendered length, at a line boundary
# ---------------------------------------------------------------------------

def _escaping_heavy_reply() -> str:
    # Under MAX_CHARS raw, but HTML escaping pushes it over 4096 rendered.
    body = "if a < b && c > d: x = '&'\n" * 140
    return "Here is the code:\n```python\n" + body + "```\nDone."


def test_split_uses_rendered_length_and_closes_the_fence() -> None:
    text = _escaping_heavy_reply()
    assert len(text) <= _transport.MAX_CHARS["telegram"]
    assert len(_format.render("telegram", text)) > _transport.HARD_CAPS["telegram"]

    head, tail = _transport.split_for_edit("telegram", text, "\n...")
    assert len(_format.render("telegram", head)) <= _transport.HARD_CAPS["telegram"]
    assert head.endswith("```\n...")          # open fence closed before the cut
    assert tail.startswith("```python\n")     # and reopened with its language
    assert tail.endswith("Done.")
    # Cut on a whole line: nothing lost, no line split in two.
    body_lines = [ln for ln in (head + "\n" + tail).splitlines()
                  if ln.startswith("if a")]
    assert len(body_lines) == 140
    assert all(ln == "if a < b && c > d: x = '&'" for ln in body_lines)


def test_split_keeps_a_reply_that_fits() -> None:
    assert _transport.split_for_edit("telegram", "**hi**", "\n...") == ("**hi**", "")


def test_long_reply_edits_the_head_and_posts_the_rest(wire) -> None:
    text = _escaping_heavy_reply()
    C._finish_placeholder(MessageHandle("telegram", "a1", "42", "m1"), text,
                          thread_id="9")
    [head] = wire["patch"]
    assert head.endswith("(continued ↓)")
    assert len(_format.render("telegram", head)) <= _transport.HARD_CAPS["telegram"]
    [(tail, thread)] = wire["post"]
    assert tail.startswith("```python") and tail.endswith("Done.")
    assert thread == "9"


# ---------------------------------------------------------------------------
# 3. Replies go back into the inbound thread
# ---------------------------------------------------------------------------

def test_post_message_passes_thread_only_to_threaded_platforms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict = {}

    def threaded(account_id, target, text, thread_id=None):
        seen["slack"] = thread_id
        return SendResult.success("1")

    def flat(account_id, target, text):           # no thread parameter
        seen["discord"] = text
        return SendResult.success("2")

    monkeypatch.setitem(_transport._POSTERS, "slack", threaded)
    monkeypatch.setitem(_transport._POSTERS, "discord", flat)
    assert _transport.post_message("slack", "a", "C_U", "hi", thread_id="1.5").ok
    assert _transport.post_message("discord", "a", "C_U", "hi", thread_id="x").ok
    assert seen == {"slack": "1.5", "discord": "hi"}


def test_telegram_send_and_file_carry_the_forum_topic(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    _accounts.save_credentials("telegram", "a1", {"bot_token": "TOK"})
    payloads: list[dict] = []
    monkeypatch.setattr(_transport, "_tg_call",
                        lambda token, method, payload, success_id=None:
                        payloads.append(payload) or SendResult.success("5"))
    assert _transport.post_message("telegram", "a1", "-100", "hi",
                                   thread_id="12").ok
    assert payloads[0]["message_thread_id"] == 12
    assert _transport.post_message("telegram", "a1", "-100", "hi").ok
    assert "message_thread_id" not in payloads[1]

    sent: dict = {}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **kw):
            sent.update(kw["data"])
            return SimpleNamespace(json=lambda: {"ok": True,
                                                 "result": {"message_id": 3}})

    monkeypatch.setattr(_transport, "safe_client", lambda purpose: _Client())
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert _transport.post_file("telegram", "a1", "-100", str(f),
                                thread_id="12").ok
    assert sent["message_thread_id"] == 12


def test_slack_send_and_file_carry_thread_ts(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    _accounts.save_credentials("slack", "a1", {"bot_token": "xoxb"})
    bodies: list[tuple[str, dict]] = []

    def _resp(data):
        return SimpleNamespace(is_success=True, status_code=200, text="",
                               headers={}, json=lambda: data)

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **kw):
            bodies.append((url, kw.get("json") or {}))
            if url.endswith("getUploadURLExternal"):
                return _resp({"ok": True, "upload_url": "https://files.slack.com/u",
                              "file_id": "F1"})
            return _resp({"ok": True, "ts": "2.0"})

    monkeypatch.setattr(_transport, "safe_client", lambda purpose: _Client())
    assert _transport.post_message("slack", "a1", "C1_U1", "hi",
                                   thread_id="1.5").ok
    assert bodies[-1][1]["thread_ts"] == "1.5"
    assert _transport.post_message("slack", "a1", "C1_U1", "hi").ok
    assert "thread_ts" not in bodies[-1][1]

    f = tmp_path / "a.txt"
    f.write_text("x")
    assert _transport.post_file("slack", "a1", "C1_U1", str(f),
                                thread_id="1.5").ok
    complete = [b for u, b in bodies if u.endswith("completeUploadExternal")]
    assert complete[-1]["thread_ts"] == "1.5"


def test_telegram_parses_the_forum_topic_but_not_reply_chains() -> None:
    from openprogram.interfaces.channels.implementations.telegram import TelegramChannel
    _accounts.save_credentials("telegram", "a1", {"bot_token": "TOK"})
    ch = TelegramChannel(account_id="a1")
    got: list = []
    ch.handle_inbound = lambda m: got.append(m)  # type: ignore[method-assign]

    def upd(**extra):
        msg = {"chat": {"id": -100, "type": "supergroup"},
               "from": {"id": 7}, "text": "hi", "date": 1, **extra}
        return {"update_id": 1, "message": msg}

    ch._handle_update(upd(message_thread_id=12, is_topic_message=True))
    ch._handle_update(upd(message_thread_id=40))      # plain reply chain
    assert [m.thread_id for m in got] == ["12", ""]
    assert ch.peer_id_for(got[0]) == ch.peer_id_for(got[1]) == "-100"


class _ThreadChannel(Channel):
    platform_id = "slack"

    def __init__(self) -> None:
        super().__init__(account_id="a1")
        self.sent: list[tuple[str, str, object]] = []

    def run(self, stop):  # pragma: no cover
        pass

    def send_text_full(self, target, text, thread_id=None):
        self.sent.append((target, text, thread_id))
        return SendResult.success("1")


def test_base_dispatches_and_replies_in_the_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.interfaces.channels import _access
    _access.approve_user("slack", "a1", "U1")
    seen: dict = {}
    monkeypatch.setattr(
        "openprogram.interfaces.channels._conversation.dispatch_inbound",
        lambda **kw: seen.update(kw) or "reply")
    ch = _ThreadChannel()
    ch._dispatch_and_reply(ChannelMessage(
        text="hi", chat_id="C1", user_id="U1", chat_type="direct",
        message_id="1.6", thread_id="1.5"))
    assert seen["thread_id"] == "1.5"
    assert seen["peer_id"] == "C1"            # routing unchanged by the thread
    assert ch.sent == [("C1", "reply", "1.5")]

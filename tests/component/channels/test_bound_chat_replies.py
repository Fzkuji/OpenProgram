"""A chat bound to a channel account gets every assistant reply in that
channel, whatever started the turn (``runtime/conversation/bound_reply``).

The transport is stubbed: nothing here reaches a real platform.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openprogram.interfaces.channels import _conversation as C
from openprogram.interfaces.channels._transport import SendResult
from openprogram.runtime.context.session_db import SessionDB
from openprogram.runtime.conversation import bound_reply as B
from openprogram.runtime.conversation import turn_runtime as D
from tests.component.channels.test_channels_dispatch_inbound import (  # noqa: F401 - fixtures
    make_delta_stream,
    stub_agent_profile,
    stub_model,
    stub_routing,
    tmp_db,
)

FIRST = "First paragraph of the answer. " * 8
REPLY_PARTS = [FIRST, "\n\nSecond part.", " The end."]
REPLY = "".join(REPLY_PARTS)


@pytest.fixture
def world(tmp_db: SessionDB, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import openprogram.interfaces.channels  # noqa: F401 - registers the channel link
    from openprogram.runtime import events

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    posted: list[tuple[str, str, str, str]] = []
    result = {"value": SendResult.success("")}

    def _post(platform, account_id, target, text, thread_id=None):
        posted.append((platform, account_id, target, text))
        return result["value"]

    monkeypatch.setattr("openprogram.interfaces.channels._transport.post_message", _post)
    monkeypatch.setattr("openprogram.interfaces.channels.accounts.owner_peer_id",
                        lambda channel, account_id: "owner-1" if channel == "wechat" else "")
    frames: list[dict] = []
    monkeypatch.setattr(events, "emit_ws_frame", frames.append)
    return tmp_db, posted, frames, result


def _bound_chat(db: SessionDB, sid: str, channel: str = "wechat", *, alias_peer: str | None = None):
    from openprogram.runtime.agent_registry import session_aliases

    db.create_session(sid, "main")
    db.update_session(sid, channel=channel, account_id="acct1")
    if alias_peer:
        session_aliases.attach(channel=channel, account_id="acct1", peer_kind="direct",
                               peer_id=alias_peer, agent_id="main", session_id=sid)


def _run_turn(sid: str, source: str, **extra):
    from openprogram.runtime.agent.turn_request import TurnRequest

    fake_stream = make_delta_stream(REPLY_PARTS)
    orig_run = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, **_):
        return orig_run(req=req, history=history, on_event=on_event,
                        cancel_event=cancel_event, stream_fn=fake_stream)

    with patch.object(D, "_run_loop_blocking", _wrapped):
        result = D.process_user_turn(TurnRequest(
            session_id=sid, user_text="what is new?", agent_id="main",
            source=source, **extra,
        ))
    # The end of a delivery runs on its recipient's background lane.
    assert B.wait_for_deliveries(10)
    return result


def _patched_loop(parts=REPLY_PARTS):
    fake_stream = make_delta_stream(parts)
    orig_run = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, **_):
        return orig_run(req=req, history=history, on_event=on_event,
                        cancel_event=cancel_event, stream_fn=fake_stream)

    return patch.object(D, "_run_loop_blocking", _wrapped)


def test_an_app_turn_in_a_bound_chat_sends_the_reply_once(world):
    db, posted, frames, _ = world
    _bound_chat(db, "local_bound", alias_peer="peer-9")
    result = _run_turn("local_bound", "web")
    assert result.final_text == REPLY
    # The reply alone, in the inbound path's paragraphs, to the alias peer.
    assert posted == [("wechat", "acct1", "peer-9", FIRST.strip()),
                      ("wechat", "acct1", "peer-9", "Second part. The end.")]
    assert not any("what is new" in text for *_, text in posted)
    assert not [f for f in frames if f["data"].get("type") == "channel_delivery_failed"]


def test_a_picked_wechat_chat_without_alias_sends_to_the_account_owner(world):
    db, posted, _, _ = world
    _bound_chat(db, "local_owner")
    _run_turn("local_owner", "web")
    assert {target for _, _, target, _ in posted} == {"owner-1"}
    assert "".join(text for *_, text in posted).replace(" ", "") == REPLY.replace("\n", "").replace(" ", "")


def test_an_always_on_wake_in_a_bound_chat_sends_its_reply(world):
    """The real always-on path: an owner turn admitted by
    ``start_owner_turn`` and run through the canonical activation."""
    from openprogram.runtime.conversation.owner_turn import start_owner_turn

    db, posted, _, _ = world
    _bound_chat(db, "local_always_on", alias_peer="peer-9")
    done = threading.Event()
    with _patched_loop():
        start_owner_turn("local_always_on", "main", "Always-on check-in.",
                         name="always-on", quiet_finish=True,
                         on_done=lambda _result: done.set())
        assert done.wait(30)
    assert B.wait_for_deliveries(10)
    assert [(target, text) for _, _, target, text in posted] == [
        ("peer-9", FIRST.strip()), ("peer-9", "Second part. The end.")]


def _scheduled_spec(sid: str) -> dict:
    from openprogram.runtime.permissions.authority import local_owner_authority

    return {"prompt": "summarize", "agent_id": "main",
            "session_id": sid, "permission_mode": "ask",
            "job_authority": local_owner_authority()}


@pytest.fixture
def scheduled(monkeypatch):
    """Run a scheduled prompt job in this process, its signed spec taken
    as verified (test_cron_command pins that)."""
    from openprogram.programs.tools.jobs.cron import worker

    monkeypatch.setattr(worker, "_verify_execution_spec",
                        lambda spec: (spec, None))
    return worker


def test_a_scheduled_job_in_a_bound_chat_sends_its_reply(world, scheduled, tmp_path):
    db, posted, _, _ = world
    _bound_chat(db, "local_scheduled", alias_peer="peer-9")
    with _patched_loop():
        scheduled._run_prompt_job(_scheduled_spec("local_scheduled"), str(tmp_path / "run.log"))
    # The job's process waited for the delivery before it ended.
    assert [(target, text) for _, _, target, text in posted] == [
        ("peer-9", FIRST.strip()), ("peer-9", "Second part. The end.")]


def test_a_scheduled_jobs_failed_delivery_reaches_the_app(world, scheduled, tmp_path, monkeypatch):
    """The job runs in a child process whose bus no WebSocket listens to:
    the failure frame goes to the parent over ``notices``, and the parent
    emits it where the Server shows it."""
    import queue

    from openprogram.runtime import events

    db, posted, frames, result = world
    result["value"] = SendResult.fail("auth", "session expired")
    _bound_chat(db, "local_scheduled_fail", alias_peer="peer-9")
    notices: "queue.Queue[dict]" = queue.Queue()
    # In the child, emit_ws_frame is the bus's own.
    child_frames: list[dict] = []
    monkeypatch.setattr(events, "emit_ws_frame", events.bus.emit_ws_frame)
    with _patched_loop():
        scheduled._run_prompt_job(_scheduled_spec("local_scheduled_fail"),
                                  str(tmp_path / "run.log"), notices=notices)
    assert posted
    while not notices.empty():
        child_frames.append(notices.get_nowait())
    assert len(child_frames) == 1
    assert child_frames[0]["data"]["error_kind"] == "auth"

    # The parent re-emits what the child handed over.
    monkeypatch.setattr(events, "emit_ws_frame", frames.append)
    for frame in child_frames:
        notices.put(frame)
    scheduled._relay_delivery_notices(SimpleNamespace(is_alive=lambda: False), notices).join(5)
    notice = [f["data"] for f in frames if f["data"].get("type") == "channel_delivery_failed"]
    assert len(notice) == 1 and notice[0]["session_id"] == "local_scheduled_fail"
    assert "not delivered to WeChat" in notice[0]["content"]


def test_an_inbound_channel_turn_is_not_sent_twice(world, stub_routing, monkeypatch):
    db, posted, _, _ = world
    from openprogram.runtime.agent_registry import session_aliases

    _bound_chat(db, "local_inbound", alias_peer="u-1")
    monkeypatch.setattr(session_aliases, "lookup",
                        lambda channel, account_id, peer: ("main", "local_inbound"))
    with _patched_loop():
        reply = C.dispatch_inbound(
            channel="wechat", account_id="acct1", peer_kind="direct",
            peer_id="u-1", user_text="hi", block_stream=True, speaker_id="u-1",
        )
    assert reply is None
    assert B.wait_for_deliveries(10)
    assert [text for *_, text in posted] == [FIRST.strip(), "Second part. The end."]


def test_a_check_in_report_reaches_the_bound_chats_peer(world, monkeypatch):
    from openprogram.runtime.conversation.always_on import delivery, runtime

    db, posted, _, _ = world
    _bound_chat(db, "local_report", alias_peer="peer-5")
    monkeypatch.setattr(runtime, "_db", lambda: db)
    monkeypatch.setattr(runtime, "_spawn", lambda target, name: None)
    monkeypatch.setattr(runtime, "_broadcast", lambda frame: None)
    monkeypatch.setattr(runtime, "_agent", lambda agent_id: SimpleNamespace(
        id="main", name="Scout", always_on={"enabled": True}))
    monkeypatch.setattr(runtime, "agent_for_session", lambda session_id: "main")
    threads = []
    original = delivery.deliver
    monkeypatch.setattr(delivery, "deliver", lambda *a, **k: threads.append(original(*a, **k)))

    # The check-in's own reply stays in the App; only its report goes out.
    _run_turn("local_report", "always_on", background=True, advance_head=False)
    assert posted == []
    runtime.report("local_report", "CI is red", "main fails")
    threads[0].join(5)
    assert [(target, text) for _, _, target, text in posted] == [
        ("peer-5", "**Scout · CI is red**\n\nmain fails")]


def test_an_unbound_chat_sends_nothing(world):
    db, posted, frames, _ = world
    db.create_session("local_plain", "main")
    _run_turn("local_plain", "web")
    assert posted == []
    assert not [f for f in frames if f["data"].get("type") == "channel_delivery_failed"]


def test_a_bound_chat_with_no_known_peer_sends_nothing(world):
    db, posted, _, _ = world
    _bound_chat(db, "local_telegram", channel="telegram")
    _run_turn("local_telegram", "web")
    assert posted == []


def test_a_failed_delivery_is_shown_in_the_app(world, caplog):
    db, posted, frames, result = world
    result["value"] = SendResult.fail("auth", "session expired")
    _bound_chat(db, "local_fail", alias_peer="peer-9")
    with caplog.at_level("WARNING"):
        out = _run_turn("local_fail", "web")
        assert B.wait_for_deliveries(10)
    assert out.final_text == REPLY  # the turn itself is unaffected
    assert posted
    notices = [f["data"] for f in frames
               if f["type"] == "chat_response" and f["data"].get("type") == "channel_delivery_failed"]
    assert len(notices) == 1
    notice = notices[0]
    assert notice["session_id"] == "local_fail"
    assert notice["channel"] == "wechat" and notice["channel_label"] == "WeChat"
    assert notice["error_kind"] == "auth"
    assert "not delivered to WeChat" in notice["content"]
    assert "msg_id" not in notice
    assert "not delivered" in caplog.text


def test_a_scheduled_run_registers_the_parents_channel_link(monkeypatch, tmp_path):
    from openprogram.programs.tools.jobs.cron import worker
    from openprogram.runtime.conversation import process_runner

    imported: list[list[str]] = []
    monkeypatch.setattr(process_runner, "_import_link_registrations", imported.append)
    spec = {"policy_hash": "x"}  # refused below before any model call
    worker._run_prompt_job(spec, str(tmp_path / "run.log"),
                           link_modules=["openprogram.interfaces.channels.link_registration"])
    assert imported == [["openprogram.interfaces.channels.link_registration"]]


def test_a_slow_channel_does_not_hold_the_turn(world):
    """The App gets the result, and the chat its next turn, while the
    rest of the reply is still on its way."""
    db, posted, _, result = world
    release = threading.Event()
    original = result["value"]

    class _Slow:
        def __getattr__(self, name):
            release.wait(10)
            return getattr(original, name)

    result["value"] = _Slow()
    _bound_chat(db, "local_slow", alias_peer="peer-9")
    from openprogram.runtime.agent.turn_request import TurnRequest

    started = time.monotonic()
    with _patched_loop():
        out = D.process_user_turn(TurnRequest(
            session_id="local_slow", user_text="hi", agent_id="main", source="web"))
    assert out.final_text == REPLY
    assert time.monotonic() - started < 5
    release.set()
    assert B.wait_for_deliveries(10)
    assert [text for *_, text in posted] == [FIRST.strip(), "Second part. The end."]


# -- a turn that pauses and resumes -----------------------------------------

SID = "local_paused"
ASSISTANT = "u1_reply"


def _text(text: str) -> dict:
    return {"type": "chat_response", "data": {
        "type": "stream_event", "session_id": SID, "msg_id": ASSISTANT,
        "event": {"type": "text", "text": text}}}


def _tool_use() -> dict:
    return {"type": "chat_response", "data": {
        "type": "stream_event", "session_id": SID, "msg_id": ASSISTANT,
        "event": {"type": "tool_use", "tool": "bash"}}}


def _attempt(*, resumed: bool, stream: list[dict]) -> B.BoundReply:
    reply = B.BoundReply(SimpleNamespace(session_id=SID, user_msg_id="u1"),
                         B.ReplyTarget("wechat", "acct1", "peer-9"), ASSISTANT,
                         resumed=resumed)
    on_event = reply.tap(lambda _env: None)
    for env in stream:
        on_event(env)
    return reply


def _restart() -> dict:
    return {"type": "chat_response", "data": {
        "type": "stream_event", "session_id": SID, "msg_id": ASSISTANT,
        "event": {"type": "retry", "attempt": 2, "reason": "transport", "restart": True}}}


def _sent(reply: B.BoundReply, final_text: str, posted) -> list[str]:
    reply.finish(final_text)
    assert B.wait_for_deliveries(10)
    return [text for _, _, _, text in posted]


def test_a_response_sent_again_does_not_repeat_the_paragraph_already_sent(world):
    """The failed response had one paragraph out and half of the next; the
    request goes out again and says the same thing from the start."""
    _, posted, _, _ = world
    reply = _attempt(resumed=False, stream=[
        _text(FIRST), _text("\n\nSecond par"), _restart(),
        *[_text(part) for part in REPLY_PARTS],
    ])
    assert _sent(reply, REPLY, posted) == [FIRST.strip(), "Second part. The end."]


def test_a_response_sent_again_with_other_words_is_sent_whole(world):
    """What went out stays; the unsent half paragraph of the failed response
    never goes out, and the new response does."""
    _, posted, _, _ = world
    other = "A different answer this time."
    reply = _attempt(resumed=False, stream=[
        _text(FIRST), _text("\n\nSecond par"), _restart(), _text(other),
    ])
    assert _sent(reply, other, posted) == [FIRST.strip(), other]


def test_a_response_sent_again_before_anything_went_out_leaves_no_trace(world):
    _, posted, _, _ = world
    reply = _attempt(resumed=False, stream=[
        _text("Half a sentence that"), _restart(), _text("The whole reply."),
    ])
    assert _sent(reply, "The whole reply.", posted) == ["The whole reply."]


def test_a_restart_voids_only_the_response_being_sent_again(world):
    """Text before a tool call belongs to a response that ended."""
    _, posted, _, _ = world
    reply = _attempt(resumed=False, stream=[
        _text("I will look."), _tool_use(), _text(FIRST), _text("\n\nSecond par"),
        _restart(), *[_text(part) for part in REPLY_PARTS],
    ])
    assert _sent(reply, "I will look." + REPLY, posted) == [
        "I will look.", FIRST.strip(), "Second part. The end."]


def test_an_app_turn_paused_twice_sends_each_part_once(world):
    """Each attempt sends what it streamed; the final ``final_text``
    repeats the earlier attempts' text, which never goes out again."""
    _, posted, _, _ = world
    first = _attempt(resumed=False, stream=[
        _text("Checking files."), _tool_use(), _text("Now deleting X."), _tool_use()])
    first.pause()
    second = _attempt(resumed=True, stream=[_text("Deleted X. Deleting Y."), _tool_use()])
    second.pause()
    third = _attempt(resumed=True, stream=[_text("Done.")])
    third.finish("Now deleting X.Deleted X. Deleting Y.Done.")
    assert B.wait_for_deliveries(10)
    assert [text for *_, text in posted] == [
        "Checking files.", "Now deleting X.", "Deleted X. Deleting Y.", "Done."]


def test_a_resumed_channel_turn_does_not_resend_what_the_inbound_path_sent(world):
    """The inbound streamer sent the text before the approval; the
    resumed attempt (attached here, ``resumed=True``) sends only the rest."""
    _, posted, _, _ = world
    assert B.delivered_here("wechat", "wechat", resumed=True)
    assert not B.delivered_here("wechat", "wechat")
    resumed = _attempt(resumed=True, stream=[_text("Done.")])
    resumed.finish("I'll delete the temp files.Done.")
    assert B.wait_for_deliveries(10)
    assert [text for *_, text in posted] == ["Done."]


def test_a_resumed_attempt_sends_the_turns_files(world, monkeypatch):
    from openprogram.state.attachments import format_marker

    _, posted, _, _ = world
    marker = format_marker("report.pdf", "/tmp/report.pdf", 2048)
    uploaded: list[str] = []

    def _deliver(channel, account_id, peer_id, text, *, attached_files, thread_id=None):
        assert attached_files == ["/tmp/report.pdf"]
        uploaded.extend(m for m in [marker] if m in text)
        return text.replace(marker, "").strip()

    monkeypatch.setattr(C, "_deliver_outbound_files", _deliver)
    resumed = _attempt(resumed=True, stream=[_text("Here it is.")])
    resumed.finish("Earlier text.Here it is.\n\n" + marker,
                   attached_files=["/tmp/report.pdf"])
    assert B.wait_for_deliveries(10)
    assert uploaded == [marker]
    assert [text for *_, text in posted] == ["Here it is."]


def test_a_reply_that_cannot_open_is_reported_once(world, monkeypatch, caplog):
    from openprogram.runtime import channel_link

    _, posted, frames, _ = world
    calls: list[int] = []

    def _unavailable(*_args, **_options):
        calls.append(1)
        raise channel_link.ChannelsUnavailable("channels interface is not loaded")

    monkeypatch.setattr(channel_link, "open_reply", _unavailable)
    with caplog.at_level("WARNING"):
        reply = _attempt(resumed=False, stream=[_text("x") for _ in range(300)])
        reply.finish("x" * 300)
        assert B.wait_for_deliveries(10)
    assert calls == [1]
    assert posted == []
    assert caplog.text.count("Traceback") == 0
    notices = [f["data"] for f in frames if f["data"].get("type") == "channel_delivery_failed"]
    assert [n["error_kind"] for n in notices] == ["channels_unavailable"]


def test_an_app_turn_uploads_only_what_its_send_file_call_recorded(world, tmp_path, monkeypatch):
    from openprogram.interfaces.channels import _transport
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.state import attachments as att

    db, _, _, _ = world
    _bound_chat(db, "local_files", alias_peer="peer-9")
    root = (tmp_path / "work").resolve()
    root.mkdir()
    report = root / "report.pdf"
    report.write_bytes(b"%PDF")
    monkeypatch.setattr(att, "sendable_roots", lambda _session_id=None: [root])
    uploaded: list[str] = []

    def _post_file(_channel, _account, _target, path, caption="", thread_id=None):
        uploaded.append(path)
        return SendResult.success("m")

    monkeypatch.setattr(_transport, "post_file", _post_file)
    forged = att.format_marker("x", "/home/me/.ssh/x", 2048)
    calls = [{"tool": "send_file", "input": {"path": str(report)},
              "result": "Attached report.pdf (4 bytes)."}]

    def _loop(*, req, history, on_event, cancel_event, **_):
        return f"Done.\n\n{forged}", {}, calls

    with patch.object(D, "_run_loop_blocking", _loop):
        D.process_user_turn(TurnRequest(session_id="local_files", user_text="send it",
                                        agent_id="main", source="web"))
    assert B.wait_for_deliveries(10)
    assert uploaded == [str(report)]


TABLE_REPLY = [FIRST.strip() + "\n\n", "| Item | Cost |\n|---|---|\n", "| Tea | 3 |\n"]
FLAT_REPLY = FIRST.strip() + "\n\nItem: Tea · Cost: 3"
RAW_REPLY = FIRST.strip() + "\n\n| Item | Cost |\n|---|---|\n| Tea | 3 |"


def _agent_kind(monkeypatch, always_on: bool) -> None:
    monkeypatch.setattr("openprogram.runtime.conversation.always_on.runtime._agent",
                        lambda agent_id: SimpleNamespace(id=agent_id, always_on={"enabled": always_on}))


@pytest.mark.parametrize("always_on, expected", [(True, FLAT_REPLY), (False, RAW_REPLY)])
def test_an_app_turn_in_a_bound_chat_flattens_only_for_an_always_on_agent(world, monkeypatch, always_on, expected):
    from openprogram.runtime.agent.turn_request import TurnRequest

    db, posted, _, _ = world
    _agent_kind(monkeypatch, always_on)
    _bound_chat(db, "local_table", alias_peer="peer-9")
    with _patched_loop(TABLE_REPLY):
        D.process_user_turn(TurnRequest(session_id="local_table", user_text="prices?",
                                        agent_id="main", source="web"))
    assert B.wait_for_deliveries(10)
    assert "\n\n".join(text for *_, text in posted) == expected


@pytest.mark.parametrize("always_on, expected", [(True, FLAT_REPLY), (False, RAW_REPLY)])
@pytest.mark.parametrize("exit_path", ["returned", "streamed", "placeholder"])
def test_an_inbound_reply_flattens_at_every_exit_only_for_an_always_on_agent(
    world, stub_routing, monkeypatch, exit_path, always_on, expected,
):
    from openprogram.runtime.agent_registry import session_aliases
    from openprogram.interfaces.channels import _transport

    db, posted, _, result = world
    result["value"] = SendResult.success("m1")
    edited: list[str] = []
    monkeypatch.setattr(_transport, "patch_message",
                        lambda _platform, _account, _target, _message, text:
                        edited.append(text) or SendResult.success("m1"))
    _agent_kind(monkeypatch, always_on)
    _bound_chat(db, "local_exits", "telegram", alias_peer="u-1")
    monkeypatch.setattr(session_aliases, "lookup", lambda channel, account_id, peer: ("main", "local_exits"))
    with _patched_loop(TABLE_REPLY):
        reply = C.dispatch_inbound(
            channel="telegram", account_id="acct1", peer_kind="direct", peer_id="u-1",
            user_text="prices?", speaker_id="u-1", block_stream=exit_path == "streamed",
            progress_stream=exit_path == "placeholder",
        )
    assert B.wait_for_deliveries(10)
    if exit_path == "returned":
        assert reply == expected and posted == []
    elif exit_path == "streamed":
        assert reply is None and "\n\n".join(text for *_, text in posted) == expected
    else:
        assert reply is None and edited[-1] == expected


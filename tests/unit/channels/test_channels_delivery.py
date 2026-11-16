"""Session generations behind /new and idle reset, and paragraph
streaming for platforms that cannot edit a sent message (WeChat).
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from openprogram.interfaces.channels.inbound import _session_routing as R
from openprogram.interfaces.channels.delivery import _transport
from openprogram.interfaces.channels.inbound._conversation import _BlockStreamer
from openprogram.interfaces.channels.delivery._transport import SendResult, _classify_network_error
from openprogram.runtime import channel_link
from openprogram.runtime.conversation.always_on import delivery, runtime, store, ticker
from openprogram.state.store.session.session_store import SessionStore


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)


def _agent(**kw):
    base = dict(id="main", session_daily_reset="", session_idle_minutes=0)
    base.update(kw)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Session generations
# ---------------------------------------------------------------------------

def test_new_moves_later_messages_to_a_new_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(R, "_last_activity", lambda key: 0.0)
    agent = _agent()
    assert R.apply_reset_policy(agent, "acct_direct_7") == "acct_direct_7"
    assert R.start_new_generation(agent, "acct_direct_7") == 1
    assert R.apply_reset_policy(agent, "acct_direct_7") == "acct_direct_7_n1"
    assert R.apply_reset_policy(agent, "acct_direct_7") == "acct_direct_7_n1"
    # Other peers and other agents keep their session.
    assert R.apply_reset_policy(agent, "acct_direct_8") == "acct_direct_8"
    assert R.apply_reset_policy(_agent(id="other"), "acct_direct_7") == "acct_direct_7"


def test_idle_reset_cuts_once_and_then_stays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    activity = {"acct_direct_7": now - 3600}
    monkeypatch.setattr(R, "_last_activity", lambda key: activity.get(key, 0.0))
    agent = _agent(session_idle_minutes=30)

    first = R.apply_reset_policy(agent, "acct_direct_7")
    assert first == "acct_direct_7_n1"
    activity[first] = now  # the new session gets a turn
    assert R.apply_reset_policy(agent, "acct_direct_7") == first


def test_daily_suffix_follows_the_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(R, "_last_activity", lambda key: 0.0)
    agent = _agent(session_daily_reset="04:00")
    R.start_new_generation(agent, "k")
    key = R.apply_reset_policy(agent, "k")
    assert key.startswith("k_n1_") and len(key) == len("k_n1_20260101")


@pytest.mark.parametrize("scope", ["main", "per-peer", "per-account-channel-peer"])
def test_two_agents_get_different_session_keys(scope: str) -> None:
    peer = {"kind": "direct", "id": "42"}
    k_main = R.session_key_for_agent(
        _agent(session_scope=scope), "telegram", "a1", peer)
    k_helper = R.session_key_for_agent(
        _agent(id="helper", session_scope=scope), "telegram", "a1", peer)
    assert k_main != k_helper
    assert k_helper.startswith("helper_")


def test_default_agent_keys_are_unchanged() -> None:
    peer = {"kind": "direct", "id": "42"}
    group = {"kind": "group", "id": "-100"}

    def key(scope, p=peer):
        return R.session_key_for_agent(
            _agent(session_scope=scope), "telegram", "a1", p)

    assert key("main") == "main"
    assert key("per-peer") == "peer_42"
    assert key("per-channel-peer") == "telegram_direct_42"
    assert key("per-account-channel-peer") == "a1_direct_42"
    assert key("main", group) == "telegram_a1_group_-100"


# ---------------------------------------------------------------------------
# Paragraph streaming
# ---------------------------------------------------------------------------

@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    out: list[str] = []
    monkeypatch.setattr(
        "openprogram.interfaces.channels.delivery._transport.post_message",
        lambda platform, account_id, target, text: out.append(text)
        or SendResult.success(""))
    return out


def test_paragraphs_leave_once_long_enough(posted) -> None:
    first = "a" * 250
    streamer = _BlockStreamer("wechat", "acct", "u1")
    for piece in (first[:100], first[100:], "\n\nshort", " tail"):
        streamer.feed(piece)
    streamer.close()
    assert posted == [first]
    assert streamer.unsent_part(first + "\n\nshort tail") == "short tail"


def test_text_before_a_tool_call_is_sent_first(posted) -> None:
    streamer = _BlockStreamer("wechat", "acct", "u1")
    streamer.feed("我查一下。")
    streamer.flush()
    streamer.feed("查到了：三条。")
    streamer.close()
    assert posted == ["我查一下。"]
    reply = "我查一下。查到了：三条。\n\n[attachment: a.txt @ /tmp/a.txt]"
    assert streamer.unsent_part(reply) == "查到了：三条。\n\n[attachment: a.txt @ /tmp/a.txt]"


def test_an_open_code_block_is_never_split(posted) -> None:
    streamer = _BlockStreamer("wechat", "acct", "u1")
    streamer.feed("```python\n" + "x = 1\n" * 60 + "\n\nmore code")
    streamer.flush()
    streamer.close()
    assert posted == []
    assert not streamer.sent_any


def test_nothing_streamed_leaves_the_whole_reply(posted) -> None:
    streamer = _BlockStreamer("wechat", "acct", "u1")
    streamer.close()
    assert streamer.unsent_part("whole reply") == "whole reply"
    streamer.send("late")
    assert posted == ["late"]


def test_a_deleted_conversation_moves_the_peer_to_a_new_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The peer's conversation was deleted in the UI: its id keeps a delete
    # mark and can never be written again, so the next message starts a
    # new conversation instead of failing.
    monkeypatch.setattr(R, "_last_activity", lambda key: 0.0)
    deleted = {"acct_direct_7"}
    monkeypatch.setattr(R, "_session_deleted", lambda key: key in deleted)
    agent = _agent()
    assert R.apply_reset_policy(agent, "acct_direct_7") == "acct_direct_7_n1"
    assert R.apply_reset_policy(agent, "acct_direct_7") == "acct_direct_7_n1"
    deleted.add("acct_direct_7_n1")
    assert R.apply_reset_policy(agent, "acct_direct_7") == "acct_direct_7_n2"
    assert R.apply_reset_policy(agent, "acct_direct_8") == "acct_direct_8"


def test_session_deleted_reads_the_store_delete_mark(tmp_path) -> None:
    from openprogram.runtime.context import session_db
    from openprogram.state.store.session.files.placement import record_delete_intent

    root = tmp_path / "projects"
    root.mkdir()

    class _Store:
        root_path = root

    import unittest.mock as mock
    with mock.patch.object(session_db, "default_db", lambda: _Store()):
        assert R._session_deleted("acct_direct_7") is False
        record_delete_intent(root, "acct_direct_7", {"session_id": "acct_direct_7"})
        assert R._session_deleted("acct_direct_7") is True


# ---------------------------------------------------------------------------
# An always-on Agent's tables
# ---------------------------------------------------------------------------

def _big_table(rows: int) -> str:
    return "| Name | Note |\n|---|---|\n" + "".join(f"| n{i} | {'x' * 40} |\n" for i in range(rows))


def test_a_table_over_the_limit_waits_and_keeps_its_headers(posted) -> None:
    table = _big_table(120)
    assert len(table) > 2048
    streamer = _BlockStreamer("wechat", "acct", "u1", flatten=True)
    for start in range(0, len(table), 37):
        streamer.feed(table[start:start + 37])
    assert not streamer.sent_any
    streamer.feed("\n")
    streamer.feed("after the table " * 20 + "\n\nend")
    streamer.close()
    assert len(posted) == 2 and posted[1].startswith("after the table")
    lines = posted[0].splitlines()
    assert "|" not in posted[0] and lines[0] == f"Name: n0 · Note: {'x' * 40}"
    assert sum(line.startswith("Name: n") for line in lines) == 120
    assert streamer.sent_upto < len(streamer.streamed)
    assert streamer.unsent_part(streamer.streamed) == "end"


def test_text_before_a_table_being_written_leaves_without_it(posted) -> None:
    before = ("word " * 30 + "\n") * 16
    streamer = _BlockStreamer("wechat", "acct", "u1", flatten=True)
    streamer.feed(before + "| a | b |\n")
    streamer.close()
    assert posted and "|" not in "".join(posted)
    assert streamer.unsent_part(streamer.streamed).endswith("| a | b |")


def test_a_tool_call_sends_a_finished_table_flattened(posted) -> None:
    streamer = _BlockStreamer("wechat", "acct", "u1", flatten=True)
    streamer.feed("| a | b |\n|---|---|\n| 1 | 2 |\n")
    streamer.flush()
    streamer.close()
    assert posted == ["a: 1 · b: 2"]


def test_other_agents_keep_their_tables_as_written(posted) -> None:
    table = _big_table(120)
    streamer = _BlockStreamer("wechat", "acct", "u1")
    streamer.feed(table)
    streamer.close()
    assert posted and "|" in posted[0]


def test_a_code_block_cut_by_the_limit_keeps_its_table_as_written(posted) -> None:
    code = "```\n" + "line of code\n" * 200 + "| a | b |\n|---|---|\n| 1 | 2 |\n```\n"
    text = "intro\n" + code + "after\n"
    assert len(code) > 2048
    streamer = _BlockStreamer("wechat", "acct", "u1", flatten=True)
    for start in range(0, len(text), 37):
        streamer.feed(text[start:start + 37])
    streamer.close()
    streamer.send(streamer.unsent_part(text))
    assert "| a | b |\n|---|---|\n| 1 | 2 |" in "\n".join(posted)
    assert not any(part.startswith("line of code") for part in posted)


def test_a_paragraph_break_in_a_tilde_fence_does_not_cut_it(posted) -> None:
    block = "~~~\n" + "x" * 250 + "\n\n| a | b |\n|---|---|\n"
    streamer = _BlockStreamer("wechat", "acct", "u1", flatten=True)
    streamer.feed(block)
    assert not streamer.sent_any
    streamer.feed("~~~\n\nnext")
    streamer.close()
    assert posted == [(block + "~~~").strip()]


# ---------------------------------------------------------------------------
# Report recovery
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("error, connect_failed", [
    (httpx.ConnectError("refused"), True),
    (httpx.ConnectTimeout("slow"), True),
    (httpx.ProxyError("proxy down"), True),
    (httpx.ReadTimeout("sent, no answer"), False),
    (httpx.RemoteProtocolError("dropped"), False),
])
def test_only_a_connection_never_made_counts_as_connect_failed(error, connect_failed) -> None:
    result = _classify_network_error(error)
    assert result.error_kind == "network" and result.connect_failed is connect_failed


class _Reports:
    """A conversation's report file and everything a recovery reaches for."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        self.tmp_path = tmp_path
        self.sent: list[str] = []
        self.outcomes: list[SendResult] = []
        self.sleeps: list[float] = []
        self.posted: list[tuple[str, dict]] = []
        self.index: dict[str, dict] = {}
        self.busy = False
        self.waits: list[float] = []
        self.threads: list[threading.Thread] = []
        monkeypatch.setattr(store, "_reports_path", lambda sid: tmp_path / f"{sid}.jsonl")
        monkeypatch.setattr(channel_link, "send_message", self.send)
        monkeypatch.setattr(delivery.time, "sleep", self.sleeps.append)
        monkeypatch.setattr(runtime, "_broadcast", lambda frame: None)
        monkeypatch.setattr(runtime, "_agent", lambda agent_id: SimpleNamespace(id=agent_id, name="Scout"))
        monkeypatch.setattr(runtime, "_spawn", lambda target, name: target())
        monkeypatch.setattr(runtime, "_db", lambda: self)
        original_post = runtime._post_when_free

        def post_when_free(session_id, agent_id, row, *, wait_s=runtime.REPORT_POST_WAIT_S, poll_s=0):
            self.waits.append(wait_s)
            original_post(session_id, agent_id, row, wait_s=0, poll_s=0)
        monkeypatch.setattr(runtime, "_post_when_free", post_when_free)
        original_deliver = delivery.deliver

        def deliver(*args, **kwargs):
            thread = original_deliver(*args, **kwargs)
            if thread is not None:
                self.threads.append(thread)
                thread.join(5)
            return thread
        monkeypatch.setattr(delivery, "deliver", deliver)

    def send(self, channel, account_id, peer_id, text):
        self.sent.append(peer_id)
        return self.outcomes.pop(0) if self.outcomes else SendResult.success()

    def list_sessions(self, **_):
        return [{"id": "chat", "agent_id": "scout"}]

    def read_index(self, session_id, reader):
        return reader(self.index)

    def append_after_head(self, session_id, message):
        self.posted.append((session_id, message))
        self.index[message["id"]] = message

    def commit_turn(self, session_id, message):
        return "commit"

    def add(self, *, age: float, status: str = "sending", title: str = "Found") -> dict:
        deliveries = [{"channel": "telegram", "account_id": "a", "peer_id": "1", "status": status}]
        row = store.add_report("chat", title=title, body="body", deliveries=deliveries)
        return store.update_report("chat", row["id"], at=time.time() - age)

    def deliveries(self) -> list[dict]:
        return store.load_reports("chat")[0]["deliveries"]


@pytest.fixture
def reports(monkeypatch: pytest.MonkeyPatch, tmp_path) -> _Reports:
    return _Reports(monkeypatch, tmp_path)


def _connect_failure() -> SendResult:
    return _classify_network_error(httpx.ConnectError("refused"))


def test_a_connect_failure_is_tried_again_after_one_and_ten_minutes(reports: _Reports) -> None:
    reports.outcomes = [_connect_failure(), _connect_failure()]
    row = reports.add(age=0)
    delivery.deliver("chat", "Scout", row).join(5)
    assert len(reports.sent) == 3 and reports.sleeps == [0.0, 60.0, 600.0]
    assert reports.deliveries()[0]["status"] == "sent"


def test_a_connect_failure_gives_up_after_the_last_try(reports: _Reports) -> None:
    reports.outcomes = [_connect_failure(), _connect_failure(), _connect_failure()]
    delivery.deliver("chat", "Scout", reports.add(age=0)).join(5)
    assert len(reports.sent) == 3
    assert reports.deliveries()[0]["status"] == "failed" and reports.deliveries()[0]["error"] == "network"


def test_a_non_connect_error_is_not_tried_again(reports: _Reports) -> None:
    reports.outcomes = [_classify_network_error(httpx.ReadTimeout("no answer"))]
    delivery.deliver("chat", "Scout", reports.add(age=0)).join(5)
    assert len(reports.sent) == 1 and reports.deliveries()[0]["status"] == "failed"


def test_a_retry_saves_the_target_that_got_through_and_leaves_it_alone(reports: _Reports) -> None:
    row = reports.add(age=0)
    row = store.update_report("chat", row["id"], deliveries=[
        *row["deliveries"], {"channel": "telegram", "account_id": "a", "peer_id": "2", "status": "sending"}])
    reports.outcomes = [SendResult.success(), _connect_failure()]
    delivery.deliver("chat", "Scout", row).join(5)
    assert reports.sent == ["1", "2", "2"]
    assert [item["status"] for item in reports.deliveries()] == ["sent", "sent"]


def test_recovery_resends_a_recent_row_and_fails_an_older_one(reports: _Reports) -> None:
    old = reports.add(age=2 * 3600, title="Old")
    runtime.recover_reports(time.time())
    assert reports.sent == [] and reports.deliveries()[0]["status"] == "failed"
    assert reports.deliveries()[0]["error"] == "interrupted"
    assert reports.deliveries()[0]["detail"] == "cut off by a restart"
    assert reports.posted == [] and old["id"]

    reports.add(age=60, title="New")
    runtime.recover_reports(time.time())
    assert reports.sent == ["1"] and reports.deliveries()[0]["status"] == "sent"


def test_recovery_is_idempotent(reports: _Reports) -> None:
    reports.add(age=2 * 3600, title="Old")
    reports.add(age=60, title="New")
    for _ in range(2):
        runtime.recover_reports(time.time())
    assert reports.sent == ["1"] and len(reports.posted) == 1


def test_recovery_posts_oldest_first_within_what_is_left_of_the_hour(reports: _Reports) -> None:
    first = reports.add(age=1800, status="sent", title="First")
    second = reports.add(age=60, status="sent", title="Second")
    reports.add(age=2 * 3600, status="sent", title="Too old")
    posted = reports.add(age=120, status="sent", title="Posted")
    reports.index[store.report_message_id(posted)] = {}
    runtime.recover_reports(time.time())
    assert [message["always_on_report"] for _, message in reports.posted] == [first["id"], second["id"]]
    assert reports.waits[0] == pytest.approx(1800, abs=5) and reports.waits[1] == pytest.approx(3540, abs=5)


def test_recovery_leaves_reports_of_this_worker_alone(reports: _Reports) -> None:
    started_at = time.time()
    reports.add(age=-1)
    runtime.recover_reports(started_at)
    assert reports.sent == [] and reports.posted == []
    assert reports.deliveries()[0]["status"] == "sending"


def test_appending_after_head_follows_a_message_that_landed_just_before(tmp_path) -> None:
    db = SessionStore(tmp_path / "sessions")
    db.create_session("chat", "main")
    db.append_message("chat", {"id": "m0", "role": "user", "content": "hi", "timestamp": 1.0})
    take_lock = db._head_file_lock
    landed = []

    def other_writer_first(session_log):
        if not landed:
            landed.append(True)
            db.append_message("chat", {"id": "m1", "role": "assistant", "content": "yes",
                                       "predecessor": "m0", "timestamp": 2.0})
        return take_lock(session_log)
    db._head_file_lock = other_writer_first

    db.append_after_head("chat", {"id": "r1", "role": "assistant", "content": "report", "timestamp": 3.0})
    db.append_after_head("chat", {"id": "r1", "role": "assistant", "content": "report", "timestamp": 3.0})

    assert [row["id"] for row in db.get_branch("chat")] == ["m0", "m1", "r1"]
    assert db.get_session("chat")["head_id"] == "r1"


def test_the_ticker_recovers_reports_once_per_worker_start(monkeypatch: pytest.MonkeyPatch) -> None:
    recovered, ticks = [], []
    monkeypatch.setattr("openprogram.runtime.conversation.always_on.handoff_migration.migrate_in_worker",
                        lambda: None)
    monkeypatch.setattr(runtime, "recover_reports", recovered.append)

    def tick() -> None:
        ticks.append(True)
        if len(ticks) == 3:
            stop.set()
    monkeypatch.setattr(ticker, "tick", tick)
    before = time.time()
    stop, thread = ticker.start_in_worker(interval=0.001)
    thread.join(5)
    assert len(ticks) == 3 and len(recovered) == 1 and recovered[0] >= before


def test_a_connection_lost_partway_through_a_long_message_is_not_sent_again(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_transport.time, "sleep", lambda _s: None)
    outcomes = [SendResult.success("1"), *[_connect_failure()] * _transport._CONNECT_RETRY_ATTEMPTS]
    monkeypatch.setitem(_transport._POSTERS, "telegram", lambda *_args, **_kwargs: outcomes.pop(0))
    result = _transport.post_message("telegram", "a", "1", "x" * (_transport.MAX_CHARS["telegram"] + 10))
    assert not outcomes and not result.ok and result.error_kind == "network"
    assert result.connect_failed is False


def test_a_connection_refused_on_the_first_piece_still_counts_as_connect_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_transport.time, "sleep", lambda _s: None)
    monkeypatch.setitem(_transport._POSTERS, "telegram", lambda *_args, **_kwargs: _connect_failure())
    assert _transport.post_message("telegram", "a", "1", "short").connect_failed is True


def test_appending_after_head_sees_what_another_process_wrote_while_it_waited(tmp_path) -> None:
    root = tmp_path / "sessions"
    db, other_process = SessionStore(root), SessionStore(root)
    db.create_session("chat", "main")
    db.append_message("chat", {"id": "m0", "role": "user", "content": "hi", "timestamp": 1.0})
    other_process.get_session("chat")
    take_lock = db._head_file_lock
    landed = []

    def other_process_first(session_log):
        if not landed:
            landed.append(True)
            other_process.append_message("chat", {"id": "m1", "role": "assistant", "content": "yes",
                                                  "predecessor": "m0", "timestamp": 2.0})
        return take_lock(session_log)
    db._head_file_lock = other_process_first

    db.append_after_head("chat", {"id": "r1", "role": "assistant", "content": "report", "timestamp": 3.0})

    assert [row["id"] for row in db.get_branch("chat")] == ["m0", "m1", "r1"]


def test_recovery_posts_into_a_real_conversation_once(reports: _Reports, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    db = SessionStore(tmp_path / "sessions")
    db.create_session("chat", "scout")
    db.append_message("chat", {"id": "m0", "role": "user", "content": "hi", "timestamp": 1.0})
    monkeypatch.setattr(runtime, "_db", lambda: db)
    monkeypatch.setattr(runtime, "_turn_running", lambda session_id: False)
    row = reports.add(age=60, status="sent")
    for _ in range(2):
        runtime.recover_reports(time.time())
    assert [item["id"] for item in db.get_branch("chat")] == ["m0", store.report_message_id(row)]


def test_recovery_does_not_load_a_conversation_with_only_old_reports(reports: _Reports, monkeypatch: pytest.MonkeyPatch) -> None:
    reports.add(age=2 * 3600, status="sent")
    monkeypatch.setattr(reports, "read_index", lambda *_args: pytest.fail("loaded the conversation"))
    runtime.recover_reports(time.time())

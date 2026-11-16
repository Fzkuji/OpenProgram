"""send_message busy-target inbox — queue, drain, limits.

Covers (design: agent-collaboration.md §5.4):
  * busy target → message queued (not delivered, not dropped)
  * drain at turn end → each entry delivered through run_agent_turn_async
    with the sender receipt header + caller routing
  * queued delivery inherits the spawn depth recorded at enqueue (+1)
  * per-target cap (50): oldest dropped + notice in the sender session
  * duplicate suppression: identical message from the same sender within
    the 60s window is rejected
"""
from __future__ import annotations

import json
import threading

import pytest

from openprogram.runtime.conversation.peer_agents import inbox
from openprogram.programs.tools.agents.send_message.send_message.send_message import (
    _send_message_impl,
)


@pytest.fixture
def two_sessions(tmp_path, monkeypatch):
    """Sender session p1 (bound on the ContextVars) + target session t1."""
    from openprogram.state.store.session.session_store import SessionStore
    from openprogram.runtime.context import session_db as sdb_mod

    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: s)

    s.create_session("p1", "main", title="sender")
    s.append_message("p1", {"id": "u1", "role": "user", "content": "hi",
                            "timestamp": 0, "predecessor": None})
    s.append_message("p1", {"id": "a1", "role": "assistant", "content": "ok",
                            "timestamp": 0, "predecessor": "u1"})
    s.commit_turn("p1", "init")

    s.create_session("t1", "main", title="target")
    s.append_message("t1", {"id": "u2", "role": "user", "content": "yo",
                            "timestamp": 0, "predecessor": None})
    s.append_message("t1", {"id": "b1", "role": "assistant", "content": "sup",
                            "timestamp": 0, "predecessor": "u2"})
    s.commit_turn("t1", "init")

    from openprogram.runtime.execution.run_control import binding as run_control_binding
    from openprogram.state import store as store_mod
    sid_tok = run_control_binding._current_session_id.set("p1")
    turn_tok = store_mod._current_turn_id.set("a1")
    # Every message is a tracked Job, so a queued one is admitted by the job
    # runner. Build it on the empty store and stop it with the test.
    monkeypatch.setattr("openprogram.runtime.jobs.runner.shared._broadcast", lambda *a, **k: None)
    from openprogram.runtime.jobs import runner as runner_mod
    runner = runner_mod.get_runner()
    yield s
    runner_mod.shutdown_runner()
    for thread in (runner._dispatcher_thread, runner._reconciler_thread, runner._budget_thread):
        thread.join()
    run_control_binding._current_session_id.reset(sid_tok)
    store_mod._current_turn_id.reset(turn_tok)


@pytest.fixture
def busy_target(two_sessions):
    """Mark t1 as running a turn (registered cancel token = busy)."""
    from openprogram.runtime.conversation import run_control
    ev = threading.Event()
    run_control.register_cancel_event("t1", ev)
    yield ev
    run_control.unregister_cancel_event("t1", ev)


def _capture_async(monkeypatch):
    """Patch run_agent_turn_async at its definition site (inbox and
    send_message both import it lazily from there) and capture kwargs."""
    calls: list[dict] = []

    def fake_async(**kw):
        calls.append(kw)
        return "task_fake"

    monkeypatch.setattr(
        "openprogram.runtime.conversation.peer_agents.sub_agent_run.run_agent_turn_async", fake_async)
    return calls


def test_busy_send_enqueues(two_sessions, busy_target, monkeypatch):
    calls = _capture_async(monkeypatch)
    out = _send_message_impl("hello t1", to="t1:b1")
    assert "[queued]" in out
    assert "busy" in out
    assert calls == []                      # nothing delivered yet
    assert inbox.pending_count("t1") == 1


def test_drain_delivers_then_removes(two_sessions, busy_target, monkeypatch):
    calls = _capture_async(monkeypatch)
    _send_message_impl("hello t1", to="t1:b1")
    assert inbox.pending_count("t1") == 1

    # Release the busy state before draining (turn ended).
    from openprogram.runtime.conversation import run_control
    run_control.unregister_cancel_event("t1", busy_target)

    delivered = inbox.drain("t1")
    assert delivered == 1
    assert inbox.pending_count("t1") == 0
    kw = calls[0]
    assert kw["session_id"] == "t1"
    assert kw["caller_session_id"] == "p1"   # reply routes back to sender
    assert kw["caller_msg_id"] == "a1"
    assert kw["prompt"].startswith("[message from p1:a1 (sender)]")
    assert "hello t1" in kw["prompt"]
    # Delivered onto the target's current head.
    head = (two_sessions.get_session("t1") or {}).get("head_id")
    assert kw["branch_from"] == head


def test_drain_leaves_entry_on_failed_delivery(two_sessions, busy_target, monkeypatch):
    """Deliver-then-delete: a failed submission keeps the entry queued."""
    _send_message_impl("keep me", to="t1:b1")

    def boom(**kw):
        raise RuntimeError("runner down")
    monkeypatch.setattr(
        "openprogram.runtime.conversation.peer_agents.sub_agent_run.run_agent_turn_async", boom)
    assert inbox.drain("t1") == 0
    assert inbox.pending_count("t1") == 1


def test_queued_delivery_inherits_both_chain_counts(two_sessions, busy_target, monkeypatch):
    """A queued hop spends a message like a direct one, and creates no
    agent, so the generation count crosses the queue unchanged."""
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        set_chain_generations, set_chain_messages,
    )
    calls = _capture_async(monkeypatch)
    tokens = [set_chain_messages(3), set_chain_generations(1)]
    try:
        _send_message_impl("deep hello", to="t1:b1")
    finally:
        for tok in tokens:
            tok.var.reset(tok)
    assert inbox.drain("t1") == 0  # the target's turn is still running
    from openprogram.runtime.conversation import run_control
    run_control.unregister_cancel_event("t1", busy_target)
    delivered = inbox.drain("t1")
    assert delivered == 1
    assert calls[0]["chain_messages"] == 4      # recorded 3 messages, +1
    assert calls[0]["chain_generations"] == 1
    assert calls[0]["caller_chain_generations"] == 1


def test_drain_reads_legacy_spawn_depth_key(two_sessions, monkeypatch):
    """An inbox.json written before the rename stores the count under
    ``spawn_depth``; drain still honours it."""
    calls = _capture_async(monkeypatch)
    inbox.enqueue(
        "t1", message="old entry", sender_session_id="p1",
        sender_msg_id="a1", sender_agent_id="main", agent_id="main",
        chain_messages=0, target_head_id="b1",
    )
    path = inbox._inbox_path("t1")
    blob = json.loads(path.read_text(encoding="utf-8"))
    entry = blob["entries"][0]
    entry.pop("chain_messages")
    entry["spawn_depth"] = 5
    path.write_text(json.dumps(blob), encoding="utf-8")

    assert inbox.drain("t1") == 1
    assert calls[0]["chain_messages"] == 6      # legacy 5, child +1


def test_cap_drops_oldest_and_notifies_sender(two_sessions):
    for i in range(inbox.MAX_PENDING):
        assert inbox.enqueue(
            "t1", message=f"msg {i}", sender_session_id="p1",
            sender_msg_id="a1", sender_agent_id="main", agent_id="main",
            chain_messages=0, target_head_id="b1",
        ) == "queued"
    assert inbox.pending_count("t1") == inbox.MAX_PENDING

    assert inbox.enqueue(
        "t1", message="one too many", sender_session_id="p1",
        sender_msg_id="a1", sender_agent_id="main", agent_id="main",
        chain_messages=0, target_head_id="b1",
    ) == "queued"
    assert inbox.pending_count("t1") == inbox.MAX_PENDING
    path = inbox._inbox_path("t1")
    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    messages = [e["message"] for e in entries]
    assert "msg 0" not in messages           # oldest dropped
    assert "one too many" in messages
    # The dropped message's sender got a system notice.
    notes = [m for m in (two_sessions.get_messages("p1") or [])
             if "inbox is full" in str(m.get("content") or "")]
    assert notes
    assert "msg 0" in notes[0]["content"]


def test_duplicate_within_window_rejected(two_sessions, busy_target, monkeypatch):
    _capture_async(monkeypatch)
    out1 = _send_message_impl("same words", to="t1:b1")
    out2 = _send_message_impl("same words", to="t1:b1")
    assert "[queued]" in out1
    assert "duplicate message ignored" in out2
    assert inbox.pending_count("t1") == 1


def test_duplicate_outside_window_accepted(two_sessions):
    assert inbox.enqueue(
        "t1", message="same words", sender_session_id="p1",
        sender_msg_id="a1", sender_agent_id="main", agent_id="main",
        chain_messages=0, target_head_id="b1",
    ) == "queued"
    # Age the queued copy past the dedup window.
    path = inbox._inbox_path("t1")
    blob = json.loads(path.read_text(encoding="utf-8"))
    blob["entries"][0]["enqueued_at"] -= inbox.DEDUP_WINDOW_SECS + 1
    path.write_text(json.dumps(blob), encoding="utf-8")
    assert inbox.enqueue(
        "t1", message="same words", sender_session_id="p1",
        sender_msg_id="a1", sender_agent_id="main", agent_id="main",
        chain_messages=0, target_head_id="b1",
    ) == "queued"
    assert inbox.pending_count("t1") == 2


def test_sweep_delivers_once_the_target_goes_quiet(two_sessions, busy_target, monkeypatch):
    """A turn that ends without draining (cancelled, crashed) does not
    strand the message: the worker sweep delivers it once the target
    is free."""
    calls = _capture_async(monkeypatch)
    _send_message_impl("later", to="t1:b1")
    inbox.sweep()
    assert calls == [] and inbox.pending_count("t1") == 1
    from openprogram.runtime.conversation import run_control
    run_control.unregister_cancel_event("t1", busy_target)
    inbox.sweep()
    assert len(calls) == 1 and inbox.pending_count("t1") == 0


def test_same_session_send_waits_for_a_busy_branch(two_sessions, monkeypatch):
    """Inside the sender's own session only the addressed branch counts:
    a sub-agent still extending it holds the message; once it ends the
    message continues that branch's new tip, not the session head."""
    from openprogram.runtime.jobs.store import save_job
    from openprogram.runtime.jobs.types import Job, JobStatus
    s = two_sessions
    s.append_message("p1", {"id": "x1", "role": "user", "content": "side",
                            "timestamp": 0, "predecessor": "u1"})
    s.append_message("p1", {"id": "x2", "role": "assistant", "content": "side reply",
                            "timestamp": 0, "predecessor": "x1"})
    s.set_head("p1", "a1")
    s.commit_turn("p1", "side branch")
    worker = Job(id="w1", parent_session_id="p1", prompt="work", agent_id="main",
                 status=JobStatus.RUNNING, target_branch_head_id="x2")
    save_job("p1", worker)
    calls = _capture_async(monkeypatch)
    out = _send_message_impl("ping", to="p1:x2")
    assert out.startswith("[queued]")
    assert inbox.drain("p1") == 0
    s.append_message("p1", {"id": "x3", "role": "user", "content": "more",
                            "timestamp": 0, "predecessor": "x2"})
    s.append_message("p1", {"id": "x4", "role": "assistant", "content": "done",
                            "timestamp": 0, "predecessor": "x3"})
    s.set_head("p1", "a1")
    s.commit_turn("p1", "worker done")
    worker.status, worker.head_id = JobStatus.COMPLETED, "x4"
    save_job("p1", worker)
    assert inbox.drain("p1") == 1
    assert calls[0]["branch_from"] == "x4"


def test_notify_when_idle_answers_at_once_for_an_idle_target(two_sessions):
    out = _send_message_impl("", to="t1:b1", notify_when_idle=True)
    assert out.startswith("[idle] t1:b1") and "sup" in out


def test_an_idle_watch_drops_the_notice_of_the_reply_it_hands_back(two_sessions, monkeypatch):
    from openprogram.runtime.jobs import notices
    read = []
    monkeypatch.setattr(notices, "consume_reply", lambda *a: read.append(a))
    _send_message_impl("", to="t1:b1", notify_when_idle=True)
    assert read and read[0][1:] == ("t1", "b1")


def test_notify_when_idle_sends_one_notice_when_the_target_goes_quiet(
        two_sessions, busy_target, monkeypatch):
    from types import SimpleNamespace
    from openprogram.runtime.jobs import notices
    monkeypatch.setattr(notices, "_due", set())
    monkeypatch.setattr(notices, "_seeded", True)
    calls = _capture_async(monkeypatch)
    sent: list = []
    runner = SimpleNamespace(_dispatch_followup=lambda *jobs: sent.append(jobs))
    monkeypatch.setattr("openprogram.runtime.jobs.get_runner", lambda: runner)

    out = _send_message_impl("", to="t1:b1", notify_when_idle=True)
    assert out.startswith("[watching]")
    assert calls == []  # nothing was delivered to the target
    notices.sweep()
    assert sent == []
    from openprogram.runtime.conversation import run_control
    run_control.unregister_cancel_event("t1", busy_target)
    notices.sweep()
    notices.sweep()
    (jobs,) = sent
    (job,) = jobs
    assert job.source == "idle_watch" and job.caller_session_id == "p1"
    text = notices.notice_text(list(jobs))
    assert "t1:b1" in text and "is idle now" in text and "sup" in text



def test_a_message_from_another_conversation_is_marked_and_still_returns(two_sessions, monkeypatch):
    """The receipt says the message is another agent's input, not the
    receiver's user, and its reply still comes back to the sender."""
    calls = _capture_async(monkeypatch)
    out = _send_message_impl("hello t1", to="t1:b1")
    assert "comes back to you automatically" in out
    kw = calls[-1]
    assert kw["caller_session_id"] == "p1"
    assert "not from your user" in kw["prompt"]
    assert "returned to the sender automatically" in kw["prompt"]


def test_a_queued_message_from_another_conversation_is_marked(two_sessions, busy_target, monkeypatch):
    calls = _capture_async(monkeypatch)
    _send_message_impl("hello t1", to="t1:b1")
    from openprogram.runtime.conversation import run_control
    run_control.unregister_cancel_event("t1", busy_target)
    inbox.drain("t1")
    assert "not from your user" in calls[0]["prompt"]


def test_a_queued_cross_session_message_continues_the_branch_it_was_sent_to(
    two_sessions, busy_target, monkeypatch,
):
    """The target may select another branch while the message waits; the
    message still continues the branch it addressed, from that branch's tip."""
    s = two_sessions
    calls = _capture_async(monkeypatch)
    _send_message_impl("hello t1", to="t1:b1")
    s.append_message("t1", {"id": "b9", "role": "assistant", "content": "another version",
                            "timestamp": 0, "predecessor": "u2"})
    s.set_head("t1", "b9")
    s.commit_turn("t1", "another branch selected")
    from openprogram.runtime.conversation import run_control
    run_control.unregister_cancel_event("t1", busy_target)
    assert inbox.drain("t1") == 1
    assert calls[0]["branch_from"] == "b1"


# ---- addressing, limits that outlive a restart, loops ------------------------

def test_an_agent_name_reaches_its_one_conversation(two_sessions, monkeypatch):
    from openprogram.runtime.agent_registry import manager
    two_sessions.create_session("butler1", "butler", title="Morning watch")
    two_sessions.append_message("butler1", {"id": "u9", "role": "user", "content": "watch",
                                            "timestamp": 0, "predecessor": None})
    two_sessions.commit_turn("butler1", "init")
    monkeypatch.setattr(manager, "list_all", lambda: [manager.AgentSpec(id="butler", name="Butler")])
    calls = _capture_async(monkeypatch)
    out = _send_message_impl("check the build", to="BUTLER")
    assert out.startswith("[delivered]"), out
    assert calls[-1]["session_id"] == "butler1"


def test_the_hourly_send_window_survives_a_worker_restart(two_sessions, monkeypatch):
    from openprogram.programs.tools.agents.send_message.send_message import depth
    monkeypatch.setattr(depth, "max_sends_per_hour", lambda: 1)
    _capture_async(monkeypatch)
    assert _send_message_impl("one", to="t1:b1").startswith("[delivered]")
    # A new Worker: nothing in memory, the window is read from the session.
    depth._sends.clear()
    from openprogram.runtime.permissions.refusal import Refused
    with pytest.raises(Refused, match="sends this hour"):
        _send_message_impl("two", to="t1:b1")


def test_a_message_back_to_a_conversation_waiting_on_this_chain_is_refused(two_sessions, monkeypatch):
    from openprogram.programs.tools.agents.send_message.send_message import depth
    calls = _capture_async(monkeypatch)
    token = depth.set_chain_ancestors(("t1",))
    try:
        out = _send_message_impl("and you?", to="t1:b1")
    finally:
        depth._chain_ancestors.reset(token)
    assert out.startswith("[send_message refused]") and "already waiting" in out
    assert calls == []


def test_a_cross_conversation_send_carries_who_waits_above_it(two_sessions, monkeypatch):
    calls = _capture_async(monkeypatch)
    _send_message_impl("relay this", to="t1:b1")
    assert "p1" in calls[-1]["chain_ancestors"]


# ---- a message to one's own running helper is a steer ------------------------

def test_a_message_to_ones_running_helper_steers_it(two_sessions, busy_target, monkeypatch):
    from types import SimpleNamespace
    from openprogram.programs.tools.agents.send_message.send_message import steer
    monkeypatch.setattr(steer, "spawned_running_job", lambda run_session, tip, sid: SimpleNamespace(id="j_help"))
    submitted = []
    monkeypatch.setattr(steer, "submit", lambda execution_id, text, **kw: submitted.append(
        (execution_id, text, kw["sender_session_id"])) or "send_cmd1")
    calls = _capture_async(monkeypatch)
    out = _send_message_impl("also check the tests", to="t1:b1")
    assert out.startswith("[steering] execution_id=j_help"), out
    assert submitted[0][0] == "j_help" and "also check the tests" in submitted[0][1]
    assert calls == []
    # The fallback entry waits for the turn's end; a steer the turn took is
    # dropped there, not delivered twice.
    (entry,) = inbox._load(inbox._inbox_path("t1"))
    assert entry["steer"] == {"command_id": "send_cmd1", "execution_id": "j_help"}
    monkeypatch.setattr(steer, "taken", lambda command_id: command_id == "send_cmd1")
    monkeypatch.setattr(inbox, "_ready", lambda sid, e: True)
    inbox.drain("t1")
    assert inbox.pending_count("t1") == 0 and calls == []


def test_a_steer_that_cannot_be_taken_queues_as_before(two_sessions, busy_target, monkeypatch):
    from types import SimpleNamespace
    from openprogram.programs.tools.agents.send_message.send_message import steer
    monkeypatch.setattr(steer, "spawned_running_job", lambda *a: SimpleNamespace(id="j_help"))
    monkeypatch.setattr(steer, "submit", lambda *a, **k: None)
    _capture_async(monkeypatch)
    assert _send_message_impl("later", to="t1:b1").startswith("[queued]")


def test_only_the_spawner_finds_its_helper(monkeypatch):
    from openprogram.programs.tools.agents.send_message.send_message import steer
    from openprogram.runtime.jobs.runner import _current_job_id
    from openprogram.runtime.jobs.types import Job, JobStatus
    helper = Job(id="j_help", parent_session_id="t1", prompt="x", agent_id="main", status=JobStatus.RUNNING,
                 creates_agent=True, wait=False, caller_session_id="p1", head_id="h1")
    monkeypatch.setattr("openprogram.runtime.jobs.store.list_jobs", lambda sid: [helper])
    assert steer.spawned_running_job("t1", "h1", "p1") is helper
    assert steer.spawned_running_job("t1", "h1", "other") is None
    assert steer.spawned_running_job("t1", "other-tip", "p1") is None
    token = _current_job_id.set("j_someone")
    try:
        assert steer.spawned_running_job("t1", "h1", "p1") is None
    finally:
        _current_job_id.reset(token)


def test_a_running_helper_addressed_by_its_reply_is_steered_not_run_twice(two_sessions, monkeypatch):
    """While a background helper runs, its name resolves to the reply it is
    writing (``<job>_user_reply``) and its Job has no head yet. A message
    from its spawner reaches it at its next tool step; it used to find the
    branch idle and start a second turn of the helper beside the first."""
    from openprogram.programs.tools.agents.send_message.send_message import steer
    from openprogram.runtime.jobs.store import save_job
    from openprogram.runtime.jobs.types import Job, JobStatus
    s = two_sessions
    s.append_message("p1", {"id": "j_help_user", "role": "user", "content": "find it",
                            "timestamp": 0, "predecessor": "a1"})
    s.append_message("p1", {"id": "j_help_user_reply", "role": "assistant", "content": "",
                            "timestamp": 0, "predecessor": "j_help_user"})
    s.set_head("p1", "a1")
    s.commit_turn("p1", "helper started")
    helper = Job(id="j_help", parent_session_id="p1", prompt="find it", agent_id="explorer",
                 status=JobStatus.RUNNING, creates_agent=True, wait=False, caller_session_id="p1")
    save_job("p1", helper)
    assert inbox.branch_busy("p1", "j_help_user_reply")
    assert steer.spawned_running_job("p1", "j_help_user_reply", "p1").id == "j_help"

    submitted = []
    monkeypatch.setattr(steer, "submit", lambda execution_id, text, **kw: submitted.append(execution_id) or "cmd1")
    calls = _capture_async(monkeypatch)
    out = _send_message_impl("also give the line number", to="p1:j_help_user_reply")
    assert out.startswith("[steering] execution_id=j_help"), out
    assert submitted == ["j_help"] and calls == []
    helper.status, helper.head_id = JobStatus.COMPLETED, "j_help_user_reply"
    save_job("p1", helper)
    assert not inbox.branch_busy("p1", "j_help_user_reply")

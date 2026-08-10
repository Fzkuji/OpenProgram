"""agent(to=…) tracked-task dispatch + task_output/task_stop ownership.

All fake — no real LLM, no live task pool run (deliveries are captured
at run_agent_turn_async, same technique as test_send_message.py):

  * addressing reuse (SID:HEAD → current tip, branch-name resolution)
  * idle target → immediate dispatch with the task header + caller
    routing + depth inheritance
  * to + start_from mutual exclusion, self-dispatch guard, depth guard
  * busy target → pending Task pre-created + inbox entry carrying its id
  * task_stop three states: queued → withdrawn from the inbox;
    running → per-turn cancel on the target; terminal → idempotent no-op
  * drain: tracked entries deliver with the task header and reuse the
    task id; withdrawn entries are dropped without delivering
  * ownership checks on task_output / task_stop: foreign session
    refused; dispatcher, ancestor chain, and no-session (user/UI) allowed

See docs/reference/design/runtime/agent-collaboration.md.
"""
from __future__ import annotations

import pytest

from openprogram.functions.tools.agent.agent.agent import _agent_impl
from openprogram.functions.tools.agent._ownership import check_task_ownership


@pytest.fixture
def parent_turn(tmp_path, monkeypatch):
    """Isolated store, two sessions, parent turn bound on p1:a1.
    Deliveries are captured at run_agent_turn_async; captured kwargs are
    exposed as ``store.async_calls``."""
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod

    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.session.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.default_store", lambda: s)
    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {"id": "u1", "role": "user", "content": "hi",
                            "timestamp": 0, "predecessor": None})
    s.append_message("p1", {"id": "a1", "role": "assistant", "content": "ok",
                            "timestamp": 0, "predecessor": "u1"})
    # A second, earlier branch tip (not the current turn) as a target.
    s.append_message("p1", {"id": "u0", "role": "user", "content": "older",
                            "timestamp": 0, "predecessor": "ROOT"})
    s.append_message("p1", {"id": "a0", "role": "assistant", "content": "older reply",
                            "timestamp": 0, "predecessor": "u0"})
    s.commit_turn("p1", "init")
    # A second session as a cross-session target.
    s.create_session("p2", "main", title="other")
    s.append_message("p2", {"id": "u9", "role": "user", "content": "x",
                            "timestamp": 0, "predecessor": None})
    s.append_message("p2", {"id": "a9", "role": "assistant", "content": "y",
                            "timestamp": 0, "predecessor": "u9"})
    s.commit_turn("p2", "init")

    from openprogram.agent import run_control
    from openprogram import store as store_mod
    sid_tok = run_control._current_session_id.set("p1")
    turn_tok = store_mod._current_turn_id.set("a1")

    calls: list[dict] = []

    def fake_async(**kw):
        calls.append(kw)
        return "t_fake"

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn_async", fake_async)
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None)
    s.async_calls = calls

    # Build the runner NOW (empty store) so its one-shot orphan
    # reconciliation doesn't flip tasks the tests save later.
    from openprogram.agent.task import get_runner
    get_runner()

    yield s
    run_control._current_session_id.reset(sid_tok)
    store_mod._current_turn_id.reset(turn_tok)
    import openprogram.agent.task.runner as runner_mod
    runner_mod.shutdown_runner()


# --- to= dispatch, idle target ---

def test_to_dispatch_runs_immediately_on_target_tip(parent_turn):
    out = _agent_impl("summarize the log", to="p1:a0")
    assert "[task dispatched]" in out
    assert "task_id=t_fake" in out
    kw = parent_turn.async_calls[-1]
    assert kw["session_id"] == "p1"
    assert kw["branch_from"] == "a0"
    assert kw["context_mode"] == "inherit"
    # Tracked-task receipt header + reply routing back to the dispatcher.
    assert kw["prompt"].startswith("[task from p1:a1]")
    assert "summarize the log" in kw["prompt"]
    assert kw["caller_session_id"] == "p1"
    assert kw["caller_msg_id"] == "a1"
    assert kw["chain_messages"] == 1


def test_to_dispatch_creates_no_generation(parent_turn):
    """agent(to=…) hands work to an agent that already exists, so it
    spends a message and leaves the generation count alone — the target
    and the reply turn both run at the dispatcher's count."""
    from openprogram.functions.tools.send_message.send_message.depth import (
        set_chain_generations,
    )
    tok = set_chain_generations(1)
    try:
        _agent_impl("summarize the log", to="p1:a0")
    finally:
        tok.var.reset(tok)
    kw = parent_turn.async_calls[-1]
    assert kw["chain_generations"] == 1
    assert kw["caller_chain_generations"] == 1


def test_same_session_dispatch_result_reaches_the_dispatcher(parent_turn, monkeypatch):
    """A to= dispatch onto a branch in the SAME session creates no attach
    pointer (it spawns nothing), so its followup has to carry the reply
    inline. It used to take the same-session spawn wording — "the whole
    transcript is attached above" — with nothing attached, and the result
    never reached the dispatcher."""
    from openprogram.agent.task import runner as runner_mod
    from openprogram.agent.task.types import Task, TaskStatus

    _agent_impl("summarize the log", to="p1:a0", description="probe")
    kw = parent_turn.async_calls[-1]
    task = Task(
        id="t_same1",
        parent_session_id=kw["session_id"],
        prompt=kw["prompt"],
        agent_id=kw["agent_id"],
        caller_session_id=kw["caller_session_id"],
        caller_msg_id=kw["caller_msg_id"],
        label=kw.get("label"),
        status=TaskStatus.COMPLETED,
        head_id="h1",
        result_text="the summary",
    )
    # The shape that made the notice lie: same session, no attach pointer.
    assert task.caller_session_id == task.parent_session_id
    assert task.attach_pointer_id is None

    seen: dict = {}

    def fake_process(req, **_kw):
        seen["session_id"] = req.session_id
        seen["text"] = req.user_text
        return type("_R", (), {})()

    import openprogram.agent.dispatcher as disp
    monkeypatch.setattr(disp, "process_user_turn", fake_process)

    def run_inline(target=None, daemon=None, **_kw):
        return type("_T", (), {"start": lambda self_: target()})()

    monkeypatch.setattr(runner_mod.threading, "Thread", run_inline)
    runner_mod.get_runner()._dispatch_followup(task)

    assert seen["session_id"] == "p1"
    assert "the summary" in seen["text"]
    assert "嵌在上面" not in seen["text"]


def test_to_addressing_by_branch_name(parent_turn):
    parent_turn.set_branch_name("p1", "a0", "research")
    out = _agent_impl("dig deeper", to="research")
    assert "[task dispatched]" in out
    assert parent_turn.async_calls[-1]["branch_from"] == "a0"


def test_to_stale_head_snaps_to_current_tip(parent_turn):
    s = parent_turn
    s.append_message("p1", {"id": "u2", "role": "user", "content": "later",
                            "timestamp": 0, "predecessor": "a0"})
    s.append_message("p1", {"id": "a2", "role": "assistant", "content": "later reply",
                            "timestamp": 0, "predecessor": "u2"})
    s.commit_turn("p1", "advance")
    _agent_impl("more", to="p1:a0")  # stale head
    assert s.async_calls[-1]["branch_from"] == "a2"


def test_to_unknown_target_errors(parent_turn):
    out = _agent_impl("hi", to="nosuchname")
    assert "[agent error]" in out and "not found" in out


def test_to_and_start_from_conflict(parent_turn):
    out = _agent_impl("hi", to="p1:a0", start_from="inherit")
    assert "mutually exclusive" in out
    assert not parent_turn.async_calls


def test_to_self_dispatch_refused(parent_turn):
    out = _agent_impl("loop", to="p1:a1")
    assert "[agent refused]" in out and "own current branch" in out


def test_to_ignores_run_in_background(parent_turn):
    out = _agent_impl("go", to="p1:a0", run_in_background=True)
    assert "[task dispatched]" in out  # still the async dispatch path


def test_to_message_budget_guard(parent_turn):
    from openprogram.functions.tools.send_message.send_message.depth import (
        set_chain_messages, _chain_messages, MAX_MESSAGES,
    )
    tok = set_chain_messages(MAX_MESSAGES)
    try:
        out = _agent_impl("deeper", to="p1:a0")
    finally:
        _chain_messages.reset(tok)
    assert "[agent refused]" in out and "messages" in out


# --- to= dispatch, busy target → pending task + inbox entry ---

def _dispatch_queued(parent_turn, monkeypatch, prompt="audit the config"):
    monkeypatch.setattr(
        "openprogram.agent.run_control.is_turn_running",
        lambda sid: sid == "p2",
    )
    out = _agent_impl(prompt, to="p2:a9", description="audit")
    assert "[task dispatched, queued]" in out
    tid = out.split("task_id=")[1].split()[0]
    return tid, out


def test_to_busy_target_precreates_task_and_queues(parent_turn, monkeypatch):
    from openprogram.agent import inbox
    from openprogram.agent.task.store import load_task
    from openprogram.agent.task.types import TaskStatus
    tid, _ = _dispatch_queued(parent_turn, monkeypatch)
    # Task record exists, pending, routed back to the dispatcher.
    t = load_task("p2", tid)
    assert t is not None
    assert t.status == TaskStatus.PENDING
    assert t.caller_session_id == "p1"
    assert t.caller_msg_id == "a1"
    assert t.parent_msg_id == "a9"
    assert t.chain_messages == 1
    # Inbox entry carries the task id.
    path = inbox._inbox_path("p2")
    entries = inbox._load(path)
    assert len(entries) == 1
    assert entries[0]["task_id"] == tid
    # Nothing was submitted to the pool while the target is busy.
    assert not parent_turn.async_calls


def test_task_stop_withdraws_queued_dispatch(parent_turn, monkeypatch):
    from openprogram.agent import inbox
    from openprogram.agent.task import get_runner, TaskStatus
    tid, _ = _dispatch_queued(parent_turn, monkeypatch)
    # The target session must NOT receive a session-level cancel: it is
    # busy with someone else's turn.
    killed = []
    monkeypatch.setattr(
        "openprogram.agent.run_control.mark_cancelled",
        lambda sid: killed.append(sid))
    res = get_runner().cancel_task(tid)
    assert res is not None
    assert res.status == TaskStatus.CANCELLED
    assert inbox.pending_count("p2") == 0
    assert killed == []


def test_task_stop_terminal_is_noop(parent_turn):
    from openprogram.agent.task import get_runner, TaskStatus
    from openprogram.agent.task.store import save_task
    from openprogram.agent.task.types import Task
    save_task("p2", Task(id="t_done1", parent_session_id="p2", prompt="x",
                         agent_id="main", status=TaskStatus.COMPLETED))
    res = get_runner().cancel_task("t_done1")
    assert res is not None
    assert res.status == TaskStatus.COMPLETED  # unchanged, idempotent


def test_task_stop_running_cancels_target_turn(parent_turn, monkeypatch):
    """A running to= task cancels THAT turn on the target (session-level
    cancel event + runtime kill), not the queued-withdraw path."""
    import threading
    from openprogram.agent.task import get_runner, TaskStatus
    from openprogram.agent.task.store import save_task
    from openprogram.agent.task.types import Task
    runner = get_runner()
    save_task("p2", Task(id="t_run1", parent_session_id="p2", prompt="x",
                         agent_id="main", status=TaskStatus.RUNNING,
                         caller_session_id="p1"))
    ev = threading.Event()
    runner._tasks["t_run1"] = {"event": ev, "future": None, "session_id": "p2"}
    marked = []
    monkeypatch.setattr(
        "openprogram.agent.run_control.mark_cancelled",
        lambda sid: marked.append(sid))
    monkeypatch.setattr(
        "openprogram.agent.run_control.kill_active_runtime",
        lambda sid: None)
    res = runner.cancel_task("t_run1")
    assert res is not None
    assert ev.is_set()
    assert marked == ["p2"]
    runner._tasks.pop("t_run1", None)


# --- inbox drain of tracked entries ---

def test_drain_delivers_tracked_entry_with_task_header(parent_turn, monkeypatch):
    from openprogram.agent import inbox
    tid, _ = _dispatch_queued(parent_turn, monkeypatch)
    monkeypatch.setattr(
        "openprogram.agent.run_control.is_turn_running", lambda sid: False)
    delivered = inbox.drain("p2")
    assert delivered == 1
    kw = parent_turn.async_calls[-1]
    assert kw["task_id"] == tid  # SAME task id — the dispatcher's handle
    assert kw["prompt"].startswith("[task from p1:a1]")
    assert kw["caller_session_id"] == "p1"
    assert inbox.pending_count("p2") == 0


def test_drain_skips_withdrawn_tracked_entry(parent_turn, monkeypatch):
    from openprogram.agent import inbox
    from openprogram.agent.task import get_runner
    tid, _ = _dispatch_queued(parent_turn, monkeypatch)
    # Withdraw... but re-add the inbox entry to simulate the race where
    # drain sees an entry whose task is already terminal.
    get_runner().cancel_task(tid)
    inbox.enqueue("p2", message="audit the config", sender_session_id="p1",
                  sender_msg_id="a1", sender_agent_id="main", agent_id="main",
                  chain_messages=0, target_head_id="a9", task_id=tid)
    monkeypatch.setattr(
        "openprogram.agent.run_control.is_turn_running", lambda sid: False)
    delivered = inbox.drain("p2")
    assert delivered == 0
    assert not parent_turn.async_calls  # never submitted
    assert inbox.pending_count("p2") == 0  # entry dropped


def test_spawn_task_does_not_resurrect_terminal_precreated(parent_turn):
    from openprogram.agent.task import get_runner, TaskStatus
    from openprogram.agent.task.store import load_task, save_task
    from openprogram.agent.task.types import Task
    save_task("p2", Task(id="t_gone1", parent_session_id="p2", prompt="x",
                         agent_id="main", status=TaskStatus.CANCELLED))
    tid = get_runner().spawn_task(
        session_id="p2", prompt="x", agent_id="main", task_id="t_gone1")
    assert tid == "t_gone1"
    assert load_task("p2", "t_gone1").status == TaskStatus.CANCELLED


# --- ownership checks on task_output / task_stop ---

def test_ownership_rejects_foreign_session(parent_turn):
    """p2's task ids are readable via read_conversation — a foreign
    session must not be able to wait on or stop them."""
    from openprogram.agent import run_control
    from openprogram.agent.task.store import save_task
    from openprogram.agent.task.types import Task, TaskStatus
    from openprogram.functions.tools.agent.task_output.task_output import (
        _task_output_impl,
    )
    save_task("p2", Task(id="t_theirs", parent_session_id="p2", prompt="x",
                         agent_id="main", status=TaskStatus.RUNNING))
    # Current session is p1 (fixture); the task belongs to p2 only.
    assert run_control._current_session_id.get(None) == "p1"
    out = check_task_ownership("t_theirs", "task_stop")
    assert "was not dispatched by this session" in out
    out2 = _task_output_impl("t_theirs", block=False)
    assert "was not dispatched by this session" in out2


def test_ownership_allows_dispatcher_session(parent_turn):
    """The dispatcher (caller_session_id) may manage a task running in
    another session — that is the whole point of to= dispatch."""
    from openprogram.agent.task.store import save_task
    from openprogram.agent.task.types import Task, TaskStatus
    save_task("p2", Task(id="t_mine", parent_session_id="p2", prompt="x",
                         agent_id="main", status=TaskStatus.RUNNING,
                         caller_session_id="p1"))
    assert check_task_ownership("t_mine", "task_stop") is None


def test_ownership_allows_ancestor_chain(parent_turn):
    """A task chain ancestor (cascading-cancel lineage) may manage its
    descendants even across sessions."""
    from openprogram.agent.task.store import save_task
    from openprogram.agent.task.types import Task, TaskStatus
    # px dispatched t_root; t_root spawned t_child in p2.
    save_task("p2", Task(id="t_root2", parent_session_id="p2", prompt="x",
                         agent_id="main", status=TaskStatus.RUNNING,
                         caller_session_id="p1"))
    save_task("p2", Task(id="t_child2", parent_session_id="p2", prompt="x",
                         agent_id="main", status=TaskStatus.RUNNING,
                         parent_task_id="t_root2"))
    # Current session p1 dispatched the ancestor → child is manageable.
    assert check_task_ownership("t_child2", "task_stop") is None


def test_ownership_allows_without_session_context(parent_turn, monkeypatch):
    """User / UI calls carry no session ContextVar — never gated."""
    from openprogram.agent import run_control
    from openprogram.agent.task.store import save_task
    from openprogram.agent.task.types import Task, TaskStatus
    save_task("p2", Task(id="t_ui", parent_session_id="p2", prompt="x",
                         agent_id="main", status=TaskStatus.RUNNING))
    tok = run_control._current_session_id.set(None)
    try:
        assert check_task_ownership("t_ui", "task_stop") is None
    finally:
        run_control._current_session_id.reset(tok)


def test_ownership_unknown_task_passes_through(parent_turn):
    """Unknown ids are not the ownership check's problem — the tool
    reports them itself."""
    assert check_task_ownership("t_nope", "task_stop") is None

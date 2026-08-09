"""Cascading cancel — cancel_task stops the whole parent_task_id subtree.

Covers (design: agent-collaboration.md §5.3):
  * cancelling a parent flips its pending/queued children to cancelled
    before they ever run
  * a running child receives the cancel signal through the normal
    per-task cancel path
  * grandchildren stop too (recursion over the chain)
  * a cycle in parent_task_id does not hang the walk (visited guard)
  * spawns made inside a running task record parent_task_id
  * session-level cancel clears the target's inbox and leaves a system
    notice in each sender session

Fake-worker technique mirrors tests/unit/test_async_task.py.
"""
from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture
def store_fixture(tmp_path, monkeypatch):
    """Isolated SessionStore with three session rows (parent / child /
    grandchild lanes)."""
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.default_store", lambda: s)
    for sid in ("p1", "p2", "p3"):
        s.create_session(sid, "main", title=sid)
        s.append_message(sid, {
            "id": f"u_{sid}", "role": "user", "content": "hi",
            "timestamp": 0, "predecessor": None,
        })
        s.append_message(sid, {
            "id": f"a_{sid}", "role": "assistant", "content": "ok",
            "timestamp": 0, "predecessor": f"u_{sid}",
        })
        s.commit_turn(sid, "init")
    return s


@pytest.fixture
def fake_worker(monkeypatch):
    """Deterministic run_agent_turn stand-in: blocks on a barrier,
    drops out when the session-level cancel flag fires."""
    calls = []
    barrier = threading.Event()
    cancel_seen = threading.Event()

    def fake_run(*, session_id, prompt, agent_id, branch_from=None,
                 label=None, spawn_caller=None, advance_head=True):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        from openprogram.agent.run_control import is_cancelled
        calls.append({"session_id": session_id, "prompt": prompt})
        for _ in range(100):
            if barrier.is_set():
                break
            if is_cancelled(session_id):
                cancel_seen.set()
                return AgentTurnResult(head_id="head_x", final_text="",
                                       failed=True, error="cancelled")
            time.sleep(0.02)
        return AgentTurnResult(head_id="head_ok", final_text="done",
                               failed=False, error=None)

    import openprogram.agent.task.runner as runner_mod
    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn", fake_run,
    )
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    yield calls, barrier, cancel_seen
    runner_mod.shutdown_runner()


def _wait_status(runner, tid, statuses, timeout=5.0):
    from openprogram.agent.task.types import TaskStatus  # noqa: F401
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = runner.get_task(tid)
        if t is not None and t.status in statuses:
            return t
        time.sleep(0.02)
    return runner.get_task(tid)


def test_parent_cancel_dequeues_pending_child(store_fixture, fake_worker,
                                              monkeypatch):
    """Pending child (never picked up) flips to cancelled with the parent."""
    monkeypatch.setenv("OPENPROGRAM_TASK_WORKERS", "1")
    import openprogram.agent.task.runner as runner_mod
    runner_mod.shutdown_runner()
    calls, barrier, _ = fake_worker
    from openprogram.agent.task import get_runner, TaskStatus
    runner = get_runner()

    parent = runner.spawn_task(
        session_id="p1", prompt="parent", agent_id="main",
        parent_msg_id="a_p1",
    )
    # Wait until the parent occupies the single worker.
    _wait_status(runner, parent, {TaskStatus.RUNNING})
    child = runner.spawn_task(
        session_id="p2", prompt="child", agent_id="main",
        parent_msg_id="a_p2", parent_task_id=parent,
    )
    runner.cancel_task(parent)
    p = runner.await_task(parent, timeout=5.0)
    c = runner.await_task(child, timeout=5.0)
    assert p.status == TaskStatus.CANCELLED
    assert c.status == TaskStatus.CANCELLED
    # The child never ran.
    assert all(call["prompt"] != "child" for call in calls)


def test_parent_cancel_stops_running_child(store_fixture, fake_worker,
                                           monkeypatch):
    """Running child (different session) is cancelled through the normal
    per-task cancel path."""
    monkeypatch.setenv("OPENPROGRAM_TASK_WORKERS", "2")
    import openprogram.agent.task.runner as runner_mod
    runner_mod.shutdown_runner()
    _, barrier, cancel_seen = fake_worker
    from openprogram.agent.task import get_runner, TaskStatus
    runner = get_runner()

    parent = runner.spawn_task(
        session_id="p1", prompt="parent", agent_id="main",
        parent_msg_id="a_p1",
    )
    child = runner.spawn_task(
        session_id="p2", prompt="child", agent_id="main",
        parent_msg_id="a_p2", parent_task_id=parent,
    )
    assert _wait_status(runner, parent, {TaskStatus.RUNNING}).status == TaskStatus.RUNNING
    assert _wait_status(runner, child, {TaskStatus.RUNNING}).status == TaskStatus.RUNNING

    runner.cancel_task(parent)
    p = runner.await_task(parent, timeout=5.0)
    c = runner.await_task(child, timeout=5.0)
    assert p.status == TaskStatus.CANCELLED
    assert c.status == TaskStatus.CANCELLED
    assert cancel_seen.is_set()


def test_grandchild_stops_too(store_fixture, fake_worker, monkeypatch):
    """Recursion: parent → child → grandchild, all cancelled from the root."""
    monkeypatch.setenv("OPENPROGRAM_TASK_WORKERS", "1")
    import openprogram.agent.task.runner as runner_mod
    runner_mod.shutdown_runner()
    _, barrier, _ = fake_worker
    from openprogram.agent.task import get_runner, TaskStatus
    runner = get_runner()

    parent = runner.spawn_task(
        session_id="p1", prompt="parent", agent_id="main",
        parent_msg_id="a_p1",
    )
    _wait_status(runner, parent, {TaskStatus.RUNNING})
    child = runner.spawn_task(
        session_id="p2", prompt="child", agent_id="main",
        parent_msg_id="a_p2", parent_task_id=parent,
    )
    grandchild = runner.spawn_task(
        session_id="p3", prompt="grandchild", agent_id="main",
        parent_msg_id="a_p3", parent_task_id=child,
    )
    runner.cancel_task(parent)
    for tid in (parent, child, grandchild):
        final = runner.await_task(tid, timeout=5.0)
        assert final.status == TaskStatus.CANCELLED, tid


def test_cycle_in_parent_chain_does_not_hang(store_fixture, fake_worker):
    """A (theoretically impossible) parent_task_id cycle terminates."""
    from openprogram.agent.task.types import Task, TaskStatus
    from openprogram.agent.task.store import save_task
    from openprogram.agent.task import get_runner

    # Construct the runner FIRST — its startup reconcile flips any
    # pre-existing non-terminal task to errored.
    runner = get_runner()
    a = Task(id="t_cyc_a", parent_session_id="p1", prompt="a",
             agent_id="main", parent_task_id="t_cyc_b")
    b = Task(id="t_cyc_b", parent_session_id="p1", prompt="b",
             agent_id="main", parent_task_id="t_cyc_a")
    save_task("p1", a)
    save_task("p1", b)
    done = threading.Event()
    result = {}

    def _go():
        result["task"] = runner.cancel_task("t_cyc_a")
        done.set()

    threading.Thread(target=_go, daemon=True).start()
    assert done.wait(timeout=5.0), "cancel_task hung on a parent cycle"
    assert result["task"].status == TaskStatus.CANCELLED
    assert runner.get_task("t_cyc_b").status == TaskStatus.CANCELLED


def test_spawn_inside_task_records_parent(store_fixture, fake_worker,
                                          monkeypatch):
    """A spawn made from inside a running task defaults parent_task_id
    to that task (the producer side of the cascade chain)."""
    import openprogram.agent.task.runner as runner_mod
    from openprogram.agent.task import get_runner
    runner = get_runner()
    recorded = {}

    def fake_run(*, session_id, prompt, agent_id, branch_from=None,
                 label=None, spawn_caller=None, advance_head=True):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        # Simulate a tool inside this turn spawning a sub-task.
        child_id = runner.spawn_task(
            session_id="p2", prompt="inner child", agent_id="main",
            parent_msg_id="a_p2",
        )
        recorded["child_id"] = child_id
        return AgentTurnResult(head_id="h", final_text="ok",
                               failed=False, error=None)

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn", fake_run,
    )
    parent = runner.spawn_task(
        session_id="p1", prompt="outer", agent_id="main",
        parent_msg_id="a_p1",
    )
    runner.await_task(parent, timeout=5.0)
    assert "child_id" in recorded
    child_task = runner.await_task(recorded["child_id"], timeout=5.0)
    assert child_task.parent_task_id == parent


def test_session_cancel_clears_inbox_with_sender_notice(store_fixture):
    """Session-level cancel drops queued messages and notifies senders."""
    from openprogram.agent import inbox

    inbox.enqueue(
        "p2",
        message="queued while busy",
        sender_session_id="p1",
        sender_msg_id="a_p1",
        sender_agent_id="main",
        agent_id="main",
        chain_messages=0,
        target_head_id="a_p2",
    )
    assert inbox.pending_count("p2") == 1

    cleared = inbox.clear("p2", reason="the target session was stopped by the user")
    assert cleared == 1
    assert inbox.pending_count("p2") == 0

    # Sender session got a runtime-display notice, head untouched.
    msgs = store_fixture.get_messages("p1") or []
    notices = [m for m in msgs if "discarded" in (m.get("content") or "")]
    assert len(notices) == 1
    assert "queued while busy" in notices[0]["content"]
    assert (store_fixture.get_session("p1") or {}).get("head_id") == "a_p1"

    # Idempotent: clearing an empty inbox is a no-op.
    assert inbox.clear("p2") == 0

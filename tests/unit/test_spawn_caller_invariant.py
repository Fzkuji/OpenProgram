"""spawn_caller invariant across the three spawn entry points.

session-dag.md §2.3: a clean-mode spawn (new branch, branch_from=None)
must pass ``spawn_caller=<spawning node>`` so its branch root's caller is
the spawning turn and the DAG attaches the branch to that turn instead of
forking it from ROOT. An inherit-mode spawn (branch_from set) passes
``spawn_caller=None`` — the fork point is already the predecessor.

The sync task() path (commit 1d1fe016) had dropped this; the async runner
and message_branch already had it. These tests pin all three so a refactor
can't silently re-orphan a sub-branch at the root.

Each entry imports ``run_agent_turn`` from
``openprogram.agent.sub_agent_run`` at call time, so patching it there
captures the kwargs for every path.
"""
from __future__ import annotations

from contextvars import copy_context

import pytest

from openprogram.agent.sub_agent_run import AgentTurnResult


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolated SessionStore + a parent session with one committed turn."""
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.default_store", lambda: s)
    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {
        "id": "u1", "role": "user", "content": "hi",
        "timestamp": 0, "predecessor": None,
    })
    s.append_message("p1", {
        "id": "a1", "role": "assistant", "content": "ok",
        "timestamp": 0, "predecessor": "u1",
    })
    s.commit_turn("p1", "init")
    return s


@pytest.fixture
def captured_run(monkeypatch):
    """Replace run_agent_turn (+ the attach-pointer writer) so the spawn
    impls run without a real LLM and we can read back spawn_caller."""
    cap = {}

    def fake_run(*, session_id, prompt, agent_id, branch_from=None,
                 label=None, spawn_caller=None):
        cap["session_id"] = session_id
        cap["branch_from"] = branch_from
        cap["spawn_caller"] = spawn_caller
        return AgentTurnResult(head_id="head_x", final_text="(reply)")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn", fake_run,
    )
    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.write_attach_pointer_for_spawn",
        lambda **kw: None,
    )
    return cap


def _run_with_ctx(fn, *, session_id, turn_id):
    """Run ``fn`` with the session/turn ContextVars the spawn impls read."""
    from openprogram.webui._pause_stop import _current_session_id
    from openprogram.store import _current_turn_id

    def _go():
        t1 = _current_session_id.set(session_id)
        t2 = _current_turn_id.set(turn_id)
        try:
            return fn()
        finally:
            _current_session_id.reset(t1)
            _current_turn_id.reset(t2)

    return copy_context().run(_go)


# ---- entry 1: sync task() (task.py _task_impl) --------------------------

def test_task_sync_clean_passes_spawn_caller(store, captured_run):
    from openprogram.functions.tools.task.task import _task_impl
    _run_with_ctx(
        lambda: _task_impl(prompt="go", context="clean", wait=True),
        session_id="p1", turn_id="a1",
    )
    assert captured_run["branch_from"] is None
    assert captured_run["spawn_caller"] == "a1"


def test_task_sync_inherit_passes_no_spawn_caller(store, captured_run):
    from openprogram.functions.tools.task.task import _task_impl
    _run_with_ctx(
        lambda: _task_impl(prompt="go", context="inherit", wait=True),
        session_id="p1", turn_id="a1",
    )
    assert captured_run["branch_from"] == "a1"
    assert captured_run["spawn_caller"] is None


# ---- entry 2: message_branch (message_branch.py _message_branch_impl) ---

def test_message_branch_new_root_passes_spawn_caller(store, captured_run):
    from openprogram.functions.tools.agent_collab.message_branch import (
        _message_branch_impl,
    )
    _run_with_ctx(
        lambda: _message_branch_impl(message="do it", target="new", wait=True),
        session_id="p1", turn_id="a1",
    )
    # target=new → branch_from=None → explicit spawn attached to caller.
    assert captured_run["branch_from"] is None
    assert captured_run["spawn_caller"] == "a1"


def test_message_branch_fork_passes_no_spawn_caller(store, captured_run):
    from openprogram.functions.tools.agent_collab.message_branch import (
        _message_branch_impl,
    )
    _run_with_ctx(
        lambda: _message_branch_impl(
            message="do it", target="new:p1:u1", wait=True,
        ),
        session_id="p1", turn_id="a1",
    )
    # fork off an existing node → branch_from set → no spawn_caller.
    assert captured_run["branch_from"] == "u1"
    assert captured_run["spawn_caller"] is None


# ---- entry 3: async runner (task/runner.py _run_one) --------------------

def test_runner_clean_passes_spawn_caller(store, monkeypatch):
    """The async worker calls run_agent_turn with
    spawn_caller=caller_msg_id when context_mode=clean (branch_from=None).
    Drive a real task through the pool with a fake worker that records it."""
    cap = {}

    def fake_run(*, session_id, prompt, agent_id, branch_from=None,
                 label=None, spawn_caller=None):
        cap["branch_from"] = branch_from
        cap["spawn_caller"] = spawn_caller
        return AgentTurnResult(head_id="head_ok", final_text="hello")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn", fake_run,
    )
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    import openprogram.agent.task.runner as runner_mod
    runner_mod.shutdown_runner()
    from openprogram.agent.task import get_runner
    runner = get_runner()
    try:
        tid = runner.spawn_task(
            session_id="p1", prompt="go", agent_id="main",
            context_mode="clean", caller_msg_id="a1", parent_msg_id="a1",
        )
        final = runner.await_task(tid, timeout=5.0)
        assert final is not None
        assert cap["branch_from"] is None
        assert cap["spawn_caller"] == "a1"
    finally:
        runner_mod.shutdown_runner()


def test_runner_inherit_passes_no_spawn_caller(store, monkeypatch):
    cap = {}

    def fake_run(*, session_id, prompt, agent_id, branch_from=None,
                 label=None, spawn_caller=None):
        cap["branch_from"] = branch_from
        cap["spawn_caller"] = spawn_caller
        return AgentTurnResult(head_id="head_ok", final_text="hello")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn", fake_run,
    )
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    import openprogram.agent.task.runner as runner_mod
    runner_mod.shutdown_runner()
    from openprogram.agent.task import get_runner
    runner = get_runner()
    try:
        tid = runner.spawn_task(
            session_id="p1", prompt="go", agent_id="main",
            context_mode="inherit", caller_msg_id="a1", parent_msg_id="a1",
        )
        final = runner.await_task(tid, timeout=5.0)
        assert final is not None
        assert cap["branch_from"] == "a1"
        assert cap["spawn_caller"] is None
    finally:
        runner_mod.shutdown_runner()


# ---- entry 1b: async task() (task.py _task_impl wait=False) -------------

def test_task_async_passes_caller_and_depth(store, monkeypatch):
    """The wait=False branch must anchor the spawn to the calling turn
    (caller_msg_id) and carry the incremented chain depth — dropping
    caller_msg_id re-orphaned async spawns at ROOT (the c919c000 case)."""
    cap = {}

    def fake_async(**kw):
        cap.update(kw)
        return "t_fake"

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn_async", fake_async,
    )
    from openprogram.functions.tools.task.task import _task_impl
    out = _run_with_ctx(
        lambda: _task_impl(prompt="go", context="clean", wait=False),
        session_id="p1", turn_id="a1",
    )
    assert "task spawned async" in out
    assert cap["caller_msg_id"] == "a1"
    assert cap["spawn_depth"] == 1


# ---- depth guard: task() refuses past MAX_SPAWN_DEPTH --------------------

def test_task_refuses_at_max_task_depth(store, captured_run):
    """task()'s own cap (MAX_TASK_DEPTH=2) is deliberately tighter than
    message_branch's MAX_SPAWN_DEPTH: coordinator→worker is the deepest
    legitimate task() nesting; a worker delegating again gets refused."""
    from openprogram.functions.tools.agent_collab.message_branch import (
        set_spawn_depth, _spawn_depth,
    )
    from openprogram.functions.tools.task.task import MAX_TASK_DEPTH, _task_impl

    def _call():
        tok = set_spawn_depth(MAX_TASK_DEPTH)
        try:
            return _task_impl(prompt="go", context="clean", wait=True)
        finally:
            _spawn_depth.reset(tok)

    out = _run_with_ctx(_call, session_id="p1", turn_id="a1")
    assert "[task refused]" in out
    assert "spawn_caller" not in captured_run  # never reached the spawn


def test_task_allows_coordinator_level(store, captured_run):
    """Depth 1 (a spawned coordinator) may still task() workers."""
    from openprogram.functions.tools.agent_collab.message_branch import (
        set_spawn_depth, _spawn_depth,
    )
    from openprogram.functions.tools.task.task import _task_impl

    def _call():
        tok = set_spawn_depth(1)
        try:
            return _task_impl(prompt="go", context="clean", wait=True)
        finally:
            _spawn_depth.reset(tok)

    _run_with_ctx(_call, session_id="p1", turn_id="a1")
    assert captured_run["spawn_caller"] == "a1"  # spawn went through


def test_task_sync_child_sees_incremented_depth(store, monkeypatch):
    """The sync path binds depth+1 around the child turn, so a chain of
    task()-inside-task() eventually trips the guard instead of recursing
    forever (each generation used to start back at depth 0)."""
    from openprogram.functions.tools.agent_collab.message_branch import (
        current_spawn_depth,
    )
    from openprogram.agent.sub_agent_run import AgentTurnResult as _R
    seen = {}

    def fake_run(**kw):
        seen["child_depth"] = current_spawn_depth()
        return _R(head_id="h", final_text="(reply)")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn", fake_run,
    )
    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.write_attach_pointer_for_spawn",
        lambda **kw: None,
    )
    from openprogram.functions.tools.task.task import _task_impl
    _run_with_ctx(
        lambda: _task_impl(prompt="go", context="clean", wait=True),
        session_id="p1", turn_id="a1",
    )
    assert seen["child_depth"] == 1

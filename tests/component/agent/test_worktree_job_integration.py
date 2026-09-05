"""Integration tests for Async Job ↔ Worktree binding.

Covers:

  * spawn_job(worktree_id=...) → worker thread sees the worktree
    bound to its ContextVar, so tools default cwd to the worktree
  * Job cancel → worktree auto-discarded (D15)
  * Job complete / errored → worktree left alone (caller decides)
"""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _worker_lock_is_held(monkeypatch):
    import openprogram.agent.job.runner as runner_mod

    runner_mod.shutdown_runner()
    monkeypatch.setattr(
        "openprogram.worker.lock.is_held_by", lambda _pid: True,
    )
    yield
    runner_mod.shutdown_runner()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "a.txt").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


@pytest.fixture
def store_fixture(tmp_path, monkeypatch):
    """Isolated SessionStore for job persistence."""
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr(
        "openprogram.store.session.session_store.default_store", lambda: s,
    )
    monkeypatch.setattr(
        "openprogram.store.default_store", lambda: s,
    )
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
def isolated_worktree_state(tmp_path, monkeypatch):
    """Route worktree persistence to a tmp dir so the test doesn't
    interact with the user's real ~/.agentic/worktrees.json."""
    state = tmp_path / "agentic"
    state.mkdir()
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)
    monkeypatch.setattr("openprogram.paths.ensure_state_dir", lambda: state)
    monkeypatch.setattr(
        "openprogram.worktree.manager.get_state_dir", lambda: state,
    )
    monkeypatch.setattr(
        "openprogram.worktree.store.get_state_dir", lambda: state,
    )
    monkeypatch.setattr(
        "openprogram.worktree.store.ensure_state_dir", lambda: state,
    )
    from openprogram.worktree.manager import _reset_manager_for_tests
    _reset_manager_for_tests()
    yield state
    _reset_manager_for_tests()


def test_job_binds_worktree_in_worker(
    store_fixture, isolated_worktree_state, tmp_path, monkeypatch,
):
    """The worker thread bound to a job with worktree_id sees the
    worktree path via current_worktree_path() while it runs."""
    # Silence WS broadcasts.
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    repo = tmp_path / "src"
    _init_repo(repo)
    from openprogram.worktree.manager import get_manager as get_wt_mgr
    wt = get_wt_mgr().create_worktree(str(repo), label="jobwt")

    seen: dict = {}

    def fake_run(*, request, cancel_event, **_kwargs):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        from openprogram.worktree.context import current_worktree_path
        seen["worktree_path"] = current_worktree_path()
        return AgentTurnResult(head_id="head_done", final_text="done",
                               failed=False, error=None)

    monkeypatch.setattr(
        "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(fake_run),
    )
    from openprogram.agent.job import get_runner, JobStatus
    runner = get_runner()
    tid = runner.spawn_job(
        session_id="p1", prompt="do thing", agent_id="main",
        parent_msg_id="a1", label="alpha",
        worktree_id=wt.id,
    )
    final = runner.await_job(tid, timeout=5.0)
    assert final is not None
    assert final.status == JobStatus.COMPLETED
    assert final.worktree_id == wt.id
    # ContextVar was set during the worker run.
    assert seen.get("worktree_path") == wt.worktree_path


def test_job_cancel_discards_worktree(
    store_fixture, isolated_worktree_state, tmp_path, monkeypatch,
):
    """When a job with a worktree is cancelled mid-flight, the runner
    auto-discards the worktree per D15 of the design."""
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    repo = tmp_path / "src"
    _init_repo(repo)
    from openprogram.worktree.manager import get_manager as get_wt_mgr
    mgr = get_wt_mgr()
    wt = mgr.create_worktree(str(repo), label="cancelwt")

    started = threading.Event()
    can_release = threading.Event()

    def fake_run(*, request, cancel_event, **_kwargs):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        started.set()
        # Poll for cancel up to ~3s
        for _ in range(150):
            if can_release.is_set() or cancel_event.is_set():
                break
            time.sleep(0.02)
        if cancel_event.is_set():
            return AgentTurnResult(head_id="head_x", final_text="",
                                   failed=True, error="cancelled")
        return AgentTurnResult(head_id="head_ok", final_text="ok",
                               failed=False, error=None)

    monkeypatch.setattr(
        "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(fake_run),
    )
    from openprogram.agent.job import get_runner, JobStatus
    runner = get_runner()
    tid = runner.spawn_job(
        session_id="p1", prompt="long", agent_id="main",
        parent_msg_id="a1", worktree_id=wt.id,
    )
    assert started.wait(timeout=3.0)
    runner.cancel_execution(tid)
    final = runner.await_job(tid, timeout=5.0)
    assert final is not None
    assert final.status == JobStatus.CANCELLED
    # Settle: the discard runs in the worker's finally clause, which
    # races with await_job's wakeup. Poll briefly so the test isn't
    # flaky on slow CI.
    from openprogram.worktree.types import WorktreeStatus
    cur = None
    for _ in range(50):
        cur = mgr.get_worktree(wt.id)
        if cur is not None and cur.status == WorktreeStatus.DISCARDED:
            break
        time.sleep(0.05)
    assert cur is not None
    assert cur.status == WorktreeStatus.DISCARDED


def test_job_complete_leaves_worktree_alone(
    store_fixture, isolated_worktree_state, tmp_path, monkeypatch,
):
    """A job that completes successfully should NOT auto-merge or
    auto-discard its worktree — the caller decides."""
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    repo = tmp_path / "src"
    _init_repo(repo)
    from openprogram.worktree.manager import get_manager as get_wt_mgr
    mgr = get_wt_mgr()
    wt = mgr.create_worktree(str(repo), label="completewt")

    def fake_run(*, request, cancel_event, **_kwargs):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        return AgentTurnResult(head_id="head_ok", final_text="ok",
                               failed=False, error=None)

    monkeypatch.setattr(
        "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(fake_run),
    )
    from openprogram.agent.job import get_runner, JobStatus
    runner = get_runner()
    tid = runner.spawn_job(
        session_id="p1", prompt="short", agent_id="main",
        parent_msg_id="a1", worktree_id=wt.id,
    )
    final = runner.await_job(tid, timeout=5.0)
    assert final is not None
    assert final.status == JobStatus.COMPLETED
    cur = mgr.get_worktree(wt.id)
    assert cur is not None
    from openprogram.worktree.types import WorktreeStatus
    assert cur.status == WorktreeStatus.ACTIVE

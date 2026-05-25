"""Unit tests for ``openprogram.worktree.manager.WorktreeManager``.

Exercise the state machine, safety guards, and the actual ``git
worktree`` lifecycle against a throwaway repo in pytest's tmp_path.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from openprogram.worktree.manager import (
    WorktreeError,
    WorktreeManager,
    _reset_manager_for_tests,
)
from openprogram.worktree.store import _store_path
from openprogram.worktree.types import WorktreeStatus, can_transition


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "a.txt").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Route ~/.agentic/ to a tmp dir so worktrees.json + the
    worktrees/ directory don't leak between tests / pollute the
    user's real profile."""
    state = tmp_path / "agentic"
    state.mkdir()
    monkeypatch.setenv("OPENPROGRAM_PROFILE", "")
    monkeypatch.setattr(
        "openprogram.paths.get_state_dir",
        lambda: state,
    )
    monkeypatch.setattr(
        "openprogram.paths.ensure_state_dir",
        lambda: state,
    )
    # ``manager`` and ``store`` modules import these symbols by-name
    # at module load — we have to rebind those too or the sessions-git
    # check uses the user's real ~/.agentic.
    monkeypatch.setattr(
        "openprogram.worktree.manager.get_state_dir",
        lambda: state,
    )
    monkeypatch.setattr(
        "openprogram.worktree.store.get_state_dir",
        lambda: state,
    )
    monkeypatch.setattr(
        "openprogram.worktree.store.ensure_state_dir",
        lambda: state,
    )
    _reset_manager_for_tests()
    yield state
    _reset_manager_for_tests()


@pytest.fixture
def repo(tmp_path):
    p = tmp_path / "src"
    _init_repo(p)
    return p


def test_create_then_merge_ff_only_lands_files(isolated_state, repo):
    mgr = WorktreeManager()
    wt = mgr.create_worktree(str(repo), label="feat-x")
    assert wt.status == WorktreeStatus.ACTIVE
    assert Path(wt.worktree_path).exists()
    assert wt.branch_name.startswith("op/wt/")
    # Make a change + commit inside the worktree
    new_file = Path(wt.worktree_path) / "b.txt"
    new_file.write_text("new\n")
    subprocess.run(["git", "add", "-A"], cwd=wt.worktree_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "b"],
        cwd=wt.worktree_path, check=True,
    )
    # Merge
    res = mgr.merge_worktree(wt.id, strategy="ff-only", delete_branch=True)
    assert res.status == WorktreeStatus.MERGED
    assert res.merge_sha
    assert (repo / "b.txt").exists()
    # Worktree dir is gone
    assert not Path(wt.worktree_path).exists()


def test_create_rejects_non_git_repo(isolated_state, tmp_path):
    mgr = WorktreeManager()
    bare = tmp_path / "not-a-repo"
    bare.mkdir()
    with pytest.raises(WorktreeError, match="not_a_git_repo"):
        mgr.create_worktree(str(bare))


def test_create_rejects_path_inside_sessions_git(isolated_state, tmp_path):
    sessions_root = isolated_state / "sessions-git" / "sess1"
    sessions_root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=sessions_root, check=True)
    mgr = WorktreeManager()
    with pytest.raises(WorktreeError, match="worktree_in_sessions_dir"):
        mgr.create_worktree(str(sessions_root))


def test_discard_clean_worktree(isolated_state, repo):
    mgr = WorktreeManager()
    wt = mgr.create_worktree(str(repo), label="discard-test")
    res = mgr.discard_worktree(wt.id)
    assert res.status == WorktreeStatus.DISCARDED
    assert not Path(wt.worktree_path).exists()
    # branch is gone too
    rc = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{wt.branch_name}"],
        cwd=repo, capture_output=True,
    ).returncode
    assert rc != 0


def test_discard_refuses_dirty_without_force(isolated_state, repo):
    mgr = WorktreeManager()
    wt = mgr.create_worktree(str(repo), label="dirty")
    (Path(wt.worktree_path) / "dirty.txt").write_text("uncommitted\n")
    with pytest.raises(WorktreeError, match="worktree_dirty"):
        mgr.discard_worktree(wt.id)
    # force=True succeeds
    res = mgr.discard_worktree(wt.id, force=True)
    assert res.status == WorktreeStatus.DISCARDED


def test_merge_dirty_worktree_refused(isolated_state, repo):
    mgr = WorktreeManager()
    wt = mgr.create_worktree(str(repo), label="dirty-merge")
    (Path(wt.worktree_path) / "x.txt").write_text("uncommitted\n")
    with pytest.raises(WorktreeError, match="worktree_dirty"):
        mgr.merge_worktree(wt.id)
    # Still active, untouched
    cur = mgr.get_worktree(wt.id)
    assert cur is not None
    assert cur.status == WorktreeStatus.ACTIVE
    assert Path(wt.worktree_path).exists()


def test_merge_failure_keeps_worktree_alive(isolated_state, repo):
    """Part 5 #3 invariant: merge failure must NOT destroy the
    worktree. We force a not-fast-forward by committing on main
    *and* on the worktree branch."""
    mgr = WorktreeManager()
    wt = mgr.create_worktree(str(repo), label="ff-conflict")
    # Commit on main (source repo)
    (repo / "main_change.txt").write_text("on main\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "main"],
        cwd=repo, check=True,
    )
    # Commit on worktree
    (Path(wt.worktree_path) / "wt_change.txt").write_text("on wt\n")
    subprocess.run(["git", "add", "-A"], cwd=wt.worktree_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "wt"],
        cwd=wt.worktree_path, check=True,
    )
    with pytest.raises(WorktreeError):
        mgr.merge_worktree(wt.id, strategy="ff-only")
    cur = mgr.get_worktree(wt.id)
    assert cur is not None
    # State machine: committing → back to active on failure
    assert cur.status == WorktreeStatus.ACTIVE
    assert cur.error
    assert Path(wt.worktree_path).exists()


def test_keep_worktree_preserves_dir_and_branch(isolated_state, repo):
    mgr = WorktreeManager()
    wt = mgr.create_worktree(str(repo), label="kept")
    res = mgr.keep_worktree(wt.id)
    assert res.status == WorktreeStatus.KEPT
    assert Path(wt.worktree_path).exists()
    # branch still resolvable
    rc = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{wt.branch_name}"],
        cwd=repo, capture_output=True,
    ).returncode
    assert rc == 0


def test_state_transition_rejects_illegal_moves():
    # COMPLETED-style states are absorbing in our state machine.
    assert not can_transition(WorktreeStatus.MERGED, WorktreeStatus.ACTIVE)
    assert not can_transition(WorktreeStatus.DISCARDED, WorktreeStatus.MERGED)
    assert not can_transition(WorktreeStatus.KEPT, WorktreeStatus.ACTIVE)
    # Legal moves
    assert can_transition(WorktreeStatus.ACTIVE, WorktreeStatus.COMMITTING)
    assert can_transition(WorktreeStatus.COMMITTING, WorktreeStatus.MERGED)
    assert can_transition(WorktreeStatus.COMMITTING, WorktreeStatus.ACTIVE)
    assert can_transition(WorktreeStatus.ACTIVE, WorktreeStatus.DISCARDED)
    assert can_transition(WorktreeStatus.ACTIVE, WorktreeStatus.KEPT)


def test_list_and_get_worktree(isolated_state, repo):
    mgr = WorktreeManager()
    wt1 = mgr.create_worktree(str(repo), label="one")
    wt2 = mgr.create_worktree(str(repo), label="two", parent_session="sess-A")
    rows = mgr.list_worktrees()
    ids = {w.id for w in rows}
    assert wt1.id in ids and wt2.id in ids
    # parent_session filter
    sess_rows = mgr.list_worktrees(parent_session="sess-A")
    assert [w.id for w in sess_rows] == [wt2.id]
    # find_active_for_session
    assert mgr.find_active_for_session("sess-A").id == wt2.id
    # get by id
    g = mgr.get_worktree(wt1.id)
    assert g is not None and g.id == wt1.id


def test_persistence_round_trip(isolated_state, repo):
    """Worktree state should survive a fresh manager instance, as
    long as the same store path is in play."""
    mgr1 = WorktreeManager()
    wt = mgr1.create_worktree(str(repo), label="persist")
    # Construct a new manager — singleton bypass.
    mgr2 = WorktreeManager()
    found = mgr2.get_worktree(wt.id)
    assert found is not None
    assert found.id == wt.id
    assert found.worktree_path == wt.worktree_path
    # The worktrees.json lives in the isolated state dir.
    assert _store_path().exists()
    blob = _store_path().read_text()
    assert wt.id in blob

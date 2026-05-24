"""Per-agent git worktree primitives on GitSession (task E, part 1)."""
from __future__ import annotations

from pathlib import Path

from openprogram.store.git_session import GitSession


def test_add_worktree_creates_branch_and_path(tmp_path: Path):
    gs = GitSession(tmp_path / "s1")
    gs._ensure_init()
    # main needs at least one non-empty commit beyond `session init` so
    # the worktree gets a real base to branch from.
    (gs.workdir_path / "main.txt").write_text("main")
    gs.commit_all("turn 1")

    wt = gs.add_worktree("agent_a")
    assert wt.exists()
    assert wt == gs.path / "_worktrees" / "agent_a"

    wts = gs.list_worktrees()
    paths = [w.get("worktree") for w in wts]
    assert str(wt) in paths
    branches = [w.get("branch", "") for w in wts]
    assert any(b.endswith("agent_a") for b in branches)


def test_add_worktree_idempotent_on_path(tmp_path: Path):
    gs = GitSession(tmp_path / "s1")
    gs._ensure_init()
    (gs.workdir_path / "f.txt").write_text("x")
    gs.commit_all("init turn")

    wt1 = gs.add_worktree("agent_a")
    wt2 = gs.add_worktree("agent_a")
    assert wt1 == wt2


def test_worktree_edits_isolated_from_main(tmp_path: Path):
    gs = GitSession(tmp_path / "s1")
    gs._ensure_init()
    (gs.workdir_path / "shared.txt").write_text("v0")
    gs.commit_all("init")

    wt = gs.add_worktree("agent_a")
    # write inside the worktree
    (wt / "workdir" / "agent_only.txt").parent.mkdir(parents=True, exist_ok=True)
    (wt / "workdir" / "agent_only.txt").write_text("from agent")
    # main repo's workdir doesn't see this file
    assert not (gs.workdir_path / "agent_only.txt").exists()


def test_remove_worktree(tmp_path: Path):
    gs = GitSession(tmp_path / "s1")
    gs._ensure_init()
    (gs.workdir_path / "f.txt").write_text("x")
    gs.commit_all("init")
    wt = gs.add_worktree("agent_a")
    assert wt.exists()
    gs.remove_worktree(wt)
    wts = gs.list_worktrees()
    paths = [w.get("worktree") for w in wts]
    assert str(wt) not in paths

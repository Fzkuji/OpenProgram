"""workdir/ subdir lives inside the session git repo (task C, minimal)."""
from __future__ import annotations

from pathlib import Path

from openprogram.store.git_session import GitSession


def test_workdir_materialized_on_init(tmp_path: Path):
    gs = GitSession(tmp_path / "s1")
    gs._ensure_init()
    wd = gs.workdir_path
    assert wd.exists() and wd.is_dir()
    assert (wd / ".gitkeep").exists()


def test_workdir_files_get_committed(tmp_path: Path):
    gs = GitSession(tmp_path / "s1")
    gs._ensure_init()
    target = gs.workdir_path / "scratch.txt"
    target.write_text("hello")
    sha = gs.commit_all("turn 1: scratch")
    assert sha
    # subsequent: nothing new to commit
    assert gs.commit_all("turn 2: noop") is None
    # tracked by git
    out = gs._run("ls-files")
    assert "workdir/scratch.txt" in out

"""Bash checkpoint: verify that _snapshot_cwd / _checkpoint_changed_files
detect file mutations and call checkpoint_before_edit."""

from __future__ import annotations

import os
from unittest.mock import patch

from openprogram.agent.agent_loop import _snapshot_cwd, _checkpoint_changed_files


def test_snapshot_returns_none_for_non_bash():
    assert _snapshot_cwd("write") is None
    assert _snapshot_cwd("edit") is None


def test_snapshot_captures_files_in_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.txt").write_text("world")

    with patch("openprogram.worktree.context.current_worktree_path", return_value=None):
        snap = _snapshot_cwd("bash")

    assert snap is not None
    assert str(tmp_path / "a.txt") in snap
    assert str(tmp_path / "b.txt") in snap


def test_snapshot_skips_dotfiles(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".hidden").write_text("secret")
    (tmp_path / "visible.txt").write_text("ok")

    with patch("openprogram.worktree.context.current_worktree_path", return_value=None):
        snap = _snapshot_cwd("bash")

    assert snap is not None
    assert not any(".hidden" in k for k in snap)
    assert any("visible.txt" in k for k in snap)


def test_checkpoint_detects_modified_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "target.txt"
    f.write_text("before")

    with patch("openprogram.worktree.context.current_worktree_path", return_value=None):
        snap = _snapshot_cwd("bash")

    f.write_text("after — content changed")

    backed_up = []
    with patch("openprogram.worktree.context.current_worktree_path", return_value=None), \
         patch("openprogram.store.snapshot.checkpoint.helpers.checkpoint_before_edit", side_effect=backed_up.append):
        _checkpoint_changed_files("bash", snap)

    assert any("target.txt" in p for p in backed_up)


def test_checkpoint_detects_new_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "existing.txt").write_text("ok")

    with patch("openprogram.worktree.context.current_worktree_path", return_value=None):
        snap = _snapshot_cwd("bash")

    (tmp_path / "new_file.txt").write_text("created by bash")

    backed_up = []
    with patch("openprogram.worktree.context.current_worktree_path", return_value=None), \
         patch("openprogram.store.snapshot.checkpoint.helpers.checkpoint_before_edit", side_effect=backed_up.append):
        _checkpoint_changed_files("bash", snap)

    assert any("new_file.txt" in p for p in backed_up)


def test_checkpoint_ignores_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stable.txt").write_text("no change")

    with patch("openprogram.worktree.context.current_worktree_path", return_value=None):
        snap = _snapshot_cwd("bash")

    backed_up = []
    with patch("openprogram.worktree.context.current_worktree_path", return_value=None), \
         patch("openprogram.store.snapshot.checkpoint.helpers.checkpoint_before_edit", side_effect=backed_up.append):
        _checkpoint_changed_files("bash", snap)

    assert len(backed_up) == 0


def test_checkpoint_noop_for_non_bash(tmp_path, monkeypatch):
    """Non-bash tools should not trigger any checkpoint from this path."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.txt").write_text("a")

    with patch("openprogram.worktree.context.current_worktree_path", return_value=None):
        snap = _snapshot_cwd("bash")

    (tmp_path / "x.txt").write_text("b")

    backed_up = []
    with patch("openprogram.worktree.context.current_worktree_path", return_value=None), \
         patch("openprogram.store.snapshot.checkpoint.helpers.checkpoint_before_edit", side_effect=backed_up.append):
        _checkpoint_changed_files("write", snap)

    assert len(backed_up) == 0


# --- recursive scan (subdirectories, skip lists, bounds) ---------------


def test_snapshot_recurses_into_subdirectories(tmp_path, monkeypatch):
    """bash can write anywhere in the tree, so nested files must be seen."""
    monkeypatch.chdir(tmp_path)
    nested = tmp_path / "pkg" / "sub"
    nested.mkdir(parents=True)
    (nested / "deep.py").write_text("x")

    with patch("openprogram.worktree.context.current_worktree_path", return_value=None):
        snap = _snapshot_cwd("bash")

    assert str(nested / "deep.py") in snap


def test_snapshot_skips_heavy_directories(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("node_modules", ".git", "__pycache__", "dist"):
        d = tmp_path / name
        d.mkdir()
        (d / "junk.txt").write_text("junk")
    (tmp_path / "real.py").write_text("keep")

    with patch("openprogram.worktree.context.current_worktree_path", return_value=None):
        snap = _snapshot_cwd("bash")

    assert str(tmp_path / "real.py") in snap
    assert not any("junk.txt" in p for p in snap)


def test_snapshot_respects_depth_limit(tmp_path, monkeypatch):
    """Beyond _SCAN_MAX_DEPTH the walk stops rather than descending forever."""
    from openprogram.agent.agent_loop import _SCAN_MAX_DEPTH

    monkeypatch.chdir(tmp_path)
    deep = tmp_path
    for i in range(_SCAN_MAX_DEPTH + 3):
        deep = deep / f"d{i}"
    deep.mkdir(parents=True)
    (deep / "buried.py").write_text("too deep")

    shallow = tmp_path / "d0" / "near.py"
    shallow.write_text("visible")

    with patch("openprogram.worktree.context.current_worktree_path", return_value=None):
        snap = _snapshot_cwd("bash")

    assert str(shallow) in snap
    assert str(deep / "buried.py") not in snap


def test_checkpoint_detects_nested_modification(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    nested = tmp_path / "pkg"
    nested.mkdir()
    f = nested / "mod.py"
    f.write_text("before")

    with patch("openprogram.worktree.context.current_worktree_path", return_value=None):
        snap = _snapshot_cwd("bash")

    f.write_text("after — changed")

    backed_up = []
    with patch("openprogram.worktree.context.current_worktree_path", return_value=None), \
         patch("openprogram.store.snapshot.checkpoint.helpers.checkpoint_before_edit", side_effect=backed_up.append):
        _checkpoint_changed_files("bash", snap)

    assert str(f) in backed_up


def test_checkpoint_detects_nested_new_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pkg").mkdir()

    with patch("openprogram.worktree.context.current_worktree_path", return_value=None):
        snap = _snapshot_cwd("bash")

    created = tmp_path / "pkg" / "created.py"
    created.write_text("new file from bash")

    backed_up = []
    with patch("openprogram.worktree.context.current_worktree_path", return_value=None), \
         patch("openprogram.store.snapshot.checkpoint.helpers.checkpoint_before_edit", side_effect=backed_up.append):
        _checkpoint_changed_files("bash", snap)

    assert str(created) in backed_up

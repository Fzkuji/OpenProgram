"""Deleting a retired tree: read-only entries go, and what cannot go is named.

Windows refuses to delete a read-only file (Git objects are written that way)
and a file another process holds. The stand-in ``os.unlink`` below has those
rules, so the cases run on any system; ``test_real_read_only_files_go`` uses
the system's own rules and is the one that means something on Windows, where
this file has not run.
"""
import os
import stat

import pytest

from openprogram.operations.self_update.versions import removal
from tests.support.windows_deletes import tree_with_read_only_objects, windows_unlink


def test_read_only_files_are_removed_where_the_system_refuses_them(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    tree_with_read_only_objects(tree)
    windows_unlink(monkeypatch)
    assert removal.remove_tree(tree) == []
    assert not tree.exists()


def test_real_read_only_files_go(tmp_path):
    tree = tmp_path / "tree"
    tree_with_read_only_objects(tree)
    assert removal.remove_tree(tree) == []
    assert not tree.exists()


@pytest.mark.skipif(os.name == "nt", reason="a Windows directory's mode does not refuse a delete")
def test_a_directory_without_write_permission_is_removed(tmp_path):
    tree = tmp_path / "tree"
    objects = tree_with_read_only_objects(tree)
    objects.chmod(0o555)
    try:
        assert removal.remove_tree(tree) == []
    finally:
        if objects.exists():
            objects.chmod(0o755)
    assert not tree.exists()


def test_a_held_file_is_named_and_everything_else_goes(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    (tree / "venv").mkdir(parents=True)
    (tree / "venv" / "python312.dll").write_bytes(b"held")
    (tree / "venv" / "free.py").write_text("x", encoding="utf-8")
    windows_unlink(monkeypatch, locked={"python312.dll"})
    assert removal.remove_tree(tree) == [str(tree / "venv" / "python312.dll")]
    assert sorted(path.name for path in (tree / "venv").iterdir()) == ["python312.dll"]


@pytest.mark.skipif(os.name == "nt", reason="making a symlink needs a privilege on Windows")
def test_a_link_is_removed_and_its_target_left_alone(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file").write_text("x", encoding="utf-8")
    outside.chmod(0o555)
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "link").symlink_to(outside, target_is_directory=True)
    try:
        assert removal.remove_tree(tree) == []
        assert stat.S_IMODE(outside.stat().st_mode) == 0o555 and (outside / "file").exists()
    finally:
        outside.chmod(0o755)


@pytest.mark.skipif(os.name == "nt", reason="a Windows directory's mode does not refuse a delete")
def test_the_parent_of_the_tree_keeps_its_permissions(tmp_path):
    parent = tmp_path / "parent"
    tree = parent / "tree"
    tree.mkdir(parents=True)
    (tree / "file").write_text("x", encoding="utf-8")
    parent.chmod(0o555)
    try:
        assert removal.remove_tree(tree) == [str(tree)]
        assert stat.S_IMODE(parent.stat().st_mode) == 0o555
        assert tree.is_dir() and not (tree / "file").exists()
    finally:
        parent.chmod(0o755)


@pytest.mark.skipif(os.name == "nt", reason="a Windows directory's mode does not refuse a delete")
def test_a_file_root_under_a_closed_parent_is_named(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    root = parent / "file"
    root.write_text("x", encoding="utf-8")
    parent.chmod(0o555)
    try:
        assert removal.remove_tree(root) == [str(root)]
        assert stat.S_IMODE(parent.stat().st_mode) == 0o555
    finally:
        parent.chmod(0o755)


def test_a_missing_tree_is_gone(tmp_path):
    assert removal.remove_tree(tmp_path / "absent") == []

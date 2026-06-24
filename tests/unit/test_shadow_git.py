"""ShadowGitStore — independent git history for agent file changes."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from openprogram.store.shadow_git.store import ShadowGitStore, _repo_dir_for


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "my_project"
    d.mkdir()
    return d


@pytest.fixture
def shadow_root(tmp_path: Path) -> Path:
    d = tmp_path / "shadow-root"
    d.mkdir()
    return d


@pytest.fixture
def store(project_dir: Path, shadow_root: Path) -> ShadowGitStore:
    with patch("openprogram.store.shadow_git.store._shadow_root",
               return_value=shadow_root):
        return ShadowGitStore(str(project_dir))


def test_init_creates_shadow_repo(store: ShadowGitStore):
    assert store._ensure_init()
    assert (store.repo_path / ".git").exists()


def test_repo_dir_deterministic(project_dir: Path):
    a = _repo_dir_for(str(project_dir))
    b = _repo_dir_for(str(project_dir))
    assert a == b


def test_commit_turn_records_file(store: ShadowGitStore, project_dir: Path):
    f = project_dir / "hello.py"
    f.write_text("print('hello')\n")

    sha = store.commit_turn("turn_001", [str(f)], "added hello")
    assert sha is not None
    assert len(sha) == 40


def test_commit_turn_no_files_returns_none(store: ShadowGitStore):
    sha = store.commit_turn("turn_002", [])
    assert sha is None


def test_commit_turn_nonexistent_file(store: ShadowGitStore, project_dir: Path):
    sha = store.commit_turn("turn_003", [str(project_dir / "nope.py")])
    assert sha is None


def test_commit_turn_file_outside_project(
    store: ShadowGitStore, tmp_path: Path
):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    sha = store.commit_turn("turn_004", [str(outside)])
    assert sha is None


def test_diff_between_commits(store: ShadowGitStore, project_dir: Path):
    f = project_dir / "a.txt"

    f.write_text("v1\n")
    sha1 = store.commit_turn("t1", [str(f)])
    assert sha1

    f.write_text("v2\n")
    sha2 = store.commit_turn("t2", [str(f)])
    assert sha2

    d = store.diff(sha1, sha2)
    assert "-v1" in d
    assert "+v2" in d


def test_restore_file(store: ShadowGitStore, project_dir: Path, tmp_path: Path):
    f = project_dir / "restore_me.txt"
    f.write_text("original content\n")
    sha = store.commit_turn("t1", [str(f)])
    assert sha

    dest = tmp_path / "restored.txt"
    ok = store.restore_file(sha, "restore_me.txt", str(dest))
    assert ok
    assert dest.read_text() == "original content\n"


def test_restore_nonexistent_file(store: ShadowGitStore):
    store._ensure_init()
    init_sha = store._git("rev-parse", "HEAD").strip()
    ok = store.restore_file(init_sha, "nope.txt", "/tmp/nope")
    assert not ok


def test_log_returns_commits(store: ShadowGitStore, project_dir: Path):
    f = project_dir / "log_test.txt"

    f.write_text("a\n")
    store.commit_turn("t1", [str(f)], "first")

    f.write_text("b\n")
    store.commit_turn("t2", [str(f)], "second")

    entries = store.log(n=5)
    assert len(entries) >= 2
    assert entries[0]["message"].endswith("second")
    assert "sha" in entries[0]
    assert "timestamp" in entries[0]


def test_log_empty_store(store: ShadowGitStore):
    entries = store.log()
    assert len(entries) == 1  # init commit


def test_multiple_files_single_commit(
    store: ShadowGitStore, project_dir: Path
):
    a = project_dir / "a.py"
    b = project_dir / "sub" / "b.py"
    b.parent.mkdir()
    a.write_text("a\n")
    b.write_text("b\n")

    sha = store.commit_turn("t1", [str(a), str(b)])
    assert sha is not None

    entries = store.log(n=2)
    assert any("t1" in e["message"] for e in entries)


def test_deleted_file_tracked(store: ShadowGitStore, project_dir: Path):
    f = project_dir / "delete_me.txt"
    f.write_text("bye\n")
    sha1 = store.commit_turn("t1", [str(f)])
    assert sha1

    f.unlink()
    sha2 = store.commit_turn("t2", [str(f)])
    assert sha2

    d = store.diff(sha1, sha2)
    assert "delete_me.txt" in d

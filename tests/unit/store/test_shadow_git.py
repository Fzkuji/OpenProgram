"""ShadowGitStore — independent git history for agent file changes."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from openprogram.state.store.shadow_git.store import ShadowGitStore, _repo_dir_for


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
    with patch("openprogram.state.store.shadow_git.store._shadow_root",
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


def test_head_sha_returns_current_commit(store: ShadowGitStore, project_dir: Path):
    f = project_dir / "h.py"
    f.write_text("a\n")
    sha = store.commit_turn("t1", [str(f)])
    assert store.head_sha() == sha


def test_numstat_counts_added_and_removed(store: ShadowGitStore, project_dir: Path):
    f = project_dir / "n.py"
    f.write_text("one\ntwo\nthree\n")
    sha1 = store.commit_turn("t1", [str(f)])
    f.write_text("one\nTWO\nthree\nfour\n")
    sha2 = store.commit_turn("t2", [str(f)])

    stats = store.numstat(sha1, sha2)
    assert stats["n.py"] == (2, 1)


def test_diff_can_be_limited_to_one_path(store: ShadowGitStore, project_dir: Path):
    a = project_dir / "a.py"
    b = project_dir / "b.py"
    a.write_text("a1\n")
    b.write_text("b1\n")
    sha1 = store.commit_turn("t1", [str(a), str(b)])
    a.write_text("a2\n")
    b.write_text("b2\n")
    sha2 = store.commit_turn("t2", [str(a), str(b)])

    only_a = store.diff(sha1, sha2, "a.py")
    assert "a.py" in only_a
    assert "b.py" not in only_a


def test_same_size_edit_with_stale_mtime_still_commits(
        store: ShadowGitStore, project_dir: Path):
    """A same-size edit whose mtime predates the index must not be lost.

    ``seed_baseline`` / ``commit_turn`` used to mirror files with
    ``shutil.copy2``, which replays the SOURCE's mtime onto the shadow
    file. Git's index caches (size, mtime) at one-second resolution, so
    a two-byte edit landing on an mtime git had already recorded clean
    was skipped by ``git add`` — nothing staged, ``commit_turn``
    returned None, and the turn's whole diff vanished. Backdating both
    files makes the collision deterministic instead of a 1-in-N flake.
    """
    import os

    f = project_dir / "flag.py"
    backup = project_dir / "flag.py.bak"
    stale = time.time() - 10

    f.write_text("x\n")
    backup.write_text("x\n")
    os.utime(backup, (stale, stale))

    assert store.seed_baseline([(str(f), str(backup))])

    f.write_text("y\n")            # same size as the baseline
    os.utime(f, (stale, stale))    # ... and older than the index

    sha = store.commit_turn("t_same_size", [str(f)])
    assert sha, "same-size edit was dropped from the shadow repo"
    assert "+y" in store.diff(f"{sha}~1", sha)


# ---------------------------------------------------------------------------
# The shadow repository does not follow the user's Git configuration
# ---------------------------------------------------------------------------

@pytest.fixture
def windows_like_git_config(tmp_path: Path, monkeypatch) -> None:
    """The global configuration Git for Windows installs, plus signing."""
    config = tmp_path / "global.gitconfig"
    config.write_text(
        "[core]\n\tautocrlf = true\n\tsafecrlf = true\n"
        "[commit]\n\tgpgsign = true\n"
        "[gpg]\n\tprogram = /nonexistent/gpg\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))


def test_non_ascii_file_name_is_listed_as_written(store: ShadowGitStore, project_dir: Path):
    name = "笔记.md"
    f = project_dir / name
    f.write_text("一\n", encoding="utf-8")
    first = store.commit_turn("turn_cn_1", [str(f)], "add")
    f.write_text("一\n二\n", encoding="utf-8")
    second = store.commit_turn("turn_cn_2", [str(f)], "edit")

    assert [row["rel"] for row in store.name_status(first, second)] == [name]
    assert store.numstat(first, second) == {name: (1, 0)}


def test_restore_returns_the_exact_bytes(
    store: ShadowGitStore, project_dir: Path, tmp_path: Path, windows_like_git_config,
):
    payloads = {
        "crlf.txt": b"one\r\ntwo\r\n",
        "lf.txt": b"one\ntwo\n",
        "gbk.txt": "中文".encode("gbk"),
        "image.bin": b"\x89PNG\r\n\x1a\n\x00\xff",
    }
    for name, payload in payloads.items():
        (project_dir / name).write_bytes(payload)
    sha = store.commit_turn(
        "turn_bytes", [str(project_dir / name) for name in payloads], "bytes",
    )
    assert sha is not None

    for name, payload in payloads.items():
        dest = tmp_path / "restored" / name
        assert store.restore_file(sha, name, str(dest))
        assert dest.read_bytes() == payload


def test_commit_does_not_ask_for_the_users_signing_key(
    store: ShadowGitStore, project_dir: Path, windows_like_git_config,
):
    f = project_dir / "signed.txt"
    f.write_text("x\n", encoding="utf-8")
    assert store.commit_turn("turn_sign", [str(f)], "no key") is not None

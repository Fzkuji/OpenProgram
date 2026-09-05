"""Unit tests for ``openprogram.worktree.include_sync.sync_include_files``.

Covers the ``.worktreeinclude`` contract: gitignore-style pattern
matching (glob / dir / comments / blanks) via ``git ls-files
--exclude-from``, untracked-only, no-clobber, per-file failure
isolation, and the zero-manifest no-op.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from openprogram.worktree.include_sync import sync_include_files


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "tracked.txt").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "src"
    _init_repo(src)
    return src


@pytest.fixture
def dst(tmp_path):
    d = tmp_path / "wt"
    d.mkdir()
    return d


def test_no_manifest_is_noop(repo, dst):
    result = sync_include_files(str(repo), str(dst))
    assert not result.attempted
    assert result.copied == []
    assert result.failed == []


def test_basic_copy(repo, dst):
    (repo / ".worktreeinclude").write_text(".env\n")
    (repo / ".env").write_text("SECRET=1\n")

    result = sync_include_files(str(repo), str(dst))

    assert result.copied == [".env"]
    assert (dst / ".env").read_text() == "SECRET=1\n"


def test_glob_pattern(repo, dst):
    (repo / ".worktreeinclude").write_text("*.local.json\n")
    (repo / "a.local.json").write_text("{}")
    (repo / "b.local.json").write_text("{}")
    (repo / "c.json").write_text("{}")  # should not match

    result = sync_include_files(str(repo), str(dst))

    assert sorted(result.copied) == ["a.local.json", "b.local.json"]
    assert not (dst / "c.json").exists()


def test_directory_pattern(repo, dst):
    (repo / ".worktreeinclude").write_text("certs/\n")
    certs = repo / "certs"
    certs.mkdir()
    (certs / "a.pem").write_text("A")
    (certs / "b.pem").write_text("B")

    result = sync_include_files(str(repo), str(dst))

    assert sorted(result.copied) == ["certs/a.pem", "certs/b.pem"]
    assert (dst / "certs" / "a.pem").read_text() == "A"


def test_comments_and_blank_lines_ignored(repo, dst):
    (repo / ".worktreeinclude").write_text(
        "# local secrets\n\n.env\n\n# trailing comment\n"
    )
    (repo / ".env").write_text("X")

    result = sync_include_files(str(repo), str(dst))

    assert result.copied == [".env"]


def test_does_not_overwrite_existing_destination(repo, dst):
    (repo / ".worktreeinclude").write_text(".env\n")
    (repo / ".env").write_text("SOURCE")
    (dst / ".env").write_text("ALREADY-THERE")

    result = sync_include_files(str(repo), str(dst))

    assert result.copied == []
    assert result.skipped_existing == [".env"]
    assert (dst / ".env").read_text() == "ALREADY-THERE"


def test_tracked_files_are_never_copied(repo, dst):
    # tracked.txt is committed in the fixture; even if a pattern would
    # match it, it must never come back from `git ls-files --others`.
    (repo / ".worktreeinclude").write_text("*.txt\n")

    result = sync_include_files(str(repo), str(dst))

    assert result.copied == []
    assert not (dst / "tracked.txt").exists()


def test_source_repo_not_a_git_repo_reports_failure_without_raising(tmp_path, dst):
    # A directory that has a .worktreeinclude but isn't a git repo at
    # all (e.g. corrupted / mid-teardown) — `git ls-files` fails;
    # that's recorded as a failure rather than raising.
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    (not_a_repo / ".worktreeinclude").write_text(".env\n")
    (not_a_repo / ".env").write_text("X")

    result = sync_include_files(str(not_a_repo), str(dst))

    assert result.copied == []
    assert result.failed
    assert result.failed[0][0] == ".worktreeinclude"


def test_single_file_failure_does_not_abort_the_rest(repo, dst, monkeypatch):
    (repo / ".worktreeinclude").write_text("*.env\n")
    (repo / "a.env").write_text("A")
    (repo / "b.env").write_text("B")

    # Make the destination directory for "a.env" unwritable-by-mkdir:
    # simplest deterministic failure is to pre-create a *file* where a
    # subdirectory would need to go, forcing the second copy's parent
    # mkdir to fail while the sibling copy still succeeds.
    blocker_repo = repo / "blocked"
    blocker_repo.mkdir()
    (blocker_repo / "a.env").write_text("A")
    (repo / ".worktreeinclude").write_text("*.env\nblocked/*.env\n")
    # Put a plain file at dst/blocked so dst/blocked/a.env's parent
    # mkdir raises NotADirectoryError.
    (dst / "blocked").write_text("i am a file, not a dir")

    result = sync_include_files(str(repo), str(dst))

    assert "a.env" in result.copied
    assert "b.env" in result.copied
    assert any(p == "blocked/a.env" for p, _reason in result.failed)


def test_symlinks_are_copied_as_links(repo, dst):
    (repo / ".worktreeinclude").write_text("link.env\n")
    (repo / "link.env").symlink_to("tracked.txt")

    result = sync_include_files(str(repo), str(dst))

    assert result.copied == ["link.env"]
    copied = dst / "link.env"
    assert copied.is_symlink()
    assert os.readlink(copied) == "tracked.txt"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows filesystems do not expose POSIX executable mode bits",
)
def test_permissions_preserved(repo, dst):
    (repo / ".worktreeinclude").write_text("script.sh\n")
    script = repo / "script.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o755)

    result = sync_include_files(str(repo), str(dst))

    assert result.copied == ["script.sh"]
    assert (dst / "script.sh").stat().st_mode & 0o777 == 0o755

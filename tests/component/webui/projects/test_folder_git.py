"""Per-folder git pills: status, branch switch, worktree, PR guard rails."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from openprogram.state.worktree import folder_git


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
    ).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "a.txt").write_text("one\ntwo\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "init")
    return root


def test_non_repo_folder_reports_not_a_repo(tmp_path: Path) -> None:
    assert folder_git.folder_status(str(tmp_path))["is_repo"] is False
    assert folder_git.folder_status("relative/path")["is_repo"] is False


def test_status_counts_tracked_lines_and_untracked_files(repo: Path) -> None:
    (repo / "a.txt").write_text("one\nTWO\nthree\n")
    (repo / "new.txt").write_text("x\n")
    (repo / "blob.bin").write_bytes(b"\0\1\2")
    status = folder_git.folder_status(str(repo), include_branches=True)
    assert status["branch"] == "main"
    assert status["changes"] == {
        "files": 3, "untracked": 2, "conflicts": 0, "insertions": 3, "deletions": 1,
    }
    assert status["branches"] == ["main"]
    assert status["is_worktree"] is False
    assert status["has_remote"] is False


def test_subfolder_reports_its_repository(repo: Path) -> None:
    sub = repo / "pkg"
    sub.mkdir()
    status = folder_git.folder_status(str(sub))
    assert status["root"] == str(repo.resolve()) or Path(status["root"]).resolve() == repo.resolve()


def test_switch_branch_creates_and_switches(repo: Path) -> None:
    status = folder_git.switch_branch(str(repo), "feature/x", create=True)
    assert status["branch"] == "feature/x"
    status = folder_git.switch_branch(str(repo), "main")
    assert status["branch"] == "main"
    with pytest.raises(folder_git.FolderGitError):
        folder_git.switch_branch(str(repo), "does-not-exist")
    with pytest.raises(folder_git.FolderGitError):
        folder_git.switch_branch(str(repo), "-bad", create=True)


def _diverge(repo: Path) -> None:
    """main and feature/x disagree on a.txt; the working tree edits it too."""
    _git(repo, "switch", "-q", "-c", "feature/x")
    (repo / "a.txt").write_text("ONE\ntwo\n")
    _git(repo, "commit", "-q", "-am", "feature edit")
    (repo / "a.txt").write_text("ONE\ntwo\nlocal\n")
    (repo / "notes.txt").write_text("untracked\n")


def test_switch_refusal_lists_the_files_it_would_overwrite(repo: Path) -> None:
    _diverge(repo)
    with pytest.raises(folder_git.FolderGitError) as info:
        folder_git.switch_branch(str(repo), "main")
    err = info.value
    assert err.code == "overwrite"
    assert err.files == ["a.txt"]
    assert "would be overwritten" in (err.detail or "")
    assert err.as_dict()["files"] == ["a.txt"]
    # nothing moved: still on the feature branch with the edit in place
    assert folder_git.folder_status(str(repo))["branch"] == "feature/x"
    assert (repo / "a.txt").read_text().endswith("local\n")


def test_switch_with_carry_moves_the_changes_over(repo: Path) -> None:
    _diverge(repo)
    status = folder_git.switch_branch(str(repo), "main", carry=True)
    assert status["branch"] == "main"
    # a three-way merge: main's first line, plus the carried last line
    assert (repo / "a.txt").read_text() == "one\ntwo\nlocal\n"
    assert (repo / "notes.txt").read_text() == "untracked\n"
    assert _git(repo, "stash", "list") == ""


def test_switch_with_carry_keeps_the_stash_on_conflict(repo: Path) -> None:
    _diverge(repo)
    _git(repo, "stash", "push", "-q", "--include-untracked")
    _git(repo, "switch", "-q", "main")
    (repo / "a.txt").write_text("one\ntwo\nmain-local\n")
    _git(repo, "commit", "-q", "-am", "main edit")
    _git(repo, "switch", "-q", "feature/x")
    _git(repo, "stash", "pop", "-q")
    # a.txt now conflicts between the carried edit and main's commit
    with pytest.raises(folder_git.FolderGitError) as info:
        folder_git.switch_branch(str(repo), "main", carry=True)
    assert info.value.code == "stash_conflict"
    assert "stash@{0}" in str(info.value)
    assert folder_git.folder_status(str(repo))["branch"] == "main"
    assert "carry to main" in _git(repo, "stash", "list")


def test_create_worktree_lands_beside_the_repo(repo: Path) -> None:
    (repo / "a.txt").write_text("dirty\n")
    wt = folder_git.create_worktree(str(repo), "feat/wt")
    target = Path(wt["path"])
    assert target.parent == repo.parent / "proj-worktrees"
    assert wt["branch"] == "feat/wt"
    assert wt["is_worktree"] is True
    # The source checkout keeps its uncommitted change; the worktree is clean.
    assert (repo / "a.txt").read_text() == "dirty\n"
    assert (target / "a.txt").read_text() == "one\ntwo\n"
    with pytest.raises(folder_git.FolderGitError, match="already checked out"):
        folder_git.create_worktree(str(repo), "feat/wt")


def test_create_pr_refuses_without_remote_or_on_base(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(folder_git.shutil, "which", lambda name: None)
    with pytest.raises(folder_git.FolderGitError, match="GitHub CLI"):
        folder_git.create_pull_request(str(repo))
    monkeypatch.setattr(folder_git.shutil, "which", lambda name: "/usr/bin/false")
    with pytest.raises(folder_git.FolderGitError, match="origin"):
        folder_git.create_pull_request(str(repo))


class _WS:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))


def test_ws_actions_echo_path_and_report_errors(repo: Path) -> None:
    from openprogram.interfaces.server.ws_actions import folder_git as actions

    ws = _WS()
    asyncio.run(actions.handle_git_folder_status(ws, {"path": str(repo)}))
    asyncio.run(actions.handle_git_switch_branch(ws, {"path": str(repo), "branch": "nope"}))
    status, switch = ws.frames
    assert status["type"] == "git_folder_status"
    assert status["data"]["path"] == str(repo)
    assert status["data"]["status"]["branch"] == "main"
    assert switch["type"] == "git_switch_branch_result"
    assert switch["data"]["ok"] is False and switch["data"]["error"]


def test_pr_checks_summary_counts_runs_and_statuses() -> None:
    rollup = [
        {"__typename": "CheckRun", "name": "unit", "status": "COMPLETED", "conclusion": "SUCCESS", "detailsUrl": "u1"},
        {"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "FAILURE", "detailsUrl": "u2"},
        {"__typename": "CheckRun", "name": "e2e", "status": "IN_PROGRESS", "conclusion": "", "detailsUrl": "u3"},
        {"__typename": "CheckRun", "name": "docs", "status": "COMPLETED", "conclusion": "SKIPPED"},
        {"__typename": "StatusContext", "context": "ci/legacy", "state": "ERROR", "targetUrl": "u4"},
        {"__typename": "StatusContext", "context": "ci/queued", "state": "PENDING"},
    ]
    assert folder_git._checks_summary(rollup) == {
        "total": 6, "passed": 2, "pending": 2, "failed_count": 2,
        "failed": [{"name": "lint", "url": "u2"}, {"name": "ci/legacy", "url": "u4"}],
    }
    assert folder_git._checks_summary([]) is None
    assert folder_git._checks_summary(None) is None


def test_open_pr_reports_checks_from_gh(repo: Path, monkeypatch) -> None:
    import types
    payload = {"number": 7, "url": "https://example.test/pr/7", "state": "OPEN", "title": "T", "isDraft": False,
               "statusCheckRollup": [{"__typename": "CheckRun", "name": "unit", "status": "COMPLETED", "conclusion": "FAILURE"}]}
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(folder_git.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(folder_git.subprocess, "run", fake_run)
    pr = folder_git._open_pr(str(repo), "feature")
    assert "statusCheckRollup" in seen["argv"][-1]
    assert pr["number"] == 7 and pr["checks"]["failed"] == [{"name": "unit", "url": None}]

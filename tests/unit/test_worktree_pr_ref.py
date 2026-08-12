"""Unit tests for ``openprogram.worktree.pr_ref`` — PR reference parsing
and the same-repo / fork fetch paths (``gh``/``git`` calls mocked)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.worktree.pr_ref import (
    PrRefError,
    fetch_pr_branch,
    fetch_pr_info,
    parse_pr_ref,
)


# parse_pr_ref — three accepted input shapes


def test_parse_pr_ref_plain_number():
    assert parse_pr_ref("123") == 123


def test_parse_pr_ref_hash_prefixed():
    assert parse_pr_ref("#123") == 123


def test_parse_pr_ref_github_url():
    assert parse_pr_ref("https://github.com/owner/repo/pull/456") == 456


def test_parse_pr_ref_github_url_with_trailing_segment():
    assert parse_pr_ref("https://github.com/owner/repo/pull/456/files") == 456


def test_parse_pr_ref_rejects_garbage():
    with pytest.raises(PrRefError, match="invalid_pr_ref"):
        parse_pr_ref("not-a-pr")


def test_parse_pr_ref_rejects_empty():
    with pytest.raises(PrRefError):
        parse_pr_ref("")


# gh availability


def test_fetch_pr_info_errors_when_gh_missing(monkeypatch):
    monkeypatch.setattr("openprogram.worktree.pr_ref.shutil.which", lambda name: None)
    with pytest.raises(PrRefError, match="gh_not_found"):
        fetch_pr_info(1, cwd="/tmp")


def test_fetch_pr_info_errors_when_gh_unauthenticated(monkeypatch):
    monkeypatch.setattr("openprogram.worktree.pr_ref.shutil.which", lambda name: "/usr/bin/gh")

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="run `gh auth login` to authenticate")

    monkeypatch.setattr("openprogram.worktree.pr_ref.subprocess.run", fake_run)
    with pytest.raises(PrRefError, match="gh_not_authenticated"):
        fetch_pr_info(1, cwd="/tmp")


# same-repo vs fork fetch paths


def _mock_gh_pr_view(monkeypatch, *, head_ref, is_cross_repo, owner=None):
    import json as _json

    monkeypatch.setattr("openprogram.worktree.pr_ref.shutil.which", lambda name: "/usr/bin/gh")

    def fake_run(argv, **kwargs):
        if argv[:2] == ["/usr/bin/gh", "pr"]:
            payload = {
                "headRefName": head_ref,
                "isCrossRepository": is_cross_repo,
                "headRepositoryOwner": {"login": owner} if owner else {},
            }
            return SimpleNamespace(returncode=0, stdout=_json.dumps(payload), stderr="")
        raise AssertionError(f"unexpected subprocess.run call: {argv}")

    monkeypatch.setattr("openprogram.worktree.pr_ref.subprocess.run", fake_run)


def test_fetch_pr_branch_same_repo_uses_head_ref_name(monkeypatch):
    _mock_gh_pr_view(monkeypatch, head_ref="feature-x", is_cross_repo=False)
    seen = {}

    def fake_run_git(*args, cwd):
        seen["args"] = args
        seen["cwd"] = cwd
        return 0, "", ""

    monkeypatch.setattr("openprogram.worktree.pr_ref._run_git", fake_run_git)
    info = fetch_pr_branch(42, source_repo="/repo", local_branch="local-pr-42")
    assert info.number == 42
    assert info.is_cross_repository is False
    assert seen["args"] == ("fetch", "origin", "feature-x:local-pr-42")


def test_fetch_pr_branch_fork_uses_pull_head_refspec(monkeypatch):
    _mock_gh_pr_view(monkeypatch, head_ref="feature-y", is_cross_repo=True, owner="contributor")
    seen = {}

    def fake_run_git(*args, cwd):
        seen["args"] = args
        return 0, "", ""

    monkeypatch.setattr("openprogram.worktree.pr_ref._run_git", fake_run_git)
    info = fetch_pr_branch(99, source_repo="/repo", local_branch="local-pr-99")
    assert info.is_cross_repository is True
    assert info.head_owner == "contributor"
    assert seen["args"] == ("fetch", "origin", "pull/99/head:local-pr-99")


def test_fetch_pr_branch_raises_on_git_fetch_failure(monkeypatch):
    _mock_gh_pr_view(monkeypatch, head_ref="feature-x", is_cross_repo=False)
    monkeypatch.setattr(
        "openprogram.worktree.pr_ref._run_git",
        lambda *a, cwd: (1, "", "fatal: couldn't find remote ref feature-x"),
    )
    with pytest.raises(PrRefError, match="git fetch failed"):
        fetch_pr_branch(42, source_repo="/repo", local_branch="local-pr-42")

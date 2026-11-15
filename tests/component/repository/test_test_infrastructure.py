from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]


def test_child_pytest_configuration_removes_temporary_home() -> None:
    env = os.environ.copy()
    env.pop("OPENPROGRAM_TEST_REAL_HOME", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, runpy; "
                "runpy.run_path('tests/conftest.py'); "
                "print(os.environ['HOME'])"
            ),
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    child_home = Path(result.stdout.strip().splitlines()[-1])

    assert child_home.name.startswith("openprogram-test-home-")
    assert not child_home.exists()


def test_child_pytest_configuration_drops_an_inherited_git_location() -> None:
    """A run started from a git hook carries GIT_DIR for the real checkout;
    every ``git`` a test ran in its own repository would act on the checkout."""
    env = os.environ.copy() | {"GIT_DIR": str(ROOT / ".git"), "GIT_WORK_TREE": str(ROOT)}
    result = subprocess.run(
        [sys.executable, "-c",
         "import os, runpy; runpy.run_path('tests/conftest.py'); "
         "print(os.environ.get('GIT_DIR'), os.environ.get('GIT_WORK_TREE'))"],
        cwd=ROOT, env=env, check=True, capture_output=True, text=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "None None"


def _guard(tmp_path: Path):
    from tests.support import host_guard

    repo = tmp_path / "checkout"
    (repo / ".git").mkdir(parents=True)
    home = tmp_path / "person"
    (home / ".openprogram-dev").mkdir(parents=True)
    guard = host_guard.Guard([repo], repo / ".git", home, tmp_path / "test-home", guard_state=True)
    return host_guard, guard, repo, home


def test_the_guard_refuses_changes_to_the_checkout_and_allows_reads(tmp_path: Path) -> None:
    host_guard, guard, repo, _home = _guard(tmp_path)
    nested = repo / "sub" / "dir"
    nested.mkdir(parents=True)

    for argv, cwd in (
        (["git", "worktree", "add", "/elsewhere/wt", "-b", "op/wt/x"], str(nested)),
        (["git", "-C", str(repo), "config", "core.autocrlf", "true"], "/"),
        (["git", "init", "-q", "--bare", str(repo / ".git")], "/"),
        (["git", "--git-dir", str(repo / ".git"), "branch", "op/wt/y"], "/"),
    ):
        with pytest.raises(host_guard.RealStateWrite):
            guard("subprocess.Popen", ("git", argv, cwd, None))
    assert len(guard.take()) == 4

    for argv, cwd in (
        (["git", "rev-parse", "--show-toplevel"], str(repo)),
        (["git", "branch", "--list", "op/wt/*"], str(repo)),
        (["git", "config", "--get", "core.bare"], str(repo)),
        (["git", "worktree", "list"], str(repo)),
        (["git", "init", "-q", str(tmp_path / "own")], str(repo)),
    ):
        guard("subprocess.Popen", ("git", argv, cwd, None))
    assert guard.take() == []


def test_the_guard_refuses_writes_to_the_persons_state(tmp_path: Path) -> None:
    host_guard, guard, repo, home = _guard(tmp_path)
    log = str(home / ".openprogram" / "logs" / "events.jsonl")

    with pytest.raises(host_guard.RealStateWrite):
        guard("open", (log, "a", 0))
    with pytest.raises(host_guard.RealStateWrite):
        guard("os.mkdir", (str(home / ".openprogram-dev" / "x"), 0o777, None))
    with pytest.raises(host_guard.RealStateWrite):
        guard("subprocess.Popen", ("python", ["python", "-m", "openprogram", "worker"], None, {"PATH": "/bin"}))
    assert len(guard.take()) == 3

    guard("open", (log, "r", 0))
    guard("os.remove", (".git", 5))  # relative to an open directory, not the checkout
    guard("open", (str(home / ".openprogramme"), "w", 0))
    assert guard.take() == []

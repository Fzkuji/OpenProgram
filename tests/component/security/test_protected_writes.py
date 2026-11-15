"""Files run outside the sandbox stay read-only inside it (host.sandbox.protected_writes)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from openprogram.host import sandbox
from openprogram.host.sandbox import SandboxPolicy, _bwrap_args, protected_writes


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home with a state folder, a login agent folder and a repository."""
    root = tmp_path / "home"
    (root / ".openprogram" / "worktrees" / "w1").mkdir(parents=True)
    (root / "Library" / "LaunchAgents").mkdir(parents=True)
    (root / ".zshrc").write_text("# rc\n")
    repo = root / "project"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("OPENPROGRAM_HOME", str(root / ".openprogram"))
    return root.resolve()


def _policy() -> SandboxPolicy:
    return SandboxPolicy()


def test_direct_writes_refuse_startup_files_state_and_repository_git(home):
    roots = [str(home)]
    assert protected_writes.denial(str(home / ".zshrc"), str(home), roots)
    assert protected_writes.denial(str(home / ".openprogram" / "config.json"), str(home), roots)
    assert protected_writes.denial(str(home / "Library" / "LaunchAgents" / "x.plist"), str(home), roots)
    repo = home / "project"
    assert protected_writes.denial(str(repo / ".git" / "hooks" / "pre-commit"), str(repo), [str(repo)])
    assert protected_writes.denial(str(repo / ".git" / "config"), str(repo), [str(repo)])
    assert protected_writes.denial(str(repo / ".vscode" / "tasks.json"), str(repo), [str(repo)])
    assert protected_writes.denial(str(repo / "src" / "app.py"), str(repo), [str(repo)]) is None
    assert protected_writes.denial(str(repo / ".git" / "HEAD"), str(repo), [str(repo)]) is None


def test_a_worktree_inside_the_state_folder_stays_writable_but_keeps_name_rules(home):
    worktree = str(home / ".openprogram" / "worktrees" / "w1")
    assert protected_writes.denial(os.path.join(worktree, "main.py"), worktree, [worktree]) is None
    assert protected_writes.denial(os.path.join(worktree, ".bashrc"), worktree, [worktree])
    sibling = str(home / ".openprogram" / "worktrees" / "other.txt")
    assert protected_writes.denial(sibling, worktree, [worktree])


def test_escalation_lifts_the_rules():
    assert protected_writes.lifted(("/",))
    assert not protected_writes.lifted(())


def test_bwrap_mounts_protected_paths_read_only_and_reopens_nested_roots(home):
    worktree = str(home / ".openprogram" / "worktrees" / "w1")
    args = _bwrap_args("true", worktree, _policy())
    state = str(home / ".openprogram")
    tree_at = args.index(state)
    assert args[tree_at - 1] == "--ro-bind"
    rebinds = [i for i, value in enumerate(args) if value == worktree and args[i - 1] == "--bind"]
    assert any(i > tree_at for i in rebinds), "the worktree is bound again after its state folder"
    rc = str(home / ".zshrc")
    assert args[args.index(rc) - 1] == "--ro-bind"
    repo = home / "project"
    repo_args = _bwrap_args("true", str(repo), _policy())
    assert str(repo / ".git" / "hooks") in repo_args


@pytest.mark.skipif(sys.platform != "darwin" or not os.path.exists("/usr/bin/sandbox-exec"),
                    reason="runs the real macOS sandbox")
def test_seatbelt_blocks_the_escapes_and_keeps_git_working(home):
    repo = str(home / "project")

    def run(command: str, cwd: str) -> int:
        args, _ = sandbox.wrap_command(command, cwd, _policy())
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True).returncode

    assert run(f"echo x >> '{home}/.zshrc'", str(home)) != 0
    assert run(f"echo x > '{home}/.openprogram/config.json'", str(home)) != 0
    assert run(f"echo x > '{home}/Library/LaunchAgents/a.plist'", str(home)) != 0
    assert run(f"echo x > '{home}/notes.txt'", str(home)) == 0
    assert run("echo x > .git/hooks/pre-commit", repo) != 0
    assert run("git config user.name probe", repo) != 0
    assert run("git -c user.email=a@b -c user.name=a commit -q --allow-empty -m t", repo) == 0
    assert run("git init -q nested", repo) == 0
    worktree = str(home / ".openprogram" / "worktrees" / "w1")
    assert run("echo x > main.py", worktree) == 0
    assert run("echo x > .bashrc", worktree) != 0

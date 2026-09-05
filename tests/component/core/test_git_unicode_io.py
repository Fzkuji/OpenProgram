"""Git machine output is UTF-8 regardless of the Windows console code page."""
import locale
import subprocess

from openprogram._compat import no_window_creation_flags
from openprogram.worktree.manager import _run_git
from openprogram.programs.tools.system.self_update import _git


def test_worktree_and_update_git_preserve_unicode_paths(tmp_path, monkeypatch):
    root = tmp_path / "中文 project"
    root.mkdir()
    monkeypatch.setattr(locale, "getencoding", lambda: "cp1252")
    original_run = subprocess.run

    def checked_run(*args, **kwargs):
        assert kwargs.get("encoding") == "utf-8"
        assert kwargs.get("creationflags") == no_window_creation_flags()
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", checked_run)

    def run(*args):
        code, output, error = _run_git(*args, cwd=str(root))
        assert code == 0, error
        return output

    run("init")
    run("config", "user.name", "Tests")
    run("config", "user.email", "tests@example.invalid")
    run("config", "commit.gpgsign", "false")
    name = "功能 文件.txt"
    (root / name).write_text("contents\n", encoding="utf-8")
    run("add", "--", name)
    run("commit", "-m", "fixture")
    assert run("ls-files", "-z") == name + "\0"
    assert _git(root, "ls-files", "-z") == name + "\0"
    assert _git(root, "rev-parse", "--show-toplevel") == root.resolve().as_posix()

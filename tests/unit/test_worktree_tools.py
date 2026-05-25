"""Unit tests for the worktree-aware behaviour of bash / edit / write /
read tools.

The actual decorator binding is exercised by importing the underlying
function bodies directly — bypassing @function's AgentTool wrapper —
because the wrapper expects an Agent runtime + a tool_call envelope.
What we care about here is that the path / cwd resolution helpers
work right when the ``_current_worktree_path`` ContextVar is set.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from openprogram.worktree.context import (
    clear_worktree,
    current_worktree_path,
    set_worktree,
)
from openprogram.worktree.path_resolve import resolve_path


@pytest.fixture
def worktree_root(tmp_path):
    root = tmp_path / "wt-root"
    root.mkdir()
    (root / "inside.txt").write_text("hello\n")
    token = set_worktree(str(root))
    yield root
    try:
        from openprogram.worktree.context import reset_worktree
        reset_worktree(token)
    except Exception:
        clear_worktree()


def test_resolve_relative_path_uses_worktree(worktree_root):
    p, warn = resolve_path("inside.txt")
    assert p == os.path.join(str(worktree_root), "inside.txt")
    assert warn is None


def test_resolve_absolute_inside_worktree_no_warning(worktree_root):
    abs_path = str(worktree_root / "inside.txt")
    p, warn = resolve_path(abs_path)
    assert p == abs_path
    assert warn is None


def test_resolve_absolute_outside_worktree_warns(worktree_root, tmp_path):
    other = tmp_path / "other.txt"
    other.write_text("x")
    p, warn = resolve_path(str(other))
    assert p == str(other)
    assert warn is not None
    assert "outside worktree" in warn


def test_resolve_relative_escaping_worktree_warns(worktree_root):
    p, warn = resolve_path("../escaped.txt")
    # Path is still resolved (warn-not-block)
    assert "../escaped.txt" not in p
    # But the result lives outside the worktree.
    assert warn is not None
    assert "outside worktree" in warn


def test_resolve_no_worktree_no_change():
    clear_worktree()
    p, warn = resolve_path("/some/abs/path")
    assert p == "/some/abs/path"
    assert warn is None
    p, warn = resolve_path("rel/path")
    assert p == "rel/path"
    assert warn is None


def _exec_tool(tool, **args) -> str:
    """Drive an AgentTool through its async ``execute`` path and
    flatten the TextContent results to a single string. Useful so
    tests don't have to know AgentTool internals."""
    import asyncio
    res = asyncio.run(tool.execute("test-call", args, None, None))
    parts: list[str] = []
    for block in (res.content or []):
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "\n".join(parts)


def test_bash_uses_worktree_cwd(worktree_root, monkeypatch):
    """When the worktree ContextVar is set, bash's backend.run is
    called with cwd=<worktree_path>."""
    # Force-load the inner submodule and grab it via sys.modules —
    # the parent package's __init__.py re-exports the AgentTool under
    # the same attribute name (bash.bash), so a plain import returns
    # the wrapper, not the file module.
    import sys
    import openprogram.functions.tools.bash.bash  # noqa: F401
    bash_inner = sys.modules["openprogram.functions.tools.bash.bash"]

    seen_kwargs: dict = {}

    class FakeBackend:
        backend_id = "local"

        def run(self, command, timeout, cwd=None):
            seen_kwargs["cwd"] = cwd
            from openprogram.backend.base import RunResult
            return RunResult(0, f"cwd={cwd}", "")

    monkeypatch.setattr(
        bash_inner, "get_active_backend", lambda: FakeBackend()
    )
    from openprogram.functions.tools.bash import bash as bash_tool
    out = _exec_tool(bash_tool, command="pwd")
    assert seen_kwargs["cwd"] == str(worktree_root)
    assert "exit_code=0" in out


def test_write_resolves_relative_inside_worktree(worktree_root):
    from openprogram.functions.tools.write import write as write_tool
    out = _exec_tool(write_tool, file_path="new.txt", content="abc")
    assert "Wrote 3 bytes" in out
    target = worktree_root / "new.txt"
    assert target.exists()
    assert target.read_text() == "abc"


def test_read_resolves_relative_inside_worktree(worktree_root):
    from openprogram.functions.tools.read import read as read_tool
    out = _exec_tool(read_tool, file_path="inside.txt")
    assert "hello" in out
    assert str(worktree_root) in out  # header echoes resolved abs path


def test_edit_resolves_relative_inside_worktree(worktree_root):
    from openprogram.functions.tools.edit import edit as edit_tool
    out = _exec_tool(
        edit_tool, file_path="inside.txt",
        old_string="hello", new_string="world",
    )
    assert "1 replacement" in out
    assert (worktree_root / "inside.txt").read_text() == "world\n"


def test_write_outside_worktree_warns(worktree_root, tmp_path):
    from openprogram.functions.tools.write import write as write_tool
    target = tmp_path / "elsewhere.txt"
    out = _exec_tool(write_tool, file_path=str(target), content="x")
    assert "[outside worktree]" in out
    # The write still happens (warn-not-block).
    assert target.exists()
    assert target.read_text() == "x"


def test_clear_worktree_returns_to_default():
    set_worktree("/tmp/some-wt")
    assert current_worktree_path() == "/tmp/some-wt"
    clear_worktree()
    assert current_worktree_path() is None

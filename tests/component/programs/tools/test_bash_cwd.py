"""Regression coverage for the Bash working-directory contract (issue #43)."""
from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
from types import ModuleType

import pytest

from openprogram.backend.base import RunResult
from openprogram.worktree.context import (
    current_worktree_path,
    reset_worktree,
    set_worktree,
)


@pytest.fixture
def bash_module() -> ModuleType:
    # The package re-exports the AgentTool as `bash`; import the module itself.
    return importlib.import_module("openprogram.programs.tools.files.bash.bash")


def _execute(bash_module: ModuleType, command: str) -> str:
    result = asyncio.run(bash_module.bash.execute(
        "cwd-contract", {"command": command, "timeout": 10_000}, None, None,
    ))
    text = "\n".join(
        block.text for block in result.content if hasattr(block, "text")
    )
    assert not result.is_error, text
    assert text.startswith("exit_code=0"), text
    return text


def test_bash_description_exposes_the_per_call_directory_contract(bash_module):
    from openprogram.programs.tools.files.bash.prompt import DESCRIPTION

    # Assert what the registered tool actually exposes, not just a source comment.
    assert bash_module.bash.description == DESCRIPTION
    assert "new shell subprocess" in DESCRIPTION
    assert "currently bound agent worktree directory" in DESCRIPTION
    assert "backend's default working directory" in DESCRIPTION
    assert "do not persist between calls" in DESCRIPTION
    assert "same call as the commands" in DESCRIPTION
    assert "working directory persists between commands" not in DESCRIPTION


@pytest.mark.parametrize("initial", [None, "worktree-a"])
def test_bash_resolves_the_current_worktree_on_every_call(
    bash_module, monkeypatch, tmp_path, initial,
):
    seen: list[str | None] = []

    class RecordingBackend:
        backend_id = "local"

        def run(self, command, timeout, cwd=None):
            seen.append(cwd)
            return RunResult(0, "", "")

    backend = RecordingBackend()
    monkeypatch.setattr(bash_module, "get_active_backend", lambda: backend)
    first = str(tmp_path / initial) if initial is not None else None
    second = str(tmp_path / "worktree-b")
    token = set_worktree(first)
    try:
        for command in ("pwd", "cd child", "pwd"):
            _execute(bash_module, command)
        assert current_worktree_path() == first
        other = set_worktree(second)
        try:
            _execute(bash_module, "pwd")
        finally:
            reset_worktree(other)
        _execute(bash_module, "pwd")
    finally:
        reset_worktree(token)
    assert seen == [first, first, first, second, first]


@pytest.mark.skipif(os.name == "nt", reason="exercises POSIX host-shell syntax")
@pytest.mark.parametrize("bound", [True, False], ids=["worktree", "backend-default"])
def test_local_bash_cd_and_exports_are_scoped_to_one_call(
    bash_module, monkeypatch, tmp_path, bound,
):
    from openprogram import sandbox
    from openprogram.backend.local import LocalBackend

    root = tmp_path / "workspace with spaces"
    child = root / "child with spaces"
    child.mkdir(parents=True)
    (root / "marker.txt").write_text("root-marker", encoding="utf-8")
    (child / "marker.txt").write_text("child-marker", encoding="utf-8")
    monkeypatch.chdir(tmp_path if bound else root)
    parent_cwd = Path.cwd()
    monkeypatch.setenv("OPENPROGRAM_CWD_CONTRACT_TEST", "parent-value")
    # Only this test's sandbox configuration is replaced. Execute through the
    # real AgentTool, LocalBackend and subprocess runner, with no model/network.
    monkeypatch.setattr(sandbox, "resolve_policy", lambda: None)
    backend = LocalBackend()
    monkeypatch.setattr(bash_module, "get_active_backend", lambda: backend)
    token = set_worktree(str(root) if bound else None)
    try:
        assert _execute(bash_module, "cat marker.txt").endswith("root-marker")
        _execute(bash_module, 'cd "child with spaces"')
        assert _execute(bash_module, "cat marker.txt").endswith("root-marker")
        assert _execute(
            bash_module, 'cd "child with spaces" && cat marker.txt',
        ).endswith("child-marker")
        assert _execute(bash_module, "cat marker.txt").endswith("root-marker")
        assert _execute(
            bash_module,
            'export OPENPROGRAM_CWD_CONTRACT_TEST=child-value; '
            'printf "%s" "$OPENPROGRAM_CWD_CONTRACT_TEST"',
        ).endswith("child-value")
        assert _execute(
            bash_module, 'printf "%s" "$OPENPROGRAM_CWD_CONTRACT_TEST"',
        ).endswith("parent-value")
        # Shell state is isolated, but ordinary filesystem writes still persist.
        _execute(bash_module, 'cd "child with spaces" && printf saved > saved.txt')
        assert _execute(
            bash_module, 'cat "child with spaces/saved.txt"',
        ).endswith("saved")
        assert current_worktree_path() == (str(root) if bound else None)
        assert Path.cwd() == parent_cwd
        assert os.environ["OPENPROGRAM_CWD_CONTRACT_TEST"] == "parent-value"
    finally:
        reset_worktree(token)

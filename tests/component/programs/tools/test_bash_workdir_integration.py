"""Concurrency and native-sandbox checks for explicit one-shot workdirs."""
from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import sys
import threading

import pytest

from openprogram import sandbox
from openprogram.backend.base import RunResult
from openprogram.worktree.context import current_worktree_path, reset_worktree, set_worktree


def _text(result):
    return "\n".join(block.text for block in result.content if hasattr(block, "text"))


def test_parallel_agent_contexts_keep_independent_workdirs(tmp_path, monkeypatch):
    module = importlib.import_module("openprogram.programs.tools.files.bash.bash")
    monkeypatch.setattr(sandbox, "resolve_policy", lambda: None)
    barrier = threading.Barrier(2)

    class ParallelBackend:
        backend_id = "local"

        def run(self, command, timeout, cwd=None):
            barrier.wait(timeout=10)
            return RunResult(0, str(cwd), "")

    monkeypatch.setattr(module, "get_active_backend", lambda: ParallelBackend())
    original_cwd, original_worktree = Path.cwd(), current_worktree_path()
    roots = [tmp_path / "agent-a", tmp_path / "agent-b"]
    for root in roots:
        (root / "child").mkdir(parents=True)

    async def run(root):
        token = set_worktree(str(root))
        try:
            result = await module.bash.execute(
                root.name, {"command": "pwd", "workdir": "child"}, None, None,
            )
            assert not result.is_error, _text(result)
            assert _text(result).endswith(str((root / "child").resolve()))
            assert current_worktree_path() == str(root)
        finally:
            reset_worktree(token)

    async def batch():
        await asyncio.gather(*(run(root) for root in roots))

    asyncio.run(batch())
    assert current_worktree_path() == original_worktree
    assert Path.cwd() == original_cwd


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native bubblewrap regression")
@pytest.mark.parametrize("swap_after_validation", [False, True], ids=["stable-directory", "symlink-swap"])
def test_native_sandbox_preserves_original_root(tmp_path, monkeypatch, swap_after_validation):
    from openprogram.backend.local import LocalBackend

    reason = sandbox.unavailable_reason()
    if reason:
        pytest.skip(reason)
    root = tmp_path / "workspace"
    child = root / "child"
    outside = tmp_path / "not-authorized"
    child.mkdir(parents=True)
    outside.mkdir()
    policy = sandbox.SandboxPolicy(deny_read=(), deny_write=())
    monkeypatch.setattr(sandbox, "resolve_policy", lambda: policy)
    module = importlib.import_module("openprogram.programs.tools.files.bash.bash")
    backend = LocalBackend()
    real_run = backend.run
    if swap_after_validation:
        def swapped(command, timeout, cwd=None):
            # The helper has already resolved/authorized child. The OS sandbox
            # must still launch at root, not turn this now-swapped path into a
            # new writable mount. Use the actual sandbox wrapper/subprocess;
            # only the test's policy configuration is supplied above.
            child.rename(root / "original-child")
            child.symlink_to(outside, target_is_directory=True)
            return real_run(command, timeout, cwd=cwd)
        monkeypatch.setattr(backend, "run", swapped)
    monkeypatch.setattr(module, "get_active_backend", lambda: backend)
    token = set_worktree(str(root))
    try:
        command = "printf escaped > escaped.txt" if swap_after_validation else "printf kept > ../at-root.txt"
        result = asyncio.run(module.bash.execute(
            "sandbox-workdir", {"command": command, "workdir": "child", "timeout": 10_000}, None, None,
        ))
        if swap_after_validation:
            assert result.is_error, _text(result)
            assert not (outside / "escaped.txt").exists()
            assert not (root / "escaped.txt").exists()
        else:
            assert not result.is_error, _text(result)
            assert (root / "at-root.txt").read_text() == "kept"
        assert current_worktree_path() == str(root)
    finally:
        reset_worktree(token)

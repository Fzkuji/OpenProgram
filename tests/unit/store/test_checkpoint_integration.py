"""Integration test: file-mutating tools hook checkpoint automatically.

Drives the ``write``, ``edit``, and ``apply_patch`` tool functions via
their AgentTool ``execute`` coroutine — the same entry point the agent
loop uses — with the ``_store`` and ``_current_turn_id`` ContextVars
installed (the same way the dispatcher installs them per turn). Then
asserts that ``CheckpointStore.restore_turn`` reverts the file changes.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

# Importing the modules registers the AgentTool in the global registry.
import openprogram.programs.tools.files.read  # noqa: F401
import openprogram.programs.tools.files.write  # noqa: F401
import openprogram.programs.tools.files.edit  # noqa: F401
import openprogram.programs.tools.files.apply_patch  # noqa: F401

from openprogram.programs._runtime import get as get_tool
from openprogram.store import _store, _current_turn_id, SessionStore, SessionNodeWriter
from openprogram.store.snapshot.checkpoint import CheckpointStore


SESSION_ID = "op-fb-integ-test"
TURN_ID = "u1_reply"


def _run_tool(name: str, args: dict) -> str:
    tool = get_tool(name)
    assert tool is not None, f"tool {name!r} not registered"
    result = asyncio.run(tool.execute("call-test", args, None, None))
    # Concatenate text content for assertion convenience.
    return "".join(getattr(c, "text", "") for c in result.content)


@pytest.fixture
def session_root(tmp_path: Path):
    root = tmp_path / "sessions-git"
    store = SessionStore(root_path=root)
    store._open(SESSION_ID, create_if_missing=True)
    yield store


@pytest.fixture
def turn_ctx(session_root, tmp_path):
    """Install per-turn ContextVars the way the dispatcher does."""
    from openprogram.worktree.context import reset_worktree, set_worktree

    shim = SessionNodeWriter(session_root, SESSION_ID)
    store_tok = _store.set(shim)
    turn_tok = _current_turn_id.set(TURN_ID)
    worktree_tok = set_worktree(str(tmp_path))
    try:
        yield session_root._session_dir(SESSION_ID)
    finally:
        reset_worktree(worktree_tok)
        _current_turn_id.reset(turn_tok)
        _store.reset(store_tok)


def test_write_tool_backs_up_and_restores(turn_ctx, tmp_path):
    session_dir = turn_ctx
    target = tmp_path / "hello.txt"
    target.write_text("original")

    # read-before-edit gate: overwriting an existing file requires it to
    # have been read first (Claude-Code contract). Mirror the real agent
    # flow.
    _run_tool("read", {"file_path": str(target)})
    out = _run_tool("write", {"file_path": str(target), "content": "overwritten"})
    assert "Wrote" in out
    assert target.read_text() == "overwritten"

    backed = CheckpointStore(session_dir).list_backed_paths(TURN_ID)
    assert str(target) in backed

    CheckpointStore(session_dir).restore_turn(TURN_ID)
    assert target.read_text() == "original"


def test_edit_tool_backs_up_and_restores(turn_ctx, tmp_path):
    session_dir = turn_ctx
    target = tmp_path / "code.py"
    target.write_text("foo = 1\nbar = 2\n")

    # read-before-edit gate: editing requires a prior read.
    _run_tool("read", {"file_path": str(target)})
    out = _run_tool("edit", {
        "file_path": str(target),
        "old_string": "foo = 1",
        "new_string": "foo = 99",
    })
    assert "Edited" in out
    assert "foo = 99" in target.read_text()

    CheckpointStore(session_dir).restore_turn(TURN_ID)
    assert target.read_text() == "foo = 1\nbar = 2\n"


def test_apply_patch_add_then_restore_deletes(turn_ctx, tmp_path):
    """Apply_patch Add File creates a fresh file; restore_turn should
    delete it (pre_existing=False path)."""
    session_dir = turn_ctx
    target = tmp_path / "new.txt"
    assert not target.exists()

    patch = (
        "*** Begin Patch\n"
        f"*** Add File: {target}\n"
        "+line one\n"
        "+line two\n"
        "*** End Patch\n"
    )
    out = _run_tool("apply_patch", {"patch": patch})
    assert "Added" in out
    assert target.exists()

    CheckpointStore(session_dir).restore_turn(TURN_ID)
    assert not target.exists()


def test_tools_noop_without_turn_context(tmp_path):
    """No ContextVars installed → tools still work, no backup recorded
    (and crucially, no crash)."""
    from openprogram.worktree.context import reset_worktree, set_worktree

    token = set_worktree(str(tmp_path))
    try:
        target = tmp_path / "lone.txt"
        target.write_text("a")
        out = _run_tool("write", {"file_path": str(target), "content": "b"})
        assert "Wrote" in out
        assert target.read_text() == "b"
    finally:
        reset_worktree(token)


def test_large_existing_file_is_committed_as_modify(turn_ctx, tmp_path):
    target = tmp_path / "large.log"
    target.write_text("x" * (5 * 1024 * 1024) + "\nold-tail\n", encoding="utf-8")

    _run_tool("read", {"file_path": str(target)})
    out = _run_tool("edit", {
        "file_path": str(target),
        "old_string": "old-tail",
        "new_string": "new-tail",
    })

    assert "Edited" in out
    mutations = CheckpointStore(turn_ctx).list_mutations(TURN_ID)
    assert len(mutations) == 1
    mutation = mutations[0]
    assert mutation["status"] == "committed"
    assert mutation["operation"] == "modify"
    assert mutation["before"]["kind"] == "regular"
    assert mutation["after"]["kind"] == "regular"
    assert mutation["diff_state"] == "large"
    assert mutation["stats"] == {
        "added": None, "removed": None, "binary": False,
    }


def test_failed_edit_does_not_create_mutation(turn_ctx, tmp_path):
    target = tmp_path / "unchanged.py"
    target.write_text("value = 1\n", encoding="utf-8")

    _run_tool("read", {"file_path": str(target)})
    out = _run_tool("edit", {
        "file_path": str(target),
        "old_string": "missing = 2",
        "new_string": "value = 3",
    })

    assert out.startswith("Error:")
    assert target.read_text(encoding="utf-8") == "value = 1\n"
    assert CheckpointStore(turn_ctx).list_mutations(TURN_ID) == []


def test_journal_prepare_failure_blocks_file_write(
    turn_ctx, tmp_path, monkeypatch,
):
    target = tmp_path / "guarded.py"
    target.write_text("value = 1\n", encoding="utf-8")
    _run_tool("read", {"file_path": str(target)})

    def fail_prepare(*_args, **_kwargs):
        raise OSError("journal unavailable")

    monkeypatch.setattr(CheckpointStore, "backup_before_edit", fail_prepare)
    out = _run_tool("edit", {
        "file_path": str(target),
        "old_string": "value = 1",
        "new_string": "value = 2",
    })

    assert out.startswith("Error: mutation journal preparation failed")
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_write_rejects_symlink_without_changing_target(turn_ctx, tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("original\n", encoding="utf-8")
    alias = tmp_path / "alias.txt"
    alias.symlink_to(target)
    _run_tool("read", {"file_path": str(alias)})

    out = _run_tool("write", {"file_path": str(alias), "content": "changed\n"})

    assert out.startswith("Error: mutation journal preparation failed")
    assert target.read_text(encoding="utf-8") == "original\n"
    assert alias.is_symlink()
    assert CheckpointStore(turn_ctx).list_mutations(TURN_ID) == []


def test_edit_rejects_hardlink_without_changing_aliases(turn_ctx, tmp_path):
    target = tmp_path / "target.py"
    target.write_text("value = 1\n", encoding="utf-8")
    alias = tmp_path / "alias.py"
    os.link(target, alias)
    _run_tool("read", {"file_path": str(alias)})

    out = _run_tool("edit", {
        "file_path": str(alias),
        "old_string": "value = 1",
        "new_string": "value = 2",
    })

    assert out.startswith("Error: mutation journal preparation failed")
    assert target.read_text(encoding="utf-8") == "value = 1\n"
    assert alias.read_text(encoding="utf-8") == "value = 1\n"
    assert CheckpointStore(turn_ctx).list_mutations(TURN_ID) == []


def test_apply_patch_preflights_all_aliases_before_writing(turn_ctx, tmp_path):
    safe = tmp_path / "safe.py"
    safe.write_text("safe = 1\n", encoding="utf-8")
    target = tmp_path / "target.py"
    target.write_text("target = 1\n", encoding="utf-8")
    alias = tmp_path / "alias.py"
    alias.symlink_to(target)
    _run_tool("read", {"file_path": str(safe)})
    _run_tool("read", {"file_path": str(alias)})
    patch = (
        "*** Begin Patch\n"
        f"*** Update File: {safe}\n"
        "@@\n"
        "-safe = 1\n"
        "+safe = 2\n"
        f"*** Update File: {alias}\n"
        "@@\n"
        "-target = 1\n"
        "+target = 2\n"
        "*** End Patch\n"
    )

    out = _run_tool("apply_patch", {"patch": patch})

    assert out.startswith("Error: mutation journal preparation failed")
    assert safe.read_text(encoding="utf-8") == "safe = 1\n"
    assert target.read_text(encoding="utf-8") == "target = 1\n"
    assert CheckpointStore(turn_ctx).list_mutations(TURN_ID) == []

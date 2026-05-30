"""Integration test: file-mutating tools hook file_backup automatically.

Drives the ``write``, ``edit``, and ``apply_patch`` tool functions via
their AgentTool ``execute`` coroutine — the same entry point the agent
loop uses — with the ``_store`` and ``_current_turn_id`` ContextVars
installed (the same way the dispatcher installs them per turn). Then
asserts that ``BackupStore.restore_turn`` reverts the file changes.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

# Importing the modules registers the AgentTool in the global registry.
import openprogram.functions.tools.read.read  # noqa: F401
import openprogram.functions.tools.write.write  # noqa: F401
import openprogram.functions.tools.edit.edit  # noqa: F401
import openprogram.functions.tools.apply_patch.apply_patch  # noqa: F401

from openprogram.functions._runtime import get as get_tool
from openprogram.store import _store, _current_turn_id, SessionStore, GraphStoreShim
from openprogram.store.snapshot.file_backup import BackupStore


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
def turn_ctx(session_root):
    """Install per-turn ContextVars the way the dispatcher does."""
    shim = GraphStoreShim(session_root, SESSION_ID)
    store_tok = _store.set(shim)
    turn_tok = _current_turn_id.set(TURN_ID)
    try:
        yield session_root._session_dir(SESSION_ID)
    finally:
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

    backed = BackupStore(session_dir).list_backed_paths(TURN_ID)
    assert str(target) in backed

    BackupStore(session_dir).restore_turn(TURN_ID)
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

    BackupStore(session_dir).restore_turn(TURN_ID)
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

    BackupStore(session_dir).restore_turn(TURN_ID)
    assert not target.exists()


def test_tools_noop_without_turn_context(tmp_path):
    """No ContextVars installed → tools still work, no backup recorded
    (and crucially, no crash)."""
    target = tmp_path / "lone.txt"
    target.write_text("a")
    out = _run_tool("write", {"file_path": str(target), "content": "b"})
    assert "Wrote" in out
    assert target.read_text() == "b"

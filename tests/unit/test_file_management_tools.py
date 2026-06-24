"""Tests for file management agent tools (checkpoint/shadow_git/sandbox).

The @function decorator returns AgentTool instances (not callable).
We test registration + the underlying helper functions directly.
"""
from __future__ import annotations

import pytest


# ── Registration ────────────────────────────────────────────────────


def test_all_tools_registered():
    """All seven tools are registered in the function registry."""
    from openprogram.functions._runtime import get

    # trigger registration
    import openprogram.functions.tools.file_management  # noqa: F401

    names = [
        "checkpoint_list", "checkpoint_restore",
        "shadow_git_log", "shadow_git_diff", "shadow_git_restore_file",
        "sandbox_status", "sandbox_toggle",
    ]
    for name in names:
        tool = get(name)
        assert tool is not None, f"tool {name!r} not registered"


def test_tools_in_full_toolset():
    """All seven tools appear in TOOLSETS['full']['tools']."""
    from openprogram.functions import TOOLSETS
    full_tools = TOOLSETS["full"]["tools"]
    for name in [
        "checkpoint_list", "checkpoint_restore",
        "shadow_git_log", "shadow_git_diff", "shadow_git_restore_file",
        "sandbox_status", "sandbox_toggle",
    ]:
        assert name in full_tools, f"{name!r} missing from TOOLSETS['full']"


# ── Checkpoint helpers ──────────────────────────────────────────────


def test_checkpoint_get_session_dir_no_session():
    from openprogram.functions.tools.file_management.checkpoint_tools import _get_session_dir
    assert _get_session_dir() is None


def test_checkpoint_store_restore_roundtrip(tmp_path):
    """CheckpointStore can backup and restore a file."""
    from openprogram.store.snapshot.checkpoint.store import CheckpointStore

    store = CheckpointStore(tmp_path)
    test_file = tmp_path / "test.txt"
    test_file.write_text("original")

    store.backup_before_edit("turn1", str(test_file))
    test_file.write_text("modified")

    restored = store.restore_turn("turn1")
    assert len(restored) == 1
    assert test_file.read_text() == "original"


# ── Shadow git helpers ──────────────────────────────────────────────


def test_shadow_git_store_init(tmp_path):
    from openprogram.store.shadow_git.store import ShadowGitStore
    store = ShadowGitStore(str(tmp_path))
    assert store._ensure_init()
    assert (store.repo_path / ".git").exists()


def test_shadow_git_log_empty(tmp_path):
    from openprogram.store.shadow_git.store import ShadowGitStore
    store = ShadowGitStore(str(tmp_path))
    entries = store.log(n=5)
    # init commit only
    assert len(entries) <= 1


# ── Sandbox helpers ─────────────────────────────────────────────────


def test_sandbox_enabled_toggle():
    from openprogram.sandbox import sandbox_enabled
    old = sandbox_enabled.get(False)
    try:
        sandbox_enabled.set(False)
        assert sandbox_enabled.get(False) is False
        sandbox_enabled.set(True)
        assert sandbox_enabled.get(False) is True
    finally:
        sandbox_enabled.set(old)


def test_sandbox_is_available():
    from openprogram.sandbox import is_available
    result = is_available()
    assert isinstance(result, bool)

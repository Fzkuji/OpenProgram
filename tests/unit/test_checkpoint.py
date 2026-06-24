"""Unit tests for openprogram.store.snapshot.checkpoint.

Covers the lifecycle: back up a file pre-edit, mutate the file,
restore the turn, file is back to pre-edit content. Also exercises
the create-and-restore-removes path (agent creates a new file →
revert deletes it) and the idempotency guarantee (calling
checkpoint twice in the same turn doesn't double-back).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.store.snapshot.checkpoint import CheckpointStore
from openprogram.store.snapshot.checkpoint.manifest import entries, load
from openprogram.store.snapshot.checkpoint.paths import turn_manifest_path


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    d = tmp_path / "session"
    d.mkdir()
    return d


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    d = tmp_path / "workdir"
    d.mkdir()
    return d


def test_backup_then_restore_existing_file(session_dir, workdir):
    """Pre-existing file: backup captures the original; restoring
    brings it back even after the agent overwrote it."""
    target = workdir / "foo.py"
    target.write_text("original content")

    store = CheckpointStore(session_dir)
    store.backup_before_edit("turn1", str(target))

    target.write_text("agent's new content")
    assert target.read_text() == "agent's new content"

    restored = store.restore_turn("turn1")
    assert str(target) in restored
    assert target.read_text() == "original content"


def test_create_then_restore_removes_file(session_dir, workdir):
    """File that didn't exist pre-turn: backup records that, and
    restoring deletes the freshly-created file."""
    target = workdir / "new.py"
    assert not target.exists()

    store = CheckpointStore(session_dir)
    store.backup_before_edit("turn1", str(target))

    target.write_text("agent's brand new file")
    assert target.exists()

    store.restore_turn("turn1")
    assert not target.exists()


def test_idempotent_within_turn(session_dir, workdir):
    """Calling checkpoint twice in the same turn for the
    same file: only the first backup wins (we want pre-turn state,
    not pre-second-edit state)."""
    target = workdir / "foo.py"
    target.write_text("V0")

    store = CheckpointStore(session_dir)
    store.backup_before_edit("turn1", str(target))

    target.write_text("V1")
    # Second call should be a no-op.
    store.backup_before_edit("turn1", str(target))

    target.write_text("V2")
    store.restore_turn("turn1")
    assert target.read_text() == "V0"


def test_independent_turns_isolated(session_dir, workdir):
    """Two turns each backup the same path. Restoring turn1 brings
    back V0 (turn1's pre-state). Restoring turn2 brings back V1
    (turn2's pre-state)."""
    target = workdir / "foo.py"
    target.write_text("V0")

    store = CheckpointStore(session_dir)
    store.backup_before_edit("turn1", str(target))
    target.write_text("V1")

    store.backup_before_edit("turn2", str(target))
    target.write_text("V2")

    store.restore_turn("turn2")
    assert target.read_text() == "V1"

    store.restore_turn("turn1")
    assert target.read_text() == "V0"


def test_manifest_records_pre_existing_flag(session_dir, workdir):
    """The manifest carries enough info that an external tool / UI
    can distinguish "agent edited an existing file" from "agent
    created a new file"."""
    existing = workdir / "old.py"
    existing.write_text("had this")
    fresh = workdir / "new.py"

    store = CheckpointStore(session_dir)
    store.backup_before_edit("turn1", str(existing))
    store.backup_before_edit("turn1", str(fresh))

    man = load(turn_manifest_path(session_dir, "turn1"))
    paths = {e["path"]: e["pre_existing"] for _, e in man["files"].items()}
    assert paths[str(existing)] is True
    assert paths[str(fresh)] is False


def test_list_backed_paths(session_dir, workdir):
    """list_backed_paths surfaces what the turn touched, useful for
    UI 'this turn edited N files' display."""
    a = workdir / "a.py"
    b = workdir / "b.py"
    a.write_text("a")
    b.write_text("b")

    store = CheckpointStore(session_dir)
    store.backup_before_edit("turn1", str(a))
    store.backup_before_edit("turn1", str(b))

    paths = store.list_backed_paths("turn1")
    assert set(paths) == {str(a), str(b)}


def test_restore_missing_backup_is_skipped(session_dir, workdir):
    """If a backup blob was lost (user deleted it manually), restore
    skips that entry rather than crashing the whole revert."""
    a = workdir / "a.py"
    a.write_text("orig")
    store = CheckpointStore(session_dir)
    store.backup_before_edit("turn1", str(a))

    # User nukes the backup dir contents but leaves manifest.
    from openprogram.store.snapshot.checkpoint.paths import turn_backup_dir
    bd = turn_backup_dir(session_dir, "turn1")
    for p in bd.iterdir():
        if p.name != "manifest.json":
            p.unlink()

    a.write_text("modified")
    restored = store.restore_turn("turn1")
    assert restored == []
    assert a.read_text() == "modified"

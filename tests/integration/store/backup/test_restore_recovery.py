"""Journal recovery validation and filesystem races."""
from __future__ import annotations
import json
import os
from pathlib import Path
import pytest

from tests.integration.store.backup.restore_support import (
    _state,
    _archive,
)


@pytest.mark.parametrize(
    "entry",
    [
        {"relative_path": "../outside.json", "previous": None, "existed": False},
        {
            "relative_path": "config.json",
            "previous": "../outside.json",
            "existed": True,
        },
        {"relative_path": "/tmp/outside.json", "previous": None, "existed": False},
    ],
)
def test_recovery_rejects_journal_traversal_without_mutation(
    tmp_path: Path, entry: dict
) -> None:
    from openprogram.cli.commands.backup import (
        UnrecoverableRestoreJournalError,
        recover_interrupted_restore,
        restore_journal_path,
    )

    state = _state(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text('{"keep": true}')
    journal = restore_journal_path(state)
    journal.write_text(
        json.dumps({"format_version": 1, "complete": False, "entries": [entry]})
    )
    journal.chmod(0o600)

    with pytest.raises(UnrecoverableRestoreJournalError):
        recover_interrupted_restore(state)
    assert json.loads(outside.read_text()) == {"keep": True}
    assert journal.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
@pytest.mark.parametrize("existed", [False, True])
def test_recovery_rejects_target_parent_symlink_before_any_mutation(
    tmp_path: Path, existed: bool
) -> None:
    from openprogram.cli.commands.backup import (
        UnrecoverableRestoreJournalError,
        recover_interrupted_restore,
        restore_journal_path,
    )

    state = _state(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / "target.json"
    outside_target.write_text("outside")
    (state / "linked").symlink_to(outside)
    backup_dir = state / ".restore-journal.d"
    backup_dir.mkdir()
    previous = None
    if existed:
        previous_path = backup_dir / "00000000.previous"
        previous_path.write_text("old")
        previous_path.chmod(0o600)
        previous = ".restore-journal.d/00000000.previous"
    journal = restore_journal_path(state)
    journal.write_text(
        json.dumps(
            {
                "format_version": 1,
                "complete": False,
                "entries": [
                    {
                        "relative_path": "linked/target.json",
                        "previous": previous,
                        "existed": existed,
                    }
                ],
            }
        )
    )
    journal.chmod(0o600)

    with pytest.raises(UnrecoverableRestoreJournalError):
        recover_interrupted_restore(state)
    assert outside_target.read_text() == "outside"
    assert journal.exists()


def test_recovery_rejects_duplicate_journal_entries_before_mutation(
    tmp_path: Path,
) -> None:
    from openprogram.cli.commands.backup import (
        UnrecoverableRestoreJournalError,
        recover_interrupted_restore,
        restore_journal_path,
    )

    state = _state(tmp_path)
    target = state / "config.json"
    target.write_text("current")
    target.chmod(0o600)
    journal = restore_journal_path(state)
    entry = {"relative_path": "config.json", "previous": None, "existed": False}
    journal.write_text(
        json.dumps(
            {"format_version": 1, "complete": False, "entries": [entry, entry]}
        )
    )
    journal.chmod(0o600)

    with pytest.raises(UnrecoverableRestoreJournalError):
        recover_interrupted_restore(state)
    assert target.read_text() == "current"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
@pytest.mark.parametrize("journal_kind", ["malformed", "invalid_schema", "unsafe_path"])
def test_unrecoverable_journal_blocks_new_restore_and_residual_symlink_write(
    tmp_path: Path, journal_kind: str
) -> None:
    from openprogram.cli.commands.backup import (
        UnrecoverableRestoreJournalError,
        restore_archive,
        restore_journal_path,
    )

    state = _state(tmp_path)
    outside_file = tmp_path / "outside.json"
    outside_file.write_text("outside")
    backup_dir = state / ".restore-journal.d"
    backup_dir.mkdir()
    (backup_dir / "00000000.previous").symlink_to(outside_file)
    journal = restore_journal_path(state)
    if journal_kind == "malformed":
        journal.write_text("{")
    elif journal_kind == "invalid_schema":
        journal.write_text(json.dumps({"format_version": 99}))
    else:
        outside_dir = tmp_path / "outside-dir"
        outside_dir.mkdir()
        (outside_dir / "target.json").write_text("outside-target")
        (state / "linked").symlink_to(outside_dir)
        journal.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "complete": False,
                    "entries": [
                        {
                            "relative_path": "linked/target.json",
                            "previous": None,
                            "existed": False,
                        }
                    ],
                }
            )
        )
    journal.chmod(0o600)
    archive = _archive(tmp_path, {"config.json": b"{}"})

    with pytest.raises(UnrecoverableRestoreJournalError):
        restore_archive(archive, state)

    assert outside_file.read_text() == "outside"
    assert (backup_dir / "00000000.previous").is_symlink()
    assert not (state / "config.json").exists()


def _write_recovery_case(state: Path, *, existed: bool) -> tuple[Path, Path]:
    from openprogram.cli.commands.backup import restore_journal_path

    parent = state / "safe"
    parent.mkdir()
    target = parent / "target.json"
    target.write_text("half-applied")
    target.chmod(0o600)
    backup = state / ".restore-journal.d"
    backup.mkdir(mode=0o700)
    previous = None
    if existed:
        source = backup / "00000000.previous"
        source.write_text("old")
        source.chmod(0o600)
        previous = ".restore-journal.d/00000000.previous"
    journal = restore_journal_path(state)
    journal.write_text(
        json.dumps(
            {
                "format_version": 1,
                "complete": False,
                "entries": [
                    {
                        "relative_path": "safe/target.json",
                        "previous": previous,
                        "existed": existed,
                    }
                ],
            }
        )
    )
    journal.chmod(0o600)
    return parent, target


@pytest.mark.skipif(os.name == "nt", reason="POSIX dirfd semantics")
def test_recovery_parent_swap_after_validation_cannot_write_outside_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.cli.commands import backup as backup_cmd

    state = _state(tmp_path)
    parent, _target = _write_recovery_case(state, existed=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / "target.json"
    outside_target.write_text("outside")
    detached = state / "detached"
    real_replace = backup_cmd._journal_replace
    swapped = False

    def swap_then_replace(source, target, **kwargs):
        nonlocal swapped
        if not swapped:
            parent.rename(detached)
            parent.symlink_to(outside)
            swapped = True
        return real_replace(source, target, **kwargs)

    monkeypatch.setattr(backup_cmd, "_journal_replace", swap_then_replace)

    assert backup_cmd.recover_interrupted_restore(state) is True
    assert outside_target.read_text() == "outside"
    assert (detached / "target.json").read_text() == "old"


@pytest.mark.skipif(os.name == "nt", reason="POSIX dirfd semantics")
def test_recovery_source_swap_after_validation_uses_opened_source_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.cli.commands import backup as backup_cmd

    state = _state(tmp_path)
    _parent, target = _write_recovery_case(state, existed=True)
    source = state / ".restore-journal.d" / "00000000.previous"
    outside = tmp_path / "outside.json"
    outside.write_text("outside")
    real_replace = backup_cmd._journal_replace
    swapped = False

    def swap_source_then_replace(src, dst, **kwargs):
        nonlocal swapped
        if not swapped:
            source.unlink()
            source.symlink_to(outside)
            swapped = True
        return real_replace(src, dst, **kwargs)

    monkeypatch.setattr(backup_cmd, "_journal_replace", swap_source_then_replace)

    assert backup_cmd.recover_interrupted_restore(state) is True
    assert target.read_text() == "old"
    assert outside.read_text() == "outside"


@pytest.mark.skipif(os.name == "nt", reason="POSIX dirfd semantics")
def test_recovery_unlink_parent_swap_cannot_delete_outside_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.cli.commands import backup as backup_cmd

    state = _state(tmp_path)
    parent, _target = _write_recovery_case(state, existed=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / "target.json"
    outside_target.write_text("outside")
    detached = state / "detached"
    real_unlink = backup_cmd.os.unlink
    swapped = False

    def swap_then_unlink(path, *args, **kwargs):
        nonlocal swapped
        if not swapped and str(path).endswith("target.json"):
            parent.rename(detached)
            parent.symlink_to(outside)
            swapped = True
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(backup_cmd.os, "unlink", swap_then_unlink)

    assert backup_cmd.recover_interrupted_restore(state) is True
    assert outside_target.read_text() == "outside"
    assert not (detached / "target.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode contract; Windows uses ACLs")
def test_restore_staging_is_owner_only_and_on_the_state_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.cli.commands import backup as backup_cmd

    state = _state(tmp_path)
    archive = _archive(tmp_path, {"memory/core.md": b"value"})
    observed: list[tuple[int, int]] = []
    real_publish = backup_cmd._publish_restored

    def inspect(target: Path, payload: bytes, *, root: Path) -> None:
        staging = next(path for path in state.parent.iterdir() if ".restore-staging-" in path.name)
        observed.append((staging.stat().st_dev, stat.S_IMODE(staging.stat().st_mode)))
        real_publish(target, payload, root=root)

    import stat

    monkeypatch.setattr(backup_cmd, "_publish_restored", inspect)
    backup_cmd.restore_archive(archive, state)

    assert observed == [(state.stat().st_dev, 0o700)]


def test_recovery_removes_targets_that_did_not_exist_before(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import (
        recover_interrupted_restore,
        restore_journal_path,
    )

    state = _state(tmp_path)
    (state / "mcp_servers.json").write_text('{"servers": {"new": {}}}')
    (state / ".restore-journal.d").mkdir()
    restore_journal_path(state).write_text(
        json.dumps(
            {
                "format_version": 1,
                "complete": False,
                "entries": [
                    {
                        "relative_path": "mcp_servers.json",
                        "previous": None,
                        "existed": False,
                    }
                ],
            }
        )
    )

    assert recover_interrupted_restore(state) is True
    assert not (state / "mcp_servers.json").exists()


def test_recovery_is_idempotent_and_a_no_op_without_a_journal(
    tmp_path: Path,
) -> None:
    from openprogram.cli.commands.backup import recover_interrupted_restore

    state = _state(tmp_path)
    (state / "config.json").write_text('{"generation": "current"}')

    assert recover_interrupted_restore(state) is False
    assert recover_interrupted_restore(state) is False
    assert json.loads((state / "config.json").read_text()) == {"generation": "current"}


def test_pre_restore_snapshot_follows_the_archive_credential_authorization(
    tmp_path: Path,
) -> None:
    """The undo snapshot keeps credentials exactly when the archive does."""
    from openprogram.cli.commands.backup import _archive_carries_credentials

    opted_in = _archive(
        tmp_path / "a",
        {"config.json": b"{}"},
        manifest=json.dumps(
            {"format_version": 1, "credential_opt_in": True}
        ).encode(),
    )
    default = _archive(
        tmp_path / "b",
        {"config.json": b"{}"},
        manifest=json.dumps(
            {"format_version": 1, "credential_opt_in": False}
        ).encode(),
    )

    assert _archive_carries_credentials(opted_in) is True
    assert _archive_carries_credentials(default) is False


def test_recovery_ignores_a_journal_marked_complete(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import (
        recover_interrupted_restore,
        restore_journal_path,
    )

    state = _state(tmp_path)
    (state / "config.json").write_text('{"generation": "new"}')
    (state / ".restore-journal.d").mkdir()
    restore_journal_path(state).write_text(
        json.dumps({"format_version": 1, "complete": True, "entries": []})
    )

    assert recover_interrupted_restore(state) is False
    assert json.loads((state / "config.json").read_text()) == {"generation": "new"}
    assert not restore_journal_path(state).exists()

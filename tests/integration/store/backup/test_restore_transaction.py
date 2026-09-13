"""Restore rollback and journal publication."""
from __future__ import annotations
import json
import os
from pathlib import Path
import pytest

from tests.integration.store.backup.restore_support import (
    _state,
    _archive,
)


def test_restore_leaves_no_staging_directory_behind(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import restore_archive

    state = _state(tmp_path)
    archive = _archive(tmp_path, {"config.json": b"{}"})

    restore_archive(archive, state)

    leftovers = [p.name for p in state.parent.iterdir() if "restore" in p.name.lower()]
    assert leftovers == []


def test_mid_restore_failure_rolls_back_every_published_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.cli.commands import backup as backup_cmd

    state = _state(tmp_path)
    (state / "config.json").write_text('{"generation": "old"}')
    (state / "mcp_servers.json").write_text('{"servers": {"old": {}}}')
    archive = _archive(
        tmp_path,
        {
            "config.json": b'{"generation": "new"}',
            "mcp_servers.json": b'{"servers": {"new": {}}}',
        },
    )

    published: list[str] = []
    real_publish = backup_cmd._publish_restored

    def explode(target: Path, payload: bytes, *, root: Path) -> None:
        published.append(target.name)
        if len(published) == 2:
            raise OSError("disk full mid-restore")
        real_publish(target, payload, root=root)

    monkeypatch.setattr(backup_cmd, "_publish_restored", explode)

    with pytest.raises(OSError):
        backup_cmd.restore_archive(archive, state)

    # Old-or-new in full: the first publish is reversed, not left half-applied.
    assert json.loads((state / "config.json").read_text()) == {"generation": "old"}
    assert json.loads((state / "mcp_servers.json").read_text()) == {
        "servers": {"old": {}}
    }


def test_process_abort_rolls_back_and_propagates_original_base_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.cli.commands import backup as backup_cmd

    state = _state(tmp_path)
    target = state / "config.json"
    target.write_text('{"generation": "old"}')
    target.chmod(0o600)
    archive = _archive(tmp_path, {"config.json": b'{"generation": "new"}'})

    def abort(*_args, **_kwargs):
        raise KeyboardInterrupt("abort")

    monkeypatch.setattr(backup_cmd, "_publish_restored", abort)

    with pytest.raises(KeyboardInterrupt, match="abort"):
        backup_cmd.restore_archive(archive, state)

    assert json.loads(target.read_text()) == {"generation": "old"}
    assert not backup_cmd.restore_journal_path(state).exists()


def test_colliding_legacy_backup_names_rollback_distinct_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.cli.commands import backup as backup_cmd

    state = _state(tmp_path)
    first = state / "a" / "b__c"
    second = state / "a__b" / "c"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("old-first")
    second.write_text("old-second")
    archive = _archive(
        tmp_path,
        {"a/b__c": b"new-first", "a__b/c": b"new-second"},
    )
    real_publish = backup_cmd._publish_restored
    calls = 0

    def fail_after_first(target: Path, payload: bytes, *, root: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second publish failed")
        real_publish(target, payload, root=root)

    monkeypatch.setattr(backup_cmd, "_publish_restored", fail_after_first)

    with pytest.raises(OSError):
        backup_cmd.restore_archive(archive, state)

    assert first.read_text() == "old-first"
    assert second.read_text() == "old-second"


def test_journal_is_removed_after_a_successful_restore(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import restore_journal_path, restore_archive

    state = _state(tmp_path)
    archive = _archive(tmp_path, {"config.json": b"{}"})

    restore_archive(archive, state)

    assert not restore_journal_path(state).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_restore_rejects_a_symlinked_journal_without_mutating_its_target(
    tmp_path: Path,
) -> None:
    from openprogram.cli.commands.backup import restore_archive, restore_journal_path

    state = _state(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text('{"keep": true}')
    restore_journal_path(state).symlink_to(outside)
    archive = _archive(tmp_path, {"config.json": b"{}"})

    with pytest.raises(OSError):
        restore_archive(archive, state)

    assert json.loads(outside.read_text()) == {"keep": True}
    assert not (state / "config.json").exists()


def test_short_journal_writes_remain_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.cli.commands import backup as backup_cmd

    state = _state(tmp_path)
    target = state / "config.json"
    target.write_text('{"generation": "old"}')
    journal = backup_cmd._RestoreJournal(state)
    real_write = os.write

    def short_write(descriptor: int, payload: bytes) -> int:
        return real_write(descriptor, payload[: max(1, len(payload) // 2)])

    monkeypatch.setattr(backup_cmd, "_journal_write", short_write)
    journal.start()
    previous = journal.preserve("config.json", target)
    journal.record("config.json", previous)
    target.write_text('{"generation": "half-applied"}')

    assert backup_cmd.recover_interrupted_restore(state) is True
    assert json.loads(target.read_text()) == {"generation": "old"}


def test_crash_after_publish_is_recovered_from_the_journal(tmp_path: Path) -> None:
    """A journal left by a killed restore rolls the state back on recovery."""
    from openprogram.cli.commands.backup import (
        recover_interrupted_restore,
        restore_journal_path,
    )

    state = _state(tmp_path)
    (state / "config.json").write_text('{"generation": "half-applied"}')
    backup_copy = state / ".restore-journal.d" / "00000000.previous"
    backup_copy.parent.mkdir(parents=True)
    backup_copy.parent.chmod(0o700)
    backup_copy.write_text('{"generation": "old"}')
    backup_copy.chmod(0o600)
    restore_journal_path(state).write_text(
        json.dumps(
            {
                "format_version": 1,
                "complete": False,
                "entries": [
                    {
                        "relative_path": "config.json",
                        "previous": ".restore-journal.d/00000000.previous",
                        "existed": True,
                    }
                ],
            }
        )
    )

    recovered = recover_interrupted_restore(state)

    assert recovered is True
    assert json.loads((state / "config.json").read_text()) == {"generation": "old"}
    assert not restore_journal_path(state).exists()

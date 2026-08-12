"""Behavior tests for staged restore, its journal, and crash recovery."""

from __future__ import annotations

import io
import json
import os
import tarfile
from pathlib import Path

import pytest


def _state(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    root.mkdir()
    os.chmod(root, 0o700)
    return root


def _archive(tmp_path: Path, members: dict[str, bytes], *, manifest: bytes | None = None) -> Path:
    from openprogram._cli_cmds.backup import _MANIFEST_NAME

    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "archive.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(payload))
        body = manifest if manifest is not None else (
            json.dumps({"format_version": 1, "credential_opt_in": False}).encode()
        )
        info = tarfile.TarInfo(_MANIFEST_NAME)
        info.size = len(body)
        info.mode = 0o600
        tar.addfile(info, io.BytesIO(body))
    return path


# --- validation happens before anything is published -----------------------


def test_restore_rejects_traversal_member_without_publishing(tmp_path: Path) -> None:
    from openprogram._cli_cmds.backup import restore_archive

    state = _state(tmp_path)
    (state / "config.json").write_text('{"keep": true}')
    archive = _archive(tmp_path, {"../escape.json": b"{}", "config.json": b"{}"})

    with pytest.raises(tarfile.TarError):
        restore_archive(archive, state)

    assert json.loads((state / "config.json").read_text()) == {"keep": True}
    assert not (tmp_path / "escape.json").exists()


def test_restore_rejects_symlink_member(tmp_path: Path) -> None:
    from openprogram._cli_cmds.backup import _MANIFEST_NAME, restore_archive

    state = _state(tmp_path)
    (state / "config.json").write_text('{"keep": true}')
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        link = tarfile.TarInfo("config.json")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
        body = json.dumps({"format_version": 1}).encode()
        info = tarfile.TarInfo(_MANIFEST_NAME)
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))

    with pytest.raises(tarfile.TarError):
        restore_archive(archive, state)

    assert json.loads((state / "config.json").read_text()) == {"keep": True}
    assert not (state / "config.json").is_symlink()


def test_restore_rejects_hardlink_member(tmp_path: Path) -> None:
    from openprogram._cli_cmds.backup import _MANIFEST_NAME, restore_archive

    state = _state(tmp_path)
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"{}"
        info = tarfile.TarInfo("config.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
        link = tarfile.TarInfo("auth/openai/default.json")
        link.type = tarfile.LNKTYPE
        link.linkname = "config.json"
        tar.addfile(link)
        body = json.dumps({"format_version": 1}).encode()
        manifest = tarfile.TarInfo(_MANIFEST_NAME)
        manifest.size = len(body)
        tar.addfile(manifest, io.BytesIO(body))

    with pytest.raises(tarfile.TarError):
        restore_archive(archive, state)


def test_restore_rejects_a_missing_manifest(tmp_path: Path) -> None:
    from openprogram._cli_cmds.backup import restore_archive

    state = _state(tmp_path)
    archive = tmp_path / "bare.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"{}"
        info = tarfile.TarInfo("config.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    with pytest.raises(tarfile.TarError):
        restore_archive(archive, state)


@pytest.mark.parametrize(
    "manifest",
    [
        [],
        {"format_version": 2, "credential_opt_in": False},
        {"format_version": True, "credential_opt_in": False},
        {"format_version": 1, "credential_opt_in": "false"},
    ],
)
def test_restore_rejects_invalid_manifest_schema(tmp_path: Path, manifest) -> None:
    from openprogram._cli_cmds.backup import restore_archive

    state = _state(tmp_path)
    archive = _archive(
        tmp_path, {"config.json": b"{}"}, manifest=json.dumps(manifest).encode()
    )

    with pytest.raises(tarfile.TarError, match="manifest"):
        restore_archive(archive, state)

    assert not (state / "config.json").exists()


def test_restore_rejects_a_registered_secret_member_that_is_not_json(
    tmp_path: Path,
) -> None:
    from openprogram._cli_cmds.backup import restore_archive

    state = _state(tmp_path)
    (state / "config.json").write_text('{"keep": true}')
    archive = _archive(tmp_path, {"config.json": b"this is not json"})

    with pytest.raises(tarfile.TarError):
        restore_archive(archive, state)

    assert json.loads((state / "config.json").read_text()) == {"keep": True}


def test_restore_rejects_secret_members_when_manifest_denies_opt_in(
    tmp_path: Path,
) -> None:
    from openprogram._cli_cmds.backup import restore_archive

    state = _state(tmp_path)
    archive = _archive(
        tmp_path,
        {"auth/openai/default.json": b'{"credentials": []}'},
        manifest=json.dumps(
            {"format_version": 1, "credential_opt_in": False}
        ).encode(),
    )

    with pytest.raises(tarfile.TarError, match="credential_opt_in"):
        restore_archive(archive, state)

    assert not (state / "auth").exists()


# --- publication, permissions, and secret preservation ---------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
def test_restored_secret_files_are_owner_only(tmp_path: Path) -> None:
    import stat

    from openprogram._cli_cmds.backup import restore_archive

    state = _state(tmp_path)
    archive = _archive(
        tmp_path,
        {
            "config.json": b'{"api_keys": {}}',
            "auth/openai/default.json": b'{"credentials": []}',
        },
        manifest=json.dumps(
            {"format_version": 1, "credential_opt_in": True}
        ).encode(),
    )

    restore_archive(archive, state)

    for name in ("config.json", "auth/openai/default.json"):
        mode = stat.S_IMODE(os.lstat(state / name).st_mode)
        assert mode == 0o600, name
    assert stat.S_IMODE(os.lstat(state / "auth" / "openai").st_mode) == 0o700


def test_restore_preserves_local_secrets_for_redacted_fields(tmp_path: Path) -> None:
    from openprogram._cli_cmds.backup import restore_archive

    state = _state(tmp_path)
    (state / "config.json").write_text(
        json.dumps({"api_keys": {"OPENAI_API_KEY": "sk-local"}, "ui": {"port": 1}})
    )
    os.chmod(state / "config.json", 0o600)
    # A default archive carries config.json with api_keys redacted away.
    archive = _archive(tmp_path, {"config.json": json.dumps({"ui": {"port": 2}}).encode()})

    restore_archive(archive, state)

    restored = json.loads((state / "config.json").read_text())
    assert restored["api_keys"] == {"OPENAI_API_KEY": "sk-local"}
    assert restored["ui"] == {"port": 2}


def test_restore_leaves_no_staging_directory_behind(tmp_path: Path) -> None:
    from openprogram._cli_cmds.backup import restore_archive

    state = _state(tmp_path)
    archive = _archive(tmp_path, {"config.json": b"{}"})

    restore_archive(archive, state)

    leftovers = [p.name for p in state.parent.iterdir() if "restore" in p.name.lower()]
    assert leftovers == []


# --- journal, rollback, and crash recovery ---------------------------------


def test_mid_restore_failure_rolls_back_every_published_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram._cli_cmds import backup as backup_cmd

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


def test_journal_is_removed_after_a_successful_restore(tmp_path: Path) -> None:
    from openprogram._cli_cmds.backup import restore_journal_path, restore_archive

    state = _state(tmp_path)
    archive = _archive(tmp_path, {"config.json": b"{}"})

    restore_archive(archive, state)

    assert not restore_journal_path(state).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_restore_rejects_a_symlinked_journal_without_mutating_its_target(
    tmp_path: Path,
) -> None:
    from openprogram._cli_cmds.backup import restore_archive, restore_journal_path

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
    from openprogram._cli_cmds import backup as backup_cmd

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


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_restore_rejects_existing_target_symlink_without_changing_its_type(
    tmp_path: Path,
) -> None:
    from openprogram._cli_cmds.backup import restore_archive

    state = _state(tmp_path)
    real = state / "real.json"
    real.write_text('{"keep": true}')
    target = state / "config.json"
    target.symlink_to(real)
    archive = _archive(tmp_path, {"config.json": b"{}"})

    with pytest.raises(OSError):
        restore_archive(archive, state)

    assert target.is_symlink()
    assert json.loads(real.read_text()) == {"keep": True}


def test_crash_after_publish_is_recovered_from_the_journal(tmp_path: Path) -> None:
    """A journal left by a killed restore rolls the state back on recovery."""
    from openprogram._cli_cmds.backup import (
        recover_interrupted_restore,
        restore_journal_path,
    )

    state = _state(tmp_path)
    (state / "config.json").write_text('{"generation": "half-applied"}')
    backup_copy = state / ".restore-journal.d" / "config.json"
    backup_copy.parent.mkdir(parents=True)
    backup_copy.write_text('{"generation": "old"}')
    restore_journal_path(state).write_text(
        json.dumps(
            {
                "format_version": 1,
                "complete": False,
                "entries": [
                    {
                        "relative_path": "config.json",
                        "previous": ".restore-journal.d/config.json",
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
    from openprogram._cli_cmds.backup import recover_interrupted_restore, restore_journal_path

    state = _state(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text('{"keep": true}')
    journal = restore_journal_path(state)
    journal.write_text(
        json.dumps({"format_version": 1, "complete": False, "entries": [entry]})
    )
    journal.chmod(0o600)

    assert recover_interrupted_restore(state) is False
    assert json.loads(outside.read_text()) == {"keep": True}
    assert journal.exists()


def test_restore_staging_is_owner_only_and_on_the_state_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram._cli_cmds import backup as backup_cmd

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
    from openprogram._cli_cmds.backup import (
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
    from openprogram._cli_cmds.backup import recover_interrupted_restore

    state = _state(tmp_path)
    (state / "config.json").write_text('{"generation": "current"}')

    assert recover_interrupted_restore(state) is False
    assert recover_interrupted_restore(state) is False
    assert json.loads((state / "config.json").read_text()) == {"generation": "current"}


def test_pre_restore_snapshot_follows_the_archive_credential_authorization(
    tmp_path: Path,
) -> None:
    """The undo snapshot keeps credentials exactly when the archive does."""
    from openprogram._cli_cmds.backup import _archive_carries_credentials

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
    from openprogram._cli_cmds.backup import (
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

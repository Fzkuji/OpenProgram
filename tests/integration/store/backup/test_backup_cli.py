"""Backup command parsing, prompts, profiles and archive management."""
from __future__ import annotations
import os
import signal
import stat
import tarfile
from pathlib import Path
import pytest

from tests.integration.store.backup.command_support import (
    profile as profile,
    _no_running_processes as _no_running_processes,
    _write_restorable_archive,
    _start_restore_paused_after_first_publish,
    _seed_registered_secrets,
)


def test_backup_cli_warning_and_manifest_report_same_scope(profile: Path, capsys):
    from openprogram.cli.commands.backup import _cmd_backup_create

    _seed_registered_secrets(profile)
    assert _cmd_backup_create(include_credentials=True) == 0
    output = capsys.readouterr().out
    assert "plaintext credentials" in output
    assert "Web runtime tokens and pending pairing codes are never included" in output


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode contract; Windows uses ACLs")
def test_archive_is_owner_only_and_named_for_profile(profile: Path):
    from openprogram.cli.commands.backup import create_backup

    archive = create_backup()
    mode = stat.S_IMODE(archive.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"
    assert archive.parent == profile / "backups"
    assert archive.name.startswith("default-")
    assert archive.name.endswith(".tar.gz")


def test_create_and_restore_round_trip(profile: Path, capsys):
    from openprogram.cli.commands.backup import _cmd_backup_create, _cmd_backup_restore

    assert _cmd_backup_create() == 0
    out = capsys.readouterr().out
    assert "size:" in out and "content:" in out
    assert "credentials excluded" in out

    archive = next((profile / "backups").glob("default-*.tar.gz"))

    # Mutate state, then restore it away.
    (profile / "memory" / "core.md").write_text("clobbered", encoding="utf-8")
    (profile / "sessions" / "s1.json").unlink()

    assert _cmd_backup_restore(archive.name, yes=True) == 0
    assert (profile / "memory" / "core.md").read_text(encoding="utf-8") == "remembered"
    assert (profile / "sessions" / "s1.json").exists()


def test_restore_snapshots_current_state_first(profile: Path):
    from openprogram.cli.commands.backup import _cmd_backup_restore, create_backup

    archive = create_backup()
    (profile / "memory" / "core.md").write_text("about to be lost", encoding="utf-8")

    assert _cmd_backup_restore(archive.name, yes=True) == 0

    safety = list((profile / "backups").glob("default-pre-restore-*.tar.gz"))
    assert len(safety) == 1, "restore must snapshot current state first"
    with tarfile.open(safety[0], "r:gz") as tar:
        member = tar.extractfile("memory/core.md")
        assert member is not None
        assert member.read().decode() == "about to be lost"


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock and SIGKILL semantics")
def test_create_backup_is_busy_during_restore_publication(
    profile: Path, tmp_path: Path
) -> None:
    from openprogram.cli.commands.backup import (
        RestoreBusyError,
        create_backup,
        recover_interrupted_restore,
    )

    archive = _write_restorable_archive(
        tmp_path / "incoming.tar.gz",
        {
            "memory/core.md": b"new-memory",
            "sessions/s1.json": b'{"generation": "new"}',
        },
    )
    marker = tmp_path / "restore-paused"
    process = _start_restore_paused_after_first_publish(profile, archive, marker)
    before = set((profile / "backups").glob("*.tar.gz")) if (profile / "backups").exists() else set()
    try:
        with pytest.raises(RestoreBusyError):
            create_backup()
        assert set((profile / "backups").glob("*.tar.gz")) == before
    finally:
        process.send_signal(signal.SIGKILL)
        process.wait(5)
        recover_interrupted_restore(profile)


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock and SIGKILL semantics")
def test_backup_create_cli_reports_busy_without_archive(
    profile: Path, tmp_path: Path, capsys
) -> None:
    from openprogram.cli.commands.backup import (
        _cmd_backup_create,
        recover_interrupted_restore,
    )

    archive = _write_restorable_archive(
        tmp_path / "incoming.tar.gz",
        {
            "memory/core.md": b"new-memory",
            "sessions/s1.json": b'{"generation": "new"}',
        },
    )
    marker = tmp_path / "restore-paused"
    process = _start_restore_paused_after_first_publish(profile, archive, marker)
    try:
        assert _cmd_backup_create() == 1
        assert "another restore is already in progress" in capsys.readouterr().err
        assert not list((profile / "backups").glob("*.tar.gz"))
    finally:
        process.send_signal(signal.SIGKILL)
        process.wait(5)
        recover_interrupted_restore(profile)


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock and SIGKILL semantics")
def test_busy_restore_cli_does_not_create_safety_snapshot(
    profile: Path, tmp_path: Path
) -> None:
    from openprogram.cli.commands.backup import (
        _cmd_backup_restore,
        recover_interrupted_restore,
    )

    archive = _write_restorable_archive(
        tmp_path / "incoming.tar.gz",
        {
            "memory/core.md": b"new-memory",
            "sessions/s1.json": b'{"generation": "new"}',
        },
    )
    marker = tmp_path / "restore-paused"
    process = _start_restore_paused_after_first_publish(profile, archive, marker)
    try:
        assert _cmd_backup_restore(str(archive), yes=True) == 1
        assert not list((profile / "backups").glob("*pre-restore*.tar.gz"))
    finally:
        process.send_signal(signal.SIGKILL)
        process.wait(5)
        recover_interrupted_restore(profile)


def test_restore_refuses_while_worker_running(profile: Path, monkeypatch, capsys):
    from openprogram.cli.commands import backup

    archive = backup.create_backup()
    monkeypatch.setattr(backup, "_running_processes", lambda: ["worker (PID 42)"])

    assert backup._cmd_backup_restore(archive.name, yes=True) == 1
    err = capsys.readouterr().err
    assert "refusing to restore" in err
    assert "openprogram stop" in err
    # And no safety backup was written, because we never got that far.
    assert not list((profile / "backups").glob("*pre-restore*"))


def test_dry_run_changes_nothing(profile: Path, capsys):
    from openprogram.cli.commands.backup import _cmd_backup_restore, create_backup

    archive = create_backup()
    (profile / "memory" / "core.md").write_text("untouched", encoding="utf-8")
    before = sorted(p.name for p in (profile / "backups").iterdir())

    assert _cmd_backup_restore(archive.name, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out
    assert "overwrite" in out
    assert "memory" in out

    assert (profile / "memory" / "core.md").read_text(encoding="utf-8") == "untouched"
    assert sorted(p.name for p in (profile / "backups").iterdir()) == before


def test_restore_declined_at_prompt_aborts(profile: Path, monkeypatch):
    from openprogram.cli.commands import backup

    archive = backup.create_backup()
    (profile / "memory" / "core.md").write_text("kept", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda _="": "n")

    assert backup._cmd_backup_restore(archive.name) == 1
    assert (profile / "memory" / "core.md").read_text(encoding="utf-8") == "kept"


def test_list_shows_size_and_contents(profile: Path, capsys):
    from openprogram.cli.commands.backup import _cmd_backup_list, create_backup

    create_backup()
    assert _cmd_backup_list() == 0
    out = capsys.readouterr().out
    assert "default-" in out
    assert "memory" in out
    assert "KB" in out or "B" in out


def test_list_is_empty_without_backups(profile: Path, capsys):
    from openprogram.cli.commands.backup import _cmd_backup_list

    assert _cmd_backup_list() == 0
    assert "No backups" in capsys.readouterr().out


def test_prune_keeps_newest_n(profile: Path):
    import os
    import time

    from openprogram.cli.commands.backup import _cmd_backup_prune, create_backup

    made = []
    for i in range(4):
        path = create_backup(label=f"n{i}")
        # Distinct mtimes so ordering is deterministic without sleeping.
        os.utime(path, (time.time() + i, time.time() + i))
        made.append(path)

    assert _cmd_backup_prune(keep=2) == 0
    left = sorted(p.name for p in (profile / "backups").glob("*.tar.gz"))
    assert len(left) == 2
    assert made[-1].name in left and made[-2].name in left


def test_prune_rejects_zero(profile: Path, capsys):
    from openprogram.cli.commands.backup import _cmd_backup_prune

    assert _cmd_backup_prune(keep=0) == 1
    assert "at least 1" in capsys.readouterr().err


def test_interrupted_create_leaves_no_visible_archive(profile: Path, monkeypatch):
    """A create that dies mid-write must not leave something `list` shows."""
    import tarfile as _tarfile

    from openprogram.cli.commands import backup

    real_add = _tarfile.TarFile.add

    def explode(self, name, *args, **kwargs):
        real_add(self, name, *args, **kwargs)
        raise OSError("disk full")

    monkeypatch.setattr(_tarfile.TarFile, "add", explode)
    with pytest.raises(OSError):
        backup.create_backup()

    assert list((profile / "backups").glob("*.tar.gz")) == []
    assert list((profile / "backups").glob("*.partial")) == []


def test_restore_unknown_name_errors(profile: Path, capsys):
    from openprogram.cli.commands.backup import _cmd_backup_restore

    assert _cmd_backup_restore("nope.tar.gz", yes=True) == 1
    assert "no such backup" in capsys.readouterr().err


def test_named_profile_is_isolated(profile: Path, monkeypatch: pytest.MonkeyPatch):
    from openprogram.cli.commands.backup import backups_dir, create_backup

    monkeypatch.setenv("OPENPROGRAM_PROFILE", "alpha")
    alt = Path.home() / ".openprogram-alpha"
    (alt / "memory").mkdir(parents=True)
    (alt / "memory" / "core.md").write_text("alpha memory", encoding="utf-8")

    archive = create_backup()
    assert archive.parent == alt / "backups"
    assert archive.name.startswith("alpha-")
    assert backups_dir() == alt / "backups"
    with tarfile.open(archive, "r:gz") as tar:
        member = tar.extractfile("memory/core.md")
        assert member is not None
        assert member.read().decode() == "alpha memory"


def test_cli_registers_backup_verbs():
    from openprogram.cli import build_parser

    parser = build_parser()
    for verb in ("create", "list", "restore", "prune"):
        args = parser.parse_args(
            ["backup", verb] + (["x"] if verb == "restore" else [])
        )
        assert args.command == "backup"
        assert args.backup_verb == verb

    args = parser.parse_args(["backup", "create", "--include-credentials"])
    assert args.include_credentials is True
    args = parser.parse_args(["backup", "prune", "--keep", "3"])
    assert args.keep == 3
    args = parser.parse_args(["backup", "restore", "x", "--dry-run"])
    assert args.dry_run is True

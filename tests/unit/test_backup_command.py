"""Round-trip, scope, and safety tests for `openprogram backup`."""
from __future__ import annotations

import stat
import tarfile
from pathlib import Path

import pytest


@pytest.fixture()
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A populated fake state dir, with paths rerouted to it."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("OPENPROGRAM_PROFILE", raising=False)

    import openprogram.paths as paths
    monkeypatch.setattr(paths, "_migration_checked", True)
    monkeypatch.setattr(paths, "_root_mode_checked", set())

    state = home / ".openprogram"
    (state / "memory" / "topics").mkdir(parents=True)
    (state / "memory" / "core.md").write_text("remembered", encoding="utf-8")
    (state / "memory" / "topics" / "a.md").write_text("topic a", encoding="utf-8")
    (state / "sessions").mkdir()
    (state / "sessions" / "s1.json").write_text('{"id": "s1"}', encoding="utf-8")
    (state / "config.json").write_text('{"theme": "dark"}', encoding="utf-8")
    (state / "bindings.json").write_text('{"discord": []}', encoding="utf-8")
    (state / "programs_meta.json").write_text('{"favorites": []}', encoding="utf-8")
    (state / "functions_meta.json").write_text("{}", encoding="utf-8")
    (state / "channels").mkdir()
    (state / "channels" / "discord.json").write_text("{}", encoding="utf-8")

    # Out-of-scope noise that must never land in an archive.
    (state / "cache" / "blobs").mkdir(parents=True)
    (state / "cache" / "blobs" / "big.bin").write_bytes(b"x" * 1024)
    (state / "logs").mkdir()
    (state / "logs" / "worker.log").write_text("noise", encoding="utf-8")
    (state / "trash").mkdir()
    (state / "trash" / "deleted.txt").write_text("gone", encoding="utf-8")
    (state / "worker.lock").write_text("", encoding="utf-8")
    (state / "worker.pid").write_text("1234", encoding="utf-8")
    (state / "worker.port").write_text("18100", encoding="utf-8")
    (state / "channels.log").write_text("noise", encoding="utf-8")
    (state / "auth").mkdir()
    (state / "auth" / "anthropic.json").write_text('{"key": "secret"}', encoding="utf-8")
    (state / "mcp_tokens").mkdir()
    (state / "mcp_tokens" / "t.json").write_text('{"token": "secret"}', encoding="utf-8")
    (state / "skills").mkdir()
    (state / "skills" / "node_modules").mkdir()
    (state / "skills" / "node_modules" / "junk.js").write_text("//", encoding="utf-8")
    (state / "skills" / "real.md").write_text("# skill", encoding="utf-8")
    return state


@pytest.fixture(autouse=True)
def _no_running_processes(monkeypatch: pytest.MonkeyPatch):
    """Default: nothing is running, so restore is allowed."""
    from openprogram._cli_cmds import backup
    monkeypatch.setattr(backup, "_running_processes", lambda: [])


def _members(path: Path) -> list[str]:
    with tarfile.open(path, "r:gz") as tar:
        return tar.getnames()


def test_create_captures_scope_and_excludes_noise(profile: Path):
    from openprogram._cli_cmds.backup import create_backup

    archive = create_backup()
    names = _members(archive)

    assert "memory/core.md" in names
    assert "memory/topics/a.md" in names
    assert "sessions/s1.json" in names
    assert "config.json" in names
    assert "bindings.json" in names
    assert "programs_meta.json" in names
    assert "functions_meta.json" in names
    assert "channels/discord.json" in names
    assert "skills/real.md" in names

    # Excluded by scope or by name/suffix rules.
    for unwanted in ("cache", "logs", "trash", "worker.lock", "worker.pid",
                     "worker.port", "channels.log"):
        assert not any(n == unwanted or n.startswith(unwanted + "/")
                       for n in names), f"{unwanted} leaked into archive"
    assert not any("node_modules" in n for n in names)


def test_credentials_excluded_by_default_and_opt_in_works(profile: Path):
    from openprogram._cli_cmds.backup import create_backup

    default_names = _members(create_backup())
    assert not any(n.startswith("auth") for n in default_names)
    assert not any(n.startswith("mcp_tokens") for n in default_names)

    opt_in_names = _members(create_backup(include_credentials=True))
    assert "auth/anthropic.json" in opt_in_names
    assert "mcp_tokens/t.json" in opt_in_names


def test_archive_is_owner_only_and_named_for_profile(profile: Path):
    from openprogram._cli_cmds.backup import create_backup

    archive = create_backup()
    mode = stat.S_IMODE(archive.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"
    assert archive.parent == profile / "backups"
    assert archive.name.startswith("default-")
    assert archive.name.endswith(".tar.gz")


def test_create_and_restore_round_trip(profile: Path, capsys):
    from openprogram._cli_cmds.backup import (_cmd_backup_create,
                                              _cmd_backup_restore)

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
    from openprogram._cli_cmds.backup import (_cmd_backup_restore,
                                              create_backup)

    archive = create_backup()
    (profile / "memory" / "core.md").write_text("about to be lost", encoding="utf-8")

    assert _cmd_backup_restore(archive.name, yes=True) == 0

    safety = list((profile / "backups").glob("default-pre-restore-*.tar.gz"))
    assert len(safety) == 1, "restore must snapshot current state first"
    with tarfile.open(safety[0], "r:gz") as tar:
        member = tar.extractfile("memory/core.md")
        assert member is not None
        assert member.read().decode() == "about to be lost"


def test_restore_refuses_while_worker_running(profile: Path, monkeypatch, capsys):
    from openprogram._cli_cmds import backup

    archive = backup.create_backup()
    monkeypatch.setattr(backup, "_running_processes", lambda: ["worker (PID 42)"])

    assert backup._cmd_backup_restore(archive.name, yes=True) == 1
    err = capsys.readouterr().err
    assert "refusing to restore" in err
    assert "openprogram stop" in err
    # And no safety backup was written, because we never got that far.
    assert not list((profile / "backups").glob("*pre-restore*"))


def test_dry_run_changes_nothing(profile: Path, capsys):
    from openprogram._cli_cmds.backup import (_cmd_backup_restore,
                                              create_backup)

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
    from openprogram._cli_cmds import backup

    archive = backup.create_backup()
    (profile / "memory" / "core.md").write_text("kept", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda _="": "n")

    assert backup._cmd_backup_restore(archive.name) == 1
    assert (profile / "memory" / "core.md").read_text(encoding="utf-8") == "kept"


def test_list_shows_size_and_contents(profile: Path, capsys):
    from openprogram._cli_cmds.backup import _cmd_backup_list, create_backup

    create_backup()
    assert _cmd_backup_list() == 0
    out = capsys.readouterr().out
    assert "default-" in out
    assert "memory" in out
    assert "KB" in out or "B" in out


def test_list_is_empty_without_backups(profile: Path, capsys):
    from openprogram._cli_cmds.backup import _cmd_backup_list

    assert _cmd_backup_list() == 0
    assert "No backups" in capsys.readouterr().out


def test_prune_keeps_newest_n(profile: Path):
    import os
    import time

    from openprogram._cli_cmds.backup import _cmd_backup_prune, create_backup

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
    from openprogram._cli_cmds.backup import _cmd_backup_prune

    assert _cmd_backup_prune(keep=0) == 1
    assert "at least 1" in capsys.readouterr().err


def test_interrupted_create_leaves_no_visible_archive(profile: Path, monkeypatch):
    """A create that dies mid-write must not leave something `list` shows."""
    import tarfile as _tarfile

    from openprogram._cli_cmds import backup

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
    from openprogram._cli_cmds.backup import _cmd_backup_restore

    assert _cmd_backup_restore("nope.tar.gz", yes=True) == 1
    assert "no such backup" in capsys.readouterr().err


def test_named_profile_is_isolated(profile: Path, monkeypatch: pytest.MonkeyPatch):
    from openprogram._cli_cmds.backup import backups_dir, create_backup

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
        args = parser.parse_args(["backup", verb] + (["x"] if verb == "restore" else []))
        assert args.command == "backup"
        assert args.backup_verb == verb

    args = parser.parse_args(["backup", "create", "--include-credentials"])
    assert args.include_credentials is True
    args = parser.parse_args(["backup", "prune", "--keep", "3"])
    assert args.keep == 3
    args = parser.parse_args(["backup", "restore", "x", "--dry-run"])
    assert args.dry_run is True

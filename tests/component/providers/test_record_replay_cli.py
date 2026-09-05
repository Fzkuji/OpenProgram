from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from openprogram import paths, setup
from openprogram.config_schema import _BY_KEY, set_setting
from openprogram.providers.recording import (
    RecordingManagementError,
    create_managed_recording,
    delete_recording,
    dispatch_recordings,
    list_recordings,
    prune_recordings,
    set_record_mode,
    set_record_replay_off,
    set_replay_mode,
    show_recording,
)
from openprogram.providers.recording import RecordingSink
from openprogram.providers.replay import RecordingFileError


@pytest.fixture
def recording_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state"
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    monkeypatch.setattr(setup, "get_config_path", lambda: state / "config.json")
    return state


def test_record_command_precreates_private_header_then_updates_config(
    recording_env: Path,
) -> None:
    info = set_record_mode("fixture")

    config = setup._read_config()["record_replay"]
    header = json.loads(info.path.read_text().splitlines()[0])
    assert config == {"mode": "record", "file": "fixture"}
    assert header["recording_id"] == "fixture"
    assert header["redaction_version"] == 1
    if os.name != "nt":
        assert stat.S_IMODE(info.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(info.path.parent.stat().st_mode) == 0o700


def test_record_rolls_back_new_file_when_config_update_fails(
    recording_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup._write_config({"record_replay": {"mode": "off", "file": "old"}})
    monkeypatch.setattr(setup, "update_config", lambda mutator: (_ for _ in ()).throw(OSError("full")))

    with pytest.raises(OSError, match="full"):
        set_record_mode("rollback")

    assert not (paths.get_recordings_dir() / "rollback.jsonl").exists()
    assert setup._read_config()["record_replay"] == {"mode": "off", "file": "old"}


def test_create_rolls_back_partial_file_when_header_write_fails(
    recording_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "openprogram.providers.recording._write_all",
        lambda fd, payload: (_ for _ in ()).throw(OSError("write failed")),
    )

    with pytest.raises(OSError, match="write failed"):
        create_managed_recording("partial")

    assert not (paths.get_recordings_dir() / "partial.jsonl").exists()


def test_replay_prevalidates_before_updating_config(recording_env: Path) -> None:
    invalid = recording_env / "invalid.jsonl"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("not-json\n")
    setup._write_config({"record_replay": {"mode": "off", "file": "old"}})

    with pytest.raises(RecordingFileError):
        set_replay_mode(str(invalid))

    assert setup._read_config()["record_replay"] == {"mode": "off", "file": "old"}


def test_list_show_and_delete_managed_recordings(recording_env: Path) -> None:
    info = create_managed_recording("managed")

    listed = list_recordings()
    shown = show_recording("managed", include_content=True)
    assert [row.recording_id for row in listed] == ["managed"]
    assert shown["recording_id"] == "managed"
    assert shown["content"][0]["type"] == "header"

    delete_recording("managed")
    assert not info.path.exists()


def test_delete_rejects_active_external_and_symlink_recordings(
    recording_env: Path, tmp_path: Path
) -> None:
    active = set_record_mode("active")
    outside = tmp_path / "outside.jsonl"
    outside.write_text(active.path.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(RecordingManagementError, match="active"):
        delete_recording("active")
    with pytest.raises(RecordingManagementError, match="managed ID"):
        delete_recording(str(outside))
    link = paths.get_recordings_dir() / "linked.jsonl"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable for this Windows account: {exc}")
    with pytest.raises(RecordingManagementError, match="symlink"):
        delete_recording("linked")


def test_prune_deletes_only_old_inactive_managed_files(recording_env: Path) -> None:
    old = create_managed_recording("old")
    recent = create_managed_recording("recent")
    active = set_record_mode("active")
    old_time = time.time() - 10 * 86400
    os.utime(old.path, (old_time, old_time))
    os.utime(active.path, (old_time, old_time))

    preview = prune_recordings(older_than_days=5, dry_run=True)
    result = prune_recordings(older_than_days=5)

    assert preview["matched"] == 1 and preview["deleted"] == 0
    assert result["deleted"] == 1
    assert not old.path.exists()
    assert recent.path.exists() and active.path.exists()


def test_prune_skips_a_locked_old_recording(recording_env: Path) -> None:
    old = create_managed_recording("locked")
    old_time = time.time() - 10 * 86400
    os.utime(old.path, (old_time, old_time))
    sink = RecordingSink(old.path)

    with sink._locked():
        result = prune_recordings(older_than_days=5)

    assert result["matched"] == 1
    assert result["failed"] == 1
    assert old.path.exists()


def test_record_replay_config_settings_are_next_start(recording_env: Path) -> None:
    assert _BY_KEY["record_replay.mode"].apply == "next_start"
    assert _BY_KEY["record_replay.file"].apply == "next_start"
    assert set_setting("record_replay.mode", "record")["error"]
    assert set_setting("record_replay.file", "fixture")["value"] == "fixture"
    assert set_setting("record_replay.mode", "record")["value"] == "record"
    assert set_setting("record_replay.file", "")["error"]


def test_config_replay_mode_rejects_an_invalid_file(recording_env: Path) -> None:
    invalid = recording_env / "invalid.jsonl"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("not-json\n")
    assert set_setting("record_replay.file", str(invalid))["value"] == str(invalid)

    result = set_setting("record_replay.mode", "replay")

    assert "error" in result
    assert setup._read_config()["record_replay"].get("mode") is None


def test_recordings_parser_and_json_dispatch(recording_env: Path, capsys) -> None:
    from openprogram.cli import build_parser

    args = build_parser().parse_args(["recordings", "record", "--name", "cli"])
    assert (args.command, args.recordings_verb, args.name) == ("recordings", "record", "cli")
    assert dispatch_recordings(args) == 0

    status = build_parser().parse_args(["recordings", "status", "--json"])
    assert dispatch_recordings(status) == 0
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["mode"] == "record"
    assert payload["file"] == "cli"


def test_off_recovers_from_a_replay_file_that_became_invalid(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state = home / ".openprogram"
    state.mkdir(parents=True)
    missing = state / "recordings" / "missing.jsonl"
    (state / "config.json").write_text(
        json.dumps({"record_replay": {"mode": "replay", "file": str(missing)}}),
        encoding="utf-8",
    )
    (state / "config.json").chmod(0o600)
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}

    result = subprocess.run(
        [sys.executable, "-m", "openprogram", "recordings", "off"],
        cwd=Path(__file__).resolve().parents[3],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads((state / "config.json").read_text())["record_replay"]["mode"] == "off"


def test_recordings_is_not_classified_as_a_tui_invocation() -> None:
    from openprogram.cli import _looks_like_tui_invocation

    assert not _looks_like_tui_invocation(["recordings", "record"])


def test_destructive_dispatch_requires_yes_when_not_a_tty(
    recording_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.cli import build_parser

    create_managed_recording("delete-me")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    args = build_parser().parse_args(["recordings", "delete", "delete-me"])

    assert dispatch_recordings(args) == 2
    assert (paths.get_recordings_dir() / "delete-me.jsonl").exists()

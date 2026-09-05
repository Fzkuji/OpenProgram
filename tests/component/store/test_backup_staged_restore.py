"""Behavior tests for staged restore, its journal, and crash recovery."""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest


def _state(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    root.mkdir()
    os.chmod(root, 0o700)
    return root


def _wait_for_path(path: Path) -> None:
    deadline = time.monotonic() + 5
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {path}")
        time.sleep(0.01)


def _archive(tmp_path: Path, members: dict[str, bytes], *, manifest: bytes | None = None) -> Path:
    from openprogram.cli.commands.backup import _MANIFEST_NAME

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
    from openprogram.cli.commands.backup import restore_archive

    state = _state(tmp_path)
    (state / "config.json").write_text('{"keep": true}')
    archive = _archive(tmp_path, {"../escape.json": b"{}", "config.json": b"{}"})

    with pytest.raises(tarfile.TarError):
        restore_archive(archive, state)

    assert json.loads((state / "config.json").read_text()) == {"keep": True}
    assert not (tmp_path / "escape.json").exists()


def test_restore_rejects_symlink_member(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import _MANIFEST_NAME, restore_archive

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
    from openprogram.cli.commands.backup import _MANIFEST_NAME, restore_archive

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


def test_restore_rejects_duplicate_member_names(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import _MANIFEST_NAME, restore_archive

    state = _state(tmp_path)
    archive = tmp_path / "duplicate.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for payload in (b'{"value": 1}', b'{"value": 2}'):
            info = tarfile.TarInfo("config.json")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        body = json.dumps(
            {"format_version": 1, "credential_opt_in": False}
        ).encode()
        manifest = tarfile.TarInfo(_MANIFEST_NAME)
        manifest.size = len(body)
        tar.addfile(manifest, io.BytesIO(body))

    with pytest.raises(tarfile.TarError, match="duplicate"):
        restore_archive(archive, state)

    assert not (state / "config.json").exists()


def test_restore_rejects_a_missing_manifest(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import restore_archive

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
    from openprogram.cli.commands.backup import restore_archive

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
    from openprogram.cli.commands.backup import restore_archive

    state = _state(tmp_path)
    (state / "config.json").write_text('{"keep": true}')
    archive = _archive(tmp_path, {"config.json": b"this is not json"})

    with pytest.raises(tarfile.TarError):
        restore_archive(archive, state)

    assert json.loads((state / "config.json").read_text()) == {"keep": True}


def test_restore_rejects_secret_members_when_manifest_denies_opt_in(
    tmp_path: Path,
) -> None:
    from openprogram.cli.commands.backup import restore_archive

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


def test_restore_rejects_unknown_credential_tree_member_without_publishing(
    tmp_path: Path,
) -> None:
    from openprogram.cli.commands.backup import restore_archive

    state = _state(tmp_path)
    (state / "config.json").write_text('{"keep": true}')
    (state / "config.json").chmod(0o600)
    archive = _archive(
        tmp_path,
        {
            "config.json": b'{"keep": false}',
            "auth/openai/unknown.txt": b"secret",
        },
        manifest=json.dumps(
            {"format_version": 1, "credential_opt_in": True}
        ).encode(),
    )

    with pytest.raises(tarfile.TarError, match="credential inventory"):
        restore_archive(archive, state)

    assert json.loads((state / "config.json").read_text()) == {"keep": True}
    assert not (state / "auth").exists()


def test_restored_secret_files_are_published(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import restore_archive

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

    assert (state / "config.json").read_bytes() == b'{"api_keys": {}}'
    assert (state / "auth/openai/default.json").read_bytes() == (
        b'{"credentials": []}'
    )


def test_restore_preserves_local_secrets_for_redacted_fields(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import restore_archive

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


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock semantics")
def test_failed_restore_does_not_rollback_a_concurrent_public_writer(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    state = home / ".openprogram"
    state.mkdir(parents=True, mode=0o700)
    target = state / "config.json"
    target.write_text('{"generation": "old"}')
    target.chmod(0o600)
    archive = _archive(tmp_path / "backup", {"config.json": b'{"generation": "restored"}'})
    published = tmp_path / "published"
    attempted = tmp_path / "attempted"
    release = tmp_path / "release"
    env = {**os.environ, "HOME": os.fspath(home)}
    restore_script = """
import sys, time
from pathlib import Path
from openprogram.cli.commands import backup
archive, state, published, release = map(Path, sys.argv[1:])
real_publish = backup._publish_restored
def fail_after_publish(target, payload, *, root):
    real_publish(target, payload, root=root)
    published.write_text('published')
    while not release.exists():
        time.sleep(0.01)
    raise OSError('injected failure')
backup._publish_restored = fail_after_publish
backup.restore_archive(archive, state)
"""
    writer_script = """
import sys
from pathlib import Path
from openprogram.setup import _write_config
attempted = Path(sys.argv[1])
attempted.write_text('attempted')
_write_config({'generation': 'concurrent'})
"""
    restorer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            restore_script,
            os.fspath(archive),
            os.fspath(state),
            os.fspath(published),
            os.fspath(release),
        ],
        cwd=Path(__file__).parents[3],
        env=env,
    )
    writer: subprocess.Popen | None = None
    try:
        _wait_for_path(published)
        writer = subprocess.Popen(
            [sys.executable, "-c", writer_script, os.fspath(attempted)],
            cwd=Path(__file__).parents[3],
            env=env,
        )
        _wait_for_path(attempted)
        time.sleep(0.2)
        assert writer.poll() is None
        release.write_text("release")
        assert restorer.wait(timeout=10) != 0
        assert writer.wait(timeout=10) == 0
        assert json.loads(target.read_text()) == {"generation": "concurrent"}
    finally:
        release.write_text("release")
        for process in (restorer, writer):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock semantics")
def test_recovery_does_not_rollback_a_concurrent_public_writer(
    tmp_path: Path,
) -> None:
    from openprogram.cli.commands.backup import restore_journal_path

    home = tmp_path / "home"
    state = home / ".openprogram"
    state.mkdir(parents=True, mode=0o700)
    target = state / "config.json"
    target.write_text('{"generation": "restored"}')
    target.chmod(0o600)
    backup_dir = state / ".restore-journal.d"
    backup_dir.mkdir(mode=0o700)
    previous = backup_dir / "00000000.previous"
    previous.write_text('{"generation": "old"}')
    previous.chmod(0o600)
    journal = restore_journal_path(state)
    journal.write_text(
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
    journal.chmod(0o600)
    recovering = tmp_path / "recovering"
    attempted = tmp_path / "recovery-writer-attempted"
    release = tmp_path / "recovery-release"
    env = {**os.environ, "HOME": os.fspath(home)}
    recovery_script = """
import sys, time
from pathlib import Path
from openprogram.cli.commands import backup
state, recovering, release = map(Path, sys.argv[1:])
real_restore = backup._restore_opened_source
def paused_restore(*args):
    recovering.write_text('recovering')
    while not release.exists():
        time.sleep(0.01)
    real_restore(*args)
backup._restore_opened_source = paused_restore
backup.recover_interrupted_restore(state)
"""
    writer_script = """
import sys
from pathlib import Path
from openprogram.setup import _write_config
attempted = Path(sys.argv[1])
attempted.write_text('attempted')
_write_config({'generation': 'concurrent'})
"""
    recovery = subprocess.Popen(
        [
            sys.executable,
            "-c",
            recovery_script,
            os.fspath(state),
            os.fspath(recovering),
            os.fspath(release),
        ],
        cwd=Path(__file__).parents[3],
        env=env,
    )
    writer: subprocess.Popen | None = None
    try:
        _wait_for_path(recovering)
        writer = subprocess.Popen(
            [sys.executable, "-c", writer_script, os.fspath(attempted)],
            cwd=Path(__file__).parents[3],
            env=env,
        )
        _wait_for_path(attempted)
        time.sleep(0.2)
        assert writer.poll() is None
        release.write_text("release")
        assert recovery.wait(timeout=10) == 0
        assert writer.wait(timeout=10) == 0
        assert json.loads(target.read_text()) == {"generation": "concurrent"}
    finally:
        release.write_text("release")
        for process in (recovery, writer):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_restore_leaves_no_staging_directory_behind(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import restore_archive

    state = _state(tmp_path)
    archive = _archive(tmp_path, {"config.json": b"{}"})

    restore_archive(archive, state)

    leftovers = [p.name for p in state.parent.iterdir() if "restore" in p.name.lower()]
    assert leftovers == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock semantics")
def test_restore_state_lock_reports_busy_across_processes(tmp_path: Path) -> None:
    from openprogram.cli.commands.backup import RestoreBusyError, _restore_state_lock

    state = _state(tmp_path)
    code = (
        "import sys; from pathlib import Path; "
        "from openprogram.cli.commands.backup import _restore_state_lock; "
        "lock=_restore_state_lock(Path(sys.argv[1])); lock.__enter__(); "
        "print('ready', flush=True); sys.stdin.read(1); lock.__exit__(None,None,None)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(state)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    try:
        with pytest.raises(RestoreBusyError):
            with _restore_state_lock(state):
                pass
    finally:
        assert process.stdin is not None
        process.stdin.write("x")
        process.stdin.flush()
        process.wait(5)
    assert process.returncode == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock and SIGKILL semantics")
@pytest.mark.parametrize("pause_count", [1, 2, 3])
def test_killed_restore_recovers_old_state_and_releases_lock(
    tmp_path: Path, pause_count: int
) -> None:
    from openprogram.cli.commands.backup import (
        RestoreBusyError,
        _restore_state_lock,
        recover_interrupted_restore,
        restore_archive,
    )

    state = _state(tmp_path)
    old = {"a.json": b"old-a", "b.json": b"old-b", "c.json": b"old-c"}
    for name, payload in old.items():
        path = state / name
        path.write_bytes(payload)
        path.chmod(0o600)
    archive = _archive(tmp_path, {name: b"new" for name in old})
    marker = tmp_path / "paused"
    code = (
        "import sys,time; from pathlib import Path; "
        "from openprogram.cli.commands import backup as b; "
        "state,archive,marker,n=Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]),int(sys.argv[4]); "
        "real=b._publish_restored; count=[0]; "
        "exec(\"def publish(target,payload,*,root):\\n count[0]+=1\\n real(target,payload,root=root)\\n if count[0]==n:\\n  marker.write_text('paused')\\n  while True: time.sleep(1)\"); "
        "b._publish_restored=publish; "
        "b.restore_archive(archive,state)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(state), str(archive), str(marker), str(pause_count)]
    )
    deadline = time.time() + 10
    while not marker.exists() and process.poll() is None and time.time() < deadline:
        time.sleep(0.01)
    assert marker.exists()
    try:
        paused = {name: (state / name).read_bytes() for name in old}
        with pytest.raises(RestoreBusyError):
            restore_archive(archive, state)
        with pytest.raises(RestoreBusyError):
            recover_interrupted_restore(state)
        assert {name: (state / name).read_bytes() for name in old} == paused
        process.send_signal(signal.SIGKILL)
        process.wait(5)
        assert recover_interrupted_restore(state) is True
        assert {name: (state / name).read_bytes() for name in old} == old
        assert not (state / ".restore-journal.json").exists()
        assert not (state / ".restore-journal.d").exists()
        with _restore_state_lock(state):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(5)


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock and SIGKILL semantics")
@pytest.mark.parametrize("finish_phase", ["before_discard", "after_discard"])
def test_killed_successful_restore_at_finish_boundary_keeps_new_state(
    tmp_path: Path, finish_phase: str
) -> None:
    from openprogram.cli.commands.backup import (
        RestoreBusyError,
        _restore_state_lock,
        recover_interrupted_restore,
        restore_archive,
    )

    state = _state(tmp_path)
    old = {"a.json": b"old-a", "b.json": b"old-b"}
    new = {"a.json": b"new-a", "b.json": b"new-b"}
    for name, payload in old.items():
        path = state / name
        path.write_bytes(payload)
        path.chmod(0o600)
    archive = _archive(tmp_path, new)
    marker = tmp_path / "paused"
    code = (
        "import sys,time; from pathlib import Path; "
        "from openprogram.cli.commands import backup as b; "
        "state,archive,marker,phase=Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4]; "
        "real_discard=b._RestoreJournal.discard; "
        "exec(\"def discard(self):\\n if phase=='after_discard': real_discard(self)\\n marker.write_text('paused')\\n while True: time.sleep(1)\"); "
        "b._RestoreJournal.discard=discard; b.restore_archive(archive,state)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(state), str(archive), str(marker), finish_phase]
    )
    deadline = time.time() + 10
    while not marker.exists() and process.poll() is None and time.time() < deadline:
        time.sleep(0.01)
    assert marker.exists()
    try:
        assert {name: (state / name).read_bytes() for name in new} == new
        with pytest.raises(RestoreBusyError):
            restore_archive(archive, state)
        with pytest.raises(RestoreBusyError):
            recover_interrupted_restore(state)
        assert {name: (state / name).read_bytes() for name in new} == new
        process.send_signal(signal.SIGKILL)
        process.wait(5)
        assert recover_interrupted_restore(state) is False
        assert {name: (state / name).read_bytes() for name in new} == new
        assert not (state / ".restore-journal.json").exists()
        assert not (state / ".restore-journal.d").exists()
        with _restore_state_lock(state):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(5)


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock and SIGKILL semantics")
@pytest.mark.parametrize("discard_phase", ["before", "after"])
def test_killed_restore_at_discard_boundary_keeps_old_state_and_releases_lock(
    tmp_path: Path, discard_phase: str
) -> None:
    from openprogram.cli.commands.backup import (
        RestoreBusyError,
        _restore_state_lock,
        recover_interrupted_restore,
        restore_archive,
    )

    state = _state(tmp_path)
    old = {"a.json": b"old-a", "b.json": b"old-b"}
    for name, payload in old.items():
        path = state / name
        path.write_bytes(payload)
        path.chmod(0o600)
    archive = _archive(tmp_path, {name: b"new" for name in old})
    marker = tmp_path / "paused"
    code = (
        "import sys,time; from pathlib import Path; "
        "from openprogram.cli.commands import backup as b; "
        "state,archive,marker,phase=Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4]; "
        "real_publish=b._publish_restored; "
        "exec(\"def publish(target,payload,*,root):\\n real_publish(target,payload,root=root)\\n raise OSError('injected publish failure')\"); "
        "b._publish_restored=publish; real_discard=b._RestoreJournal.discard; "
        "exec(\"def discard(self):\\n if phase=='after': real_discard(self)\\n marker.write_text('paused')\\n while True: time.sleep(1)\"); "
        "b._RestoreJournal.discard=discard; b.restore_archive(archive,state)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(state), str(archive), str(marker), discard_phase]
    )
    deadline = time.time() + 10
    while not marker.exists() and process.poll() is None and time.time() < deadline:
        time.sleep(0.01)
    assert marker.exists()
    try:
        assert {name: (state / name).read_bytes() for name in old} == old
        with pytest.raises(RestoreBusyError):
            restore_archive(archive, state)
        with pytest.raises(RestoreBusyError):
            recover_interrupted_restore(state)
        assert {name: (state / name).read_bytes() for name in old} == old
        process.send_signal(signal.SIGKILL)
        process.wait(5)
        assert recover_interrupted_restore(state) is (discard_phase == "before")
        assert {name: (state / name).read_bytes() for name in old} == old
        assert not (state / ".restore-journal.json").exists()
        assert not (state / ".restore-journal.d").exists()
        with _restore_state_lock(state):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(5)


# --- journal, rollback, and crash recovery ---------------------------------


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_restore_rejects_existing_target_symlink_without_changing_its_type(
    tmp_path: Path,
) -> None:
    from openprogram.cli.commands.backup import restore_archive

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

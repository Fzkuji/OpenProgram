"""Restore locks, concurrent writers and process interruption."""
from __future__ import annotations
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import pytest

from tests.integration.store.backup.restore_support import (
    _state,
    _wait_for_path,
    _archive,
)


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
        cwd=Path(__file__).parents[4],
        env=env,
    )
    writer: subprocess.Popen | None = None
    try:
        _wait_for_path(published)
        writer = subprocess.Popen(
            [sys.executable, "-c", writer_script, os.fspath(attempted)],
            cwd=Path(__file__).parents[4],
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
        cwd=Path(__file__).parents[4],
        env=env,
    )
    writer: subprocess.Popen | None = None
    try:
        _wait_for_path(recovering)
        writer = subprocess.Popen(
            [sys.executable, "-c", writer_script, os.fspath(attempted)],
            cwd=Path(__file__).parents[4],
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

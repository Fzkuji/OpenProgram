"""Exercise durable installer transactions on fixture Apps, never the live App."""
import json
import os
from pathlib import Path
import plistlib
import subprocess

import pytest

from tests.component.config.test_distribution_release import MACOS_DESKTOP_INSTALL, _fake_desktop_app

pytestmark = [pytest.mark.macos, MACOS_DESKTOP_INSTALL]
INSTALLER = Path(__file__).resolve().parents[3] / "apps/desktop/scripts/install-app.sh"


@pytest.fixture
def installation(tmp_path):
    env = {"DESTDIR": str(tmp_path / "root"), "HOME": str(tmp_path / "home"),
           "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TMPDIR": str(tmp_path / "tmp")}
    Path(env["TMPDIR"]).mkdir()
    old = _fake_desktop_app(tmp_path / "old", "0.6.1")
    candidate = _fake_desktop_app(tmp_path / "new", "0.6.2")
    def run(*args, check=True, extra_env=None):
        result = subprocess.run(["bash", str(INSTALLER), *map(str, args)], env={**env, **(extra_env or {})},
                                capture_output=True, text=True, timeout=30)
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result
    run(old)
    target = Path(env["DESTDIR"]) / "Applications/OpenProgram.app"
    yield run, candidate, target, tmp_path


def prepare(run, candidate):
    result = run("--prepare", candidate)
    rows = [line.removeprefix("OPENPROGRAM_TRANSACTION_DIR=") for line in result.stdout.splitlines()
            if line.startswith("OPENPROGRAM_TRANSACTION_DIR=")]
    assert len(rows) == 1
    return Path(rows[0])


def version(app):
    return plistlib.loads((app / "Contents/Info.plist").read_bytes())["CFBundleShortVersionString"]


def phase(transaction):
    return json.loads((transaction / "transaction.json").read_text())["phase"]


@pytest.mark.parametrize("action", ["commit", "rollback"])
def test_prepare_activate_and_idempotent_terminal_action(installation, action):
    run, candidate, target, _ = installation
    tx = prepare(run, candidate)
    assert phase(tx) == "prepared"
    assert version(target) == "0.6.1"
    assert not (tx / "previous.app").exists()
    run("--activate", tx)
    assert phase(tx) == "activated"
    assert version(target) == "0.6.2"
    assert version(tx / "previous.app") == "0.6.1"
    run("--" + action, tx)
    run("--" + action, tx)
    assert phase(tx) == ("committed" if action == "commit" else "rolled_back")
    assert version(target) == ("0.6.2" if action == "commit" else "0.6.1")
    assert not (tx / "previous.app").exists()
    opposite = "--rollback" if action == "commit" else "--commit"
    assert run(opposite, tx, check=False).returncode != 0


@pytest.mark.parametrize("changed", ["old", "candidate"])
def test_activation_rejects_identity_drift_without_replacing_old(installation, changed):
    run, candidate, target, _ = installation
    tx = prepare(run, candidate)
    app = target if changed == "old" else tx / "OpenProgram.app"
    (app / "drift").write_text("changed")
    assert run("--activate", tx, check=False).returncode != 0
    assert version(target) == "0.6.1"
    assert phase(tx) == "prepared"


@pytest.mark.parametrize("action", ["activate", "rollback"])
@pytest.mark.parametrize("rename", ["first", "second"])
def test_rollback_after_installer_is_killed_after_actual_rename(installation, rename, action):
    run, candidate, target, tmp = installation
    tx = prepare(run, candidate)
    if action == "rollback":
        run("--activate", tx)
    shim = tmp / "shim"
    shim.mkdir()
    mv = shim / "mv"
    mv.write_text('#!/bin/bash\n/bin/mv "$@" || exit $?\n'
                  'if [[ "${@: -1}" == "$KILL_AFTER_RENAME" ]]; then kill -KILL "$PPID"; fi\n')
    mv.chmod(0o755)
    destination = tx / ("previous.app" if action == "activate" else "failed.app") if rename == "first" else target
    result = run("--" + action, tx, check=False, extra_env={
        "PATH": str(shim) + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
        "KILL_AFTER_RENAME": str(destination),
    })
    assert result.returncode == -9, result.stderr
    owner = int((target.parent / ".openprogram-app-install.lock").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(owner, 0)
    assert phase(tx) == ("activating" if action == "activate" else "rolling_back")
    old_location = target if action == "rollback" and rename == "second" else tx / "previous.app"
    assert version(old_location) == "0.6.1"
    run("--rollback", tx)
    run("--rollback", tx)
    assert version(target) == "0.6.1"
    assert phase(tx) == "rolled_back"


@pytest.mark.parametrize("partial", [False, True])
def test_commit_can_resume_after_previous_app_is_removed(installation, partial):
    run, candidate, target, tmp = installation
    tx = prepare(run, candidate)
    run("--activate", tx)
    shim = tmp / "shim"
    shim.mkdir()
    rm = shim / "rm"
    remove = '/bin/rm "$KILL_AFTER_REMOVE/Contents/Info.plist"' if partial else '/bin/rm "$@"'
    rm.write_text('#!/bin/bash\nif [[ "${@: -1}" == "$KILL_AFTER_REMOVE" ]]; then\n'
                  + remove + ' || exit $?\nkill -KILL "$PPID"\nelse /bin/rm "$@"; fi\n')
    rm.chmod(0o755)
    result = run("--commit", tx, check=False, extra_env={
        "PATH": str(shim) + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
        "KILL_AFTER_REMOVE": str(tx / "previous.app"),
    })
    assert result.returncode == -9
    owner = int((target.parent / ".openprogram-app-install.lock").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(owner, 0)
    assert phase(tx) == "committing"
    run("--commit", tx)
    assert phase(tx) == "committed"
    assert version(target) == "0.6.2"


@pytest.mark.parametrize("changed", ["active", "previous"])
def test_rollback_preserves_unrecognized_app_state(installation, changed):
    run, candidate, target, _ = installation
    tx = prepare(run, candidate)
    run("--activate", tx)
    app = target if changed == "active" else tx / "previous.app"
    (app / "drift").write_text("changed")
    assert run("--rollback", tx, check=False).returncode != 0
    assert phase(tx) == "activated"
    assert version(target) == "0.6.2"
    assert version(tx / "previous.app") == "0.6.1"


def test_live_installer_lock_is_not_removed(installation):
    run, candidate, target, _ = installation
    tx = prepare(run, candidate)
    lock = target.parent / ".openprogram-app-install.lock"
    lock.write_text(str(os.getpid()) + "\n")
    assert run("--activate", tx, check=False).returncode != 0
    assert lock.read_text().strip() == str(os.getpid())
    assert phase(tx) == "prepared"
    assert version(target) == "0.6.1"


@pytest.mark.parametrize("terminal", ["prepared", "committed", "rolled_back"])
def test_terminal_identity_check_is_read_only_and_phase_bound(installation, terminal):
    from openprogram.self_update.supervisor import _tree_digest
    run, candidate, target, _ = installation
    tx = prepare(run, candidate)
    if terminal != "prepared":
        run("--activate", tx)
        run("--commit" if terminal == "committed" else "--rollback", tx)
    before = _tree_digest(target), _tree_digest(tx)
    for _ in range(2):
        result = run("--verify-terminal:" + terminal, tx)
        assert f"OPENPROGRAM_TRANSACTION_DIR={tx}" in result.stdout
        assert (_tree_digest(target), _tree_digest(tx)) == before
    other = "prepared" if terminal != "prepared" else "committed"
    assert run("--verify-terminal:" + other, tx, check=False).returncode != 0
    (target / "drift").write_text("changed")
    assert run("--verify-terminal:" + terminal, tx, check=False).returncode != 0
    assert _tree_digest(tx) == before[1]

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native PowerShell installer")


@pytest.fixture
def install_fixture(tmp_path: Path):
    state = tmp_path / "state"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for suffix in ("ps1", "cmd"):
        (bin_dir / f"openprogram.{suffix}").write_text("previous launcher", encoding="ascii")
    release = state / "runtime" / "cli" / "releases" / "9.9.7"
    events = tmp_path / "events.txt"
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w") as payload:
        payload.writestr("runtime/runtime-manifest.json", json.dumps({"python": "bin/python.cmd"}))
        payload.writestr("runtime/bin/verify-product-runtime.py", "fixture marker")
        # No Python worker is started: only this harmless native batch process
        # runs. Extraction, verifier ordering, publication and locking are real.
        payload.writestr("runtime/bin/python.cmd", "\r\n".join([
            "@echo off",
            'if /i "%~nx2"=="verify-product-runtime.py" (',
            '  echo verify>>"%OP_TEST_EVENTS%"',
            '  if exist "%OP_TEST_RELEASE%" if not "%OP_TEST_REUSE%"=="1" exit /b 40',
            '  if "%OP_TEST_FAILURE%"=="verify" exit /b 41',
            ")",
            'if "%~5"=="worker" echo %~6>>"%OP_TEST_EVENTS%"',
            "exit /b 0", "",
        ]))
    source = (ROOT / "scripts/release/install-release.ps1").read_text(encoding="utf-8")
    # Replace only the network/worker probe boundary, leaving the real
    # installer flow and native process exit-code checking in place.
    probe = """
function Test-WorkerHealth {
    param([string]$PythonBin, [int]$Port)
    Add-Content -LiteralPath $env:OP_TEST_EVENTS -Value 'health' -Encoding ASCII
    if ($env:OP_TEST_REUSE -ne '1' -and (Test-Path -LiteralPath $env:OP_TEST_RELEASE)) {
        throw 'candidate was published before its health probe'
    }
    if ($env:OP_TEST_FAILURE -eq 'health') { throw 'injected health failure' }
    if ($env:OP_TEST_FAILURE -eq 'publish') {
        [IO.File]::WriteAllText($env:OP_TEST_RELEASE, 'publication collision')
    }
}
"""
    script = tmp_path / "install.ps1"
    script.write_text(source.replace("$Version = if", probe + "\n$Version = if", 1), encoding="utf-8-sig")
    env = {
        **os.environ,
        "OPENPROGRAM_VERSION": "9.9.7",
        "OPENPROGRAM_RUNTIME_ARCHIVE": str(archive),
        "OPENPROGRAM_RUNTIME_SHA256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "OPENPROGRAM_STATE_DIR": str(state),
        "OPENPROGRAM_BIN_DIR": str(bin_dir),
        "OP_TEST_RELEASE": str(release),
        "OP_TEST_EVENTS": str(events),
        "OP_TEST_FAILURE": "",
        "OP_TEST_REUSE": "",
    }

    def run(*, failure="", reuse=False, target=script):
        return subprocess.run(
            [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(target)],
            env={**env, "OP_TEST_FAILURE": failure, "OP_TEST_REUSE": "1" if reuse else ""},
            capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        )

    return run, release, bin_dir, events, script


@pytest.mark.parametrize("failure", ["verify", "health"])
def test_failed_candidate_is_not_published_and_can_be_retried(install_fixture, failure):
    run, release, bin_dir, events, _script = install_fixture
    failed = run(failure=failure)
    assert failed.returncode != 0
    assert not release.exists()
    assert not list(release.parent.parent.glob(".staging-*"))
    for suffix in ("ps1", "cmd"):
        assert (bin_dir / f"openprogram.{suffix}").read_text() == "previous launcher"
    assert "verify" in events.read_text()

    retried = run()
    assert retried.returncode == 0, (retried.stdout, retried.stderr)
    assert (release / "runtime-manifest.json").is_file()
    for suffix in ("ps1", "cmd"):
        launcher = (bin_dir / f"openprogram.{suffix}").read_text(encoding="utf-8-sig")
        assert str(release) in launcher
        assert ".staging-" not in launcher
        assert (bin_dir / f"openprogram.previous.{suffix}").read_text() == "previous launcher"
    assert not list(release.parent.parent.glob(".staging-*"))


def test_verified_cached_release_is_revalidated_before_activation(install_fixture):
    run, release, bin_dir, _events, _script = install_fixture
    first = run()
    assert first.returncode == 0, (first.stdout, first.stderr)
    before = (bin_dir / "openprogram.ps1").read_bytes()
    failed = run(failure="verify", reuse=True)
    assert failed.returncode != 0
    assert release.is_dir()
    assert (bin_dir / "openprogram.ps1").read_bytes() == before
    assert run(reuse=True).returncode == 0


def test_failed_publication_keeps_launchers_and_preserves_collision(install_fixture):
    run, release, bin_dir, events, _script = install_fixture
    failed = run(failure="publish")
    assert failed.returncode != 0
    assert "health" in events.read_text()
    assert release.read_text() == "publication collision"
    assert not list(release.parent.parent.glob(".staging-*"))
    for suffix in ("ps1", "cmd"):
        assert (bin_dir / f"openprogram.{suffix}").read_text() == "previous launcher"


def test_concurrent_install_is_rejected_before_extracting_or_activating(install_fixture, tmp_path):
    run, release, bin_dir, events, script = install_fixture
    runtime = release.parent.parent
    runtime.mkdir(parents=True)
    driver = tmp_path / "locked.ps1"
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    driver.write_text(
        f"$testLock=[IO.File]::Open({quote(runtime / '.install.lock')},'OpenOrCreate','ReadWrite','None')\n"
        "try {\n"
        f"& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File {quote(script)}\n"
        "$testExit=$LASTEXITCODE\n"
        "} finally { $testLock.Dispose() }\nexit $testExit\n",
        encoding="utf-8-sig",
    )
    locked = run(target=driver)
    assert locked.returncode != 0
    assert not events.exists()
    assert not release.exists()
    assert (bin_dir / "openprogram.ps1").read_text() == "previous launcher"
    assert run().returncode == 0

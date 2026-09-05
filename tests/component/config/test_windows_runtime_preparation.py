"""Runtime preparation publishes only successful builds and restores failures."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows packaging")


@pytest.mark.parametrize("archive", [False, True])
@pytest.mark.parametrize("outcome", ["success", "build-failure", "wrong-version", "publish-failure", "rollback-failure", "cleanup-failure", "long-path", "junction"])
def test_runtime_preparation_retains_last_good_payload(tmp_path, archive, outcome):
    shell = shutil.which("powershell.exe")
    assert shell, "Windows packaging requires PowerShell"
    repo = tmp_path / "checkout"
    scripts = repo / "scripts" / "release"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/release/prepare-desktop-runtime.ps1", scripts)
    desktop = repo / "apps" / "desktop"
    old = desktop / "build" / "runtime"
    old.mkdir(parents=True)
    (old / "original.txt").write_text("keep me")
    external = tmp_path / "external"
    external.mkdir()
    (external / "sentinel.txt").write_text("never remove the junction target")
    if outcome == "long-path":
        deep = old
        while len(str(deep / "readonly.dat")) < 290:
            deep /= "long-component-" + "x" * 30
        extended = Path("\\\\?\\" + str(deep))
        extended.mkdir(parents=True)
        payload = extended / "readonly.dat"
        payload.write_bytes(b"old long-path payload")
        payload.chmod(0o400)
    (desktop / "package.json").write_text(json.dumps({"version": "0.8.1"}))
    builder = '''
$Target = if ($env:TEST_ARCHIVE -eq '1') {
    Join-Path $env:OPENPROGRAM_STATE_DIR 'runtime\\cli\\releases\\0.8.1'
} else { $env:OPENPROGRAM_RUNTIME_ROOT }
if ($env:TEST_OUTCOME -eq 'junction') {
    $Old = Join-Path $PSScriptRoot '..\\..\\apps\\desktop\\build\\runtime'
    New-Item -ItemType Junction -Path (Join-Path $Old 'external-link') -Target $env:TEST_EXTERNAL_ROOT | Out-Null
}
New-Item -ItemType Directory -Path $Target -Force | Out-Null
Set-Content -LiteralPath (Join-Path $Target 'new.txt') -Value 'replacement'
if ($env:TEST_OUTCOME -eq 'build-failure') { throw 'injected build failure' }
$Version = if ($env:TEST_OUTCOME -eq 'wrong-version') { '0.0.0' } else { '0.8.1' }
@{openprogram=$Version} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Target 'runtime-manifest.json')
'''
    for name in ("build-product-runtime.ps1", "install-release.ps1"):
        (scripts / name).write_text(builder, encoding="utf-8")
    driver = repo / "test.ps1"
    driver.write_text('''
$ErrorActionPreference = 'Stop'
$script:BackupReadLock = $null
function Move-Item {
    param([string]$LiteralPath, [string]$Destination)
    if ($env:TEST_OUTCOME -in @('publish-failure', 'rollback-failure') -and
        $Destination.EndsWith('build\\runtime') -and
        ($env:TEST_OUTCOME -eq 'rollback-failure' -or -not $LiteralPath.EndsWith('previous-runtime'))) {
        throw 'injected publication failure'
    }
    Microsoft.PowerShell.Management\\Move-Item @PSBoundParameters
    if ($env:TEST_OUTCOME -eq 'cleanup-failure' -and $Destination.EndsWith('previous-runtime')) {
        $script:BackupReadLock = [IO.File]::Open((Join-Path $Destination 'original.txt'),
            [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    }
}
try { & (Join-Path $PSScriptRoot 'scripts\\release\\prepare-desktop-runtime.ps1') }
finally {
    if ($script:BackupReadLock) { $script:BackupReadLock.Dispose() }
    if ($env:OPENPROGRAM_RUNTIME_ROOT -ne 'preserve-original-environment') {
        throw 'caller environment was not restored'
    }
    $Lock = [IO.File]::Open((Join-Path $PSScriptRoot 'apps\\desktop\\build\\.runtime-prepare.lock'),
        [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $Lock.Dispose()
}
''', encoding="utf-8")
    env = dict(os.environ, TEST_ARCHIVE="1" if archive else "0", TEST_OUTCOME=outcome,
               TEST_EXTERNAL_ROOT=str(external),
               OPENPROGRAM_RUNTIME_ROOT="preserve-original-environment")
    env.pop("OPENPROGRAM_RUNTIME_ARCHIVE", None)
    if archive:
        zip_path = tmp_path / "runtime.zip"
        zip_path.write_bytes(b"fixture")
        env["OPENPROGRAM_RUNTIME_ARCHIVE"] = str(zip_path)
    result = subprocess.run([shell, "-NoLogo", "-NoProfile", "-NonInteractive",
                             "-ExecutionPolicy", "Bypass", "-File", str(driver)],
                            env=env, capture_output=True, text=True, timeout=20)
    assert (external / "sentinel.txt").read_text() == "never remove the junction target"
    if outcome in {"success", "cleanup-failure", "long-path", "junction"}:
        assert result.returncode == 0, result.stderr
        assert (old / "new.txt").is_file()
        assert not (old / "original.txt").exists()
        if outcome == "cleanup-failure":
            assert "original.txt" in result.stdout
            backups = list(old.parent.glob(".runtime-stage-*/previous-runtime/original.txt"))
            assert len(backups) == 1
            assert backups[0].read_text() == "keep me"
            return
    elif outcome == "rollback-failure":
        assert result.returncode != 0
        backups = list(old.parent.glob(".runtime-stage-*/previous-runtime/original.txt"))
        assert len(backups) == 1
        assert backups[0].read_text() == "keep me"
        assert "original retained" in result.stderr
        return
    else:
        assert result.returncode != 0
        assert (old / "original.txt").read_text() == "keep me"
        assert not (old / "new.txt").exists()
    assert not list(old.parent.glob(".runtime-stage-*"))

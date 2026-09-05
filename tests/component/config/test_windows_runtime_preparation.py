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
@pytest.mark.parametrize("outcome", ["success", "build-failure", "wrong-version", "publish-failure", "rollback-failure", "cleanup-failure"])
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
    (desktop / "package.json").write_text(json.dumps({"version": "0.8.1"}))
    builder = '''
$Target = if ($env:TEST_ARCHIVE -eq '1') {
    Join-Path $env:OPENPROGRAM_STATE_DIR 'runtime\\cli\\releases\\0.8.1'
} else { $env:OPENPROGRAM_RUNTIME_ROOT }
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
function Remove-Item {
    param([string]$LiteralPath, [switch]$Recurse, [switch]$Force)
    if ($env:TEST_OUTCOME -eq 'cleanup-failure') { throw 'injected cleanup failure' }
    Microsoft.PowerShell.Management\\Remove-Item @PSBoundParameters
}
function Move-Item {
    param([string]$LiteralPath, [string]$Destination)
    if ($env:TEST_OUTCOME -in @('publish-failure', 'rollback-failure') -and
        $Destination.EndsWith('build\\runtime') -and
        ($env:TEST_OUTCOME -eq 'rollback-failure' -or -not $LiteralPath.EndsWith('previous-runtime'))) {
        throw 'injected publication failure'
    }
    Microsoft.PowerShell.Management\\Move-Item @PSBoundParameters
}
try { & (Join-Path $PSScriptRoot 'scripts\\release\\prepare-desktop-runtime.ps1') }
finally {
    if ($env:OPENPROGRAM_RUNTIME_ROOT -ne 'preserve-original-environment') {
        throw 'caller environment was not restored'
    }
    $Lock = [IO.File]::Open((Join-Path $PSScriptRoot 'apps\\desktop\\build\\.runtime-prepare.lock'),
        [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $Lock.Dispose()
}
''', encoding="utf-8")
    env = dict(os.environ, TEST_ARCHIVE="1" if archive else "0", TEST_OUTCOME=outcome,
               OPENPROGRAM_RUNTIME_ROOT="preserve-original-environment")
    env.pop("OPENPROGRAM_RUNTIME_ARCHIVE", None)
    if archive:
        zip_path = tmp_path / "runtime.zip"
        zip_path.write_bytes(b"fixture")
        env["OPENPROGRAM_RUNTIME_ARCHIVE"] = str(zip_path)
    result = subprocess.run([shell, "-NoLogo", "-NoProfile", "-NonInteractive",
                             "-ExecutionPolicy", "Bypass", "-File", str(driver)],
                            env=env, capture_output=True, text=True, timeout=20)
    if outcome in {"success", "cleanup-failure"}:
        assert result.returncode == 0, result.stderr
        assert (old / "new.txt").is_file()
        assert not (old / "original.txt").exists()
        if outcome == "cleanup-failure":
            assert "injected cleanup failure" in result.stdout
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

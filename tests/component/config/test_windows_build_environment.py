"""PowerShell release helpers leave the caller's toolchain settings intact."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="native PowerShell build helpers")


def _run_driver(repo, env, script, keys):
    driver = repo / "test.ps1"
    driver.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$Saved = @{}\n"
        f"foreach ($Name in @({','.join(repr(key) for key in keys)})) {{\n"
        "  $Saved[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')\n"
        "}\n"
        "$Before = (Get-Location).Path\n"
        "$Failed = $false\n"
        f"try {{ & (Join-Path $PSScriptRoot 'scripts/release/{script}') }}\n"
        "catch { $Failed = $true; Write-Output $_.Exception.Message }\n"
        "if (-not $Failed) { throw 'expected injected build failure' }\n"
        "foreach ($Name in $Saved.Keys) {\n"
        "  if ([Environment]::GetEnvironmentVariable($Name, 'Process') -cne $Saved[$Name]) {\n"
        "    throw \"caller setting changed: $Name\"\n"
        "  }\n"
        "}\n"
        "if ((Get-Location).Path -ne $Before) { throw 'caller directory changed' }\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-File", str(driver)],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("failure", ["ci", "build"])
def test_asset_staging_restores_environment_after_failure(tmp_path, configured, failure):
    scripts = tmp_path / "scripts" / "release"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/release/stage-release-assets.ps1", scripts)
    fake_bin = tmp_path / "tools"
    fake_bin.mkdir()
    (fake_bin / "npm.cmd").write_text(
        '@echo off\nif "%1"=="%TEST_FAIL_COMMAND%" exit /b 17\nexit /b 0\n'
    )
    # No Python or uv command is reached before these two injected failures.
    placeholder = fake_bin / "placeholder.cmd"
    placeholder.write_text("@exit /b 19\n")
    keys = ("npm_config_workspace", "npm_config_workspaces", "NEXT_IGNORE_INCORRECT_LOCKFILE")
    env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"],
               OPENPROGRAM_UV_BIN=str(placeholder), OPENPROGRAM_BUILD_PYTHON=str(placeholder),
               TEST_FAIL_COMMAND="ci" if failure == "ci" else "run")
    for key in keys:
        env.pop(key, None)
        if configured:
            env[key] = "caller-setting"
    output = _run_driver(tmp_path, env, "stage-release-assets.ps1", keys)
    assert "exited with code 17" in output


@pytest.mark.parametrize("configured", [False, True])
def test_runtime_builder_restores_tool_overrides_after_staging_failure(tmp_path, configured):
    scripts = tmp_path / "scripts" / "release"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/release/build-product-runtime.ps1", scripts)
    (scripts / "stage-release-assets.ps1").write_text("throw 'injected staging failure'\n")
    fake_bin = tmp_path / ".venv" / "Scripts"
    fake_bin.mkdir(parents=True)
    # PowerShell invokes .cmd explicitly when overrides are configured; the
    # fallback path must be a real executable, so reuse this checkout's uv.
    uv = ROOT / ".venv" / "Scripts" / "uv.exe"
    if not uv.is_file():
        uv_command = shutil.which("uv.exe")
        assert uv_command, "Windows release CI requires uv"
        uv = Path(uv_command)
    uv_version = subprocess.check_output([str(uv), "--version"], text=True).split()[1]
    (scripts / "product-runtime.json").write_text(json.dumps({"python": "3.12.10", "uv": uv_version}))
    shutil.copy2(uv, fake_bin / "uv.exe")
    shutil.copy2(Path(os.sys.executable), fake_bin / "python.exe")
    keys = ("OPENPROGRAM_UV_BIN", "OPENPROGRAM_BUILD_PYTHON")
    env = dict(os.environ)
    env.pop("OPENPROGRAM_RUNTIME_ROOT", None)
    for key, executable in zip(keys, ("uv.exe", "python.exe")):
        env.pop(key, None)
        if configured:
            env[key] = str(fake_bin / executable)
    output = _run_driver(tmp_path, env, "build-product-runtime.ps1", keys)
    assert "injected staging failure" in output

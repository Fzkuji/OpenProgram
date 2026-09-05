from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows launchers")


def _ps(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture
def launcher_runtime(tmp_path: Path) -> tuple[Path, Path]:
    # Exercise actual Python executable loading, not a mocked shell result.
    runtime = tmp_path / "开发 space %OP_LAUNCHER_SENTINEL% ! &' Ω"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(runtime)],
        check=True, capture_output=True, timeout=60,
    )
    package = runtime / "Lib" / "site-packages" / "openprogram"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json, os, sys\n"
        "print(json.dumps({'args': sys.argv[1:], 'python': sys.executable, "
        "'playwright': os.environ.get('PLAYWRIGHT_BROWSERS_PATH'), "
        "'gpa': os.environ.get('GPA_MODEL_PATH')}))\n"
        "raise SystemExit(17)\n",
        encoding="utf-8",
    )
    return runtime, runtime / "Scripts" / "python.exe"


def _generate_launchers(tmp_path: Path, runtime: Path, python: Path, flavor: str) -> Path:
    # Execute the production generator without installing packages, changing
    # the user's PATH, or starting any worker/profile during this fixture.
    source = (ROOT / "scripts/release/install-release.ps1").read_text(encoding="utf-8")
    functions = source[source.index("function Write-Utf8NoBom") : source.index("$Version =")]
    if flavor.startswith("source_"):
        source = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")
        body = source[source.index('  $launcher = Join-Path $binDir "openprogram.cmd"') :]
        body = body[:body.index("  $separator =")]
    else:
        body = source[source.index("$LauncherPs1 =") : source.index("if (-not $env:OPENPROGRAM_BIN_DIR)")]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    driver = tmp_path / "generate.ps1"
    driver.write_text(
        "$ErrorActionPreference='Stop'\n" + functions + "\n"
        f"$ReleaseDir={_ps(runtime)}\n$PythonBin={_ps(python)}\n$PY=$PythonBin\n"
        f"$BinDir={_ps(bin_dir)}\n" + body,
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-File", str(driver)],
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    return bin_dir / ("openprogram.ps1" if flavor.endswith("ps1") else "openprogram.cmd")


@pytest.mark.parametrize("flavor", ["release_ps1", "release_cmd", "source_ps1", "source_cmd"])
@pytest.mark.parametrize("codepage", [437, 936, 65001])
def test_native_launchers_preserve_unicode_paths_exit_and_console(
    tmp_path: Path, launcher_runtime: tuple[Path, Path], flavor: str, codepage: int,
) -> None:
    runtime, python = launcher_runtime
    launcher = _generate_launchers(tmp_path, runtime, python, flavor)
    if flavor.endswith("ps1"):
        invocation = (
            f'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass '
            f'-File "{launcher}" "argument with spaces"'
        )
    else:
        invocation = f'call "{launcher}" "argument with spaces"'
    wrapper = tmp_path / "invoke.cmd"
    wrapper.write_text(
        f"@echo off\r\nchcp {codepage}>nul\r\n{invocation}\r\n"
        'set "LAUNCH_EXIT=%ERRORLEVEL%"\r\nchcp\r\nexit /b %LAUNCH_EXIT%\r\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        ["cmd.exe", "/d", "/v:on", "/c", str(wrapper)],
        cwd=tmp_path,
        env={**os.environ, "OP_LAUNCHER_SENTINEL": "must-not-expand"},
        capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 17, (result.stdout, result.stderr)
    payload = next(line for line in result.stdout.splitlines() if line.startswith(b"{"))
    data = json.loads(payload)
    assert data["python"] == str(python)
    assert data["args"] == ["argument with spaces"]
    if flavor.startswith("release_"):
        assert data["playwright"] == str(runtime / "assets" / "playwright")
        assert data["gpa"] == str(runtime / "assets" / "gpa" / "model.pt")
    assert str(codepage).encode("ascii") in result.stdout.splitlines()[-1]


@pytest.mark.parametrize("flavor", ["release_ps1", "release_cmd", "source_ps1", "source_cmd"])
def test_powershell_invocation_preserves_unicode_arguments(
    tmp_path: Path, launcher_runtime: tuple[Path, Path], flavor: str,
) -> None:
    runtime, python = launcher_runtime
    launcher = _generate_launchers(tmp_path, runtime, python, flavor)
    unicode_bin = tmp_path / "启动目录 with spaces"
    launcher.parent.rename(unicode_bin)
    launcher = unicode_bin / launcher.name
    arguments = ["你好 with spaces", "trailing\\", "amp&bang!"]
    # Explicit .cmd invocation needs CMD quoting at the PowerShell boundary;
    # the normal PowerShell .ps1 launcher accepts literal arguments directly.
    invocation_args = arguments if flavor.endswith("ps1") else [
        arguments[0], arguments[1], '"' + arguments[2] + '"',
    ]
    driver = tmp_path / "invoke.ps1"
    command = "openprogram" if flavor.endswith("ps1") else _ps(launcher)
    driver.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"$env:PATH={_ps(unicode_bin)}+';'+$env:PATH\n"
        f"& {command} " + " ".join(map(_ps, invocation_args)) + "\nexit $LASTEXITCODE\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-File", str(driver)],
        cwd=tmp_path, capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 17, (result.stdout, result.stderr)
    data = json.loads(result.stdout)
    assert data["python"] == str(python)
    assert data["args"] == arguments

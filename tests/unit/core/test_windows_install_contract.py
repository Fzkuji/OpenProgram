from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]


def test_windows_installer_builds_web_and_tui_and_checks_failures() -> None:
    installer = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert "function Invoke-CheckedNative" in installer
    assert "npm.cmd\" ci --include-workspace-root" in installer
    assert "npm.cmd\" run build --workspace apps/web" in installer
    assert "npm.cmd\" run build --workspace apps/cli" in installer
    assert 'cmd /c "npm install"' not in installer
    assert installer.index("if ($Minimal)") < installer.index(
        'Step "installing web and terminal UI dependencies"'
    )


def test_windows_installer_uses_checkout_virtualenv_by_default() -> None:
    installer = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")

    resolve = installer[installer.index("function Resolve-Python") :]
    resolve = resolve[: resolve.index("$PY = Resolve-Python")]
    assert "CONDA_PREFIX" not in resolve
    assert "VIRTUAL_ENV" not in resolve
    assert '$HostRoot\\.venv\\Scripts\\python.exe' in resolve


def test_windows_installer_exposes_the_isolated_cli_on_user_path() -> None:
    installer = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert 'Join-Path $env:LOCALAPPDATA "OpenProgram\\bin"' in installer
    assert 'Join-Path $binDir "openprogram.cmd"' in installer
    assert '-m openprogram %*' in installer
    assert '[Environment]::SetEnvironmentVariable("Path", $updated, "User")' in installer
    assert '& $PY -m openprogram programs install $name' in installer


def test_windows_release_installer_replaces_stale_launchers_atomically() -> None:
    installer = (
        ROOT / "scripts" / "release" / "install-release.ps1"
    ).read_text(encoding="utf-8")

    launcher_section = installer[installer.index("$LauncherPs1 =") :]
    assert 'OPENPROGRAM_IMMUTABLE_RUNTIME = "1"' in launcher_section
    assert 'Move-Atomic $LauncherTemporary $LauncherPs1' in launcher_section
    assert 'Move-Atomic $CmdTemporary $LauncherCmd' in launcher_section
    assert '"openprogram.previous.cmd"' in launcher_section
    assert 'if (-not (Test-Path -LiteralPath $LauncherCmd' not in launcher_section
    assert "ConvertTo-CmdBatchLiteral $PythonBin" in launcher_section
    assert '"$CmdPython" -I -B -m openprogram %*' in launcher_section
    assert "powershell.exe -NoLogo" not in launcher_section


def test_packaged_runtime_smoke_detects_managed_release_without_env_marker() -> None:
    smoke = (
        ROOT / "scripts" / "release" / "smoke-packaged-runtime.ps1"
    ).read_text(encoding="utf-8")

    assert "Remove-Item Env:OPENPROGRAM_IMMUTABLE_RUNTIME" in smoke
    assert "detect_install_method() is InstallMethod.MANAGED_RELEASE" in smoke


def test_desktop_upgrade_preparation_is_path_scoped_and_long_path_aware() -> None:
    helper = (
        ROOT / "apps" / "desktop" / "build" / "prepare-upgrade.ps1"
    ).read_text(encoding="utf-8")
    installer = (
        ROOT / "apps" / "desktop" / "build" / "installer.nsh"
    ).read_text(encoding="utf-8")

    assert "OpenProgram.exe" in helper
    assert "resources\\app.asar" in helper
    assert "StartsWith(" in helper
    assert "[StringComparison]::OrdinalIgnoreCase" in helper
    assert "ConvertTo-ExtendedPath" in helper
    assert "[IO.Directory]::EnumerateFileSystemEntries" in helper
    assert "$Entry.Length - $ExtendedRoot.Length) -ge 248" in helper
    assert "[IO.FileAttributes]::ReparsePoint" in helper
    assert "prepare-upgrade.ps1" in installer
    assert "customCheckAppRunning" not in installer


def test_windows_product_runtime_uses_short_python_root_without_bytecode() -> None:
    builder = (
        ROOT / "scripts" / "release" / "build-product-runtime.ps1"
    ).read_text(encoding="utf-8")

    assert '$ShortPythonHome = Join-Path $RuntimeRoot "py"' in builder
    assert 'Move-Item -LiteralPath $PythonHome -Destination $ShortPythonHome' in builder
    assert '-Directory -Filter "__pycache__"' in builder
    assert '$_.Extension -in ".pyc", ".pyo"' in builder
    assert 'Invoke-NativeOutput $PythonBin -I -B' in builder
    assert 'Invoke-Native $PythonBin -I -B $Verifier' in builder


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer contract")
def test_desktop_upgrade_helper_stops_embedded_process_and_removes_long_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "OpenProgram"
    resources = root / "resources"
    resources.mkdir(parents=True)
    (root / "OpenProgram.exe").write_bytes(b"marker")
    (resources / "app.asar").write_bytes(b"marker")

    # Keep every component below NTFS's limit while crossing the legacy
    # MAX_PATH threshold that electron-builder's old-install move expands.
    deep = resources / "runtime"
    index = 0
    while len(str(deep / "legacy.pyc")) < 250:
        deep /= f"legacy-{index:02d}-" + ("x" * 32)
        index += 1
    legacy_file = deep / "legacy.pyc"
    extended_deep = Path("\\\\?\\" + str(deep))
    extended_deep.mkdir(parents=True)
    (extended_deep / legacy_file.name).write_bytes(b"old bytecode")
    assert len(str(legacy_file)) >= 248

    command = shutil.which("cmd.exe")
    powershell = shutil.which("powershell.exe")
    assert command and powershell
    embedded = root / "resources" / "runtime" / "worker.exe"
    shutil.copy2(command, embedded)
    worker = subprocess.Popen(
        [str(embedded), "/d", "/c", "ping -n 30 127.0.0.1 >nul"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        completed = subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(
                    ROOT
                    / "apps"
                    / "desktop"
                    / "build"
                    / "prepare-upgrade.ps1"
                ),
                "-InstallRoot",
                str(root),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout
        worker.wait(timeout=5)
        assert not os.path.exists("\\\\?\\" + str(legacy_file))
        assert "stopped 1 process(es)" in completed.stdout
        assert "removed 1 legacy long-path file(s)" in completed.stdout
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)

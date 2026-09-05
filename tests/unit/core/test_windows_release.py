from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[3]


def _archive_builder():
    return runpy.run_path(
        str(ROOT / "scripts" / "release" / "archive-product-runtime.py")
    )["create_windows_archive"]


def _write_fake_runtime(runtime: Path) -> None:
    python = Path(sys.executable).resolve()
    (runtime / "bin").mkdir(parents=True)
    (runtime / "assets" / "playwright").mkdir(parents=True)
    (runtime / "assets" / "gpa").mkdir(parents=True)
    (runtime / "assets" / "gpa" / "model.pt").write_bytes(b"test-model")
    (runtime / "bin" / "verify-product-runtime.py").write_text(
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    wrapper = runtime / "bin" / "python.cmd"
    wrapper.write_text(f'@"{python}" %*\n', encoding="utf-8")
    (runtime / "runtime-manifest.json").write_text(
        json.dumps({"python": "bin/python.cmd"}),
        encoding="utf-8",
    )


def test_windows_archive_is_deterministic_and_rooted(tmp_path: Path) -> None:
    runtime = tmp_path / "source" / "runtime"
    _write_fake_runtime(runtime)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    _archive_builder()(runtime, first)
    _archive_builder()(runtime, second)

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert all(info.create_system == 0 for info in archive.infolist())
    assert names[0] == "runtime/"
    assert "runtime/runtime-manifest.json" in names
    assert all(name == "runtime/" or name.startswith("runtime/") for name in names)


def test_windows_archive_handles_paths_longer_than_legacy_max_path(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "source" / "runtime"
    _write_fake_runtime(runtime)
    payload = runtime / ("p" * 220 + ".txt")
    assert len(str(payload)) > 260
    filesystem_payload = (
        rf"\\?\{payload.absolute()}" if os.name == "nt" else str(payload)
    )
    with open(filesystem_payload, "w", encoding="utf-8") as stream:
        stream.write("long paths stay portable")

    output = tmp_path / "long-path.zip"
    try:
        _archive_builder()(runtime, output)

        archived_name = "runtime/" + payload.relative_to(runtime).as_posix()
        with zipfile.ZipFile(output) as archive:
            assert archive.read(archived_name) == b"long paths stay portable"
    finally:
        os.unlink(filesystem_payload)


def test_managed_release_target_includes_windows_zip() -> None:
    from openprogram import _compat

    assert _compat.managed_release_target("Windows", "AMD64") == (
        "windows",
        "x86_64",
        ".zip",
        "install-release.ps1",
    )
    assert _compat.managed_release_target("Windows", "ARM64") == (
        "windows",
        "arm64",
        ".zip",
        "install-release.ps1",
    )


def test_windows_versioned_installer_uses_immutable_tag(monkeypatch) -> None:
    from openprogram.updater import github

    installer = b'$ErrorActionPreference = "Stop"\nSet-StrictMode -Version Latest\n'
    seen: list[str] = []
    monkeypatch.setattr(
        github,
        "_curl_release_bytes",
        lambda url: seen.append(url) or installer,
    )

    assert github.release_installer(
        "1.2.3", script_name="install-release.ps1"
    ) == installer
    assert seen == [
        "https://raw.githubusercontent.com/Fzkuji/OpenProgram/"
        "v1.2.3/scripts/install-release.ps1"
    ]


def test_managed_windows_upgrade_executes_tagged_powershell_installer(
    monkeypatch,
) -> None:
    from openprogram.cli.commands import upgrade

    monkeypatch.setattr(
        upgrade,
        "_managed_release_status",
        lambda: {
            "current_version": "1.2.2",
            "latest_version": "1.2.3",
            "update_available": True,
            "archive": "OpenProgram-1.2.3-runtime-windows-x86_64.zip",
        },
    )
    requested: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "openprogram.updater.github.release_installer",
        lambda version, *, script_name: (
            requested.append((version, script_name))
            or b'$ErrorActionPreference = "Stop"\n'
        ),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: (
            calls.append(command)
            or subprocess.CompletedProcess(command, 0, "", "")
        ),
    )

    assert upgrade.run_managed_release_upgrade(
        check_only=False,
        as_json=True,
    ) == 0
    assert requested == [("1.2.3", "install-release.ps1")]
    assert calls[0][-2] == "-File"
    assert calls[0][-1].endswith(".ps1")


def test_windows_release_contract_has_public_bootstrap_and_ci_gate() -> None:
    public = (ROOT / "docs" / "_static_root" / "install.ps1").read_text(
        encoding="utf-8"
    )
    bootstrap = (ROOT / "scripts" / "install-release.ps1").read_text(
        encoding="utf-8"
    )
    installer = (ROOT / "scripts" / "release" / "install-release.ps1").read_text(
        encoding="utf-8"
    )
    builder = (ROOT / "scripts" / "release" / "build-product-runtime.ps1").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "releases/latest" in public
    assert "v$Version/scripts/install-release.ps1" in public
    assert "v$Version/scripts/release/install-release.ps1" in bootstrap
    assert "Assert-SafeArchive" in installer
    assert "Move-Atomic" in installer
    assert all(
        marker not in installer
        for marker in ("icacls", "Set-Acl", "Add-MpPreference", "Set-MpPreference")
    )
    assert "runner: windows-2025" in workflow
    assert "runner: windows-11-vs2026-arm" in workflow
    assert "arch: arm64" in workflow
    assert "scripts/release/build-product-runtime.ps1" in workflow
    assert "scripts/release/archive-product-runtime.ps1" in workflow
    assert "scripts/install-release.ps1" in workflow
    assert "managed Python created an unexpected reparse point" in builder
    assert "[IO.Directory]::Delete($Alias.FullName, $false)" in builder
    assert r'Join-Path $RuntimeRoot "bin\node.exe"' in builder
    assert r'Join-Path $RuntimeRoot "assets\tui\index.cjs"' in builder


def test_windows_desktop_release_requires_signed_installer_and_native_checks() -> None:
    package = json.loads(
        (ROOT / "apps" / "desktop" / "package.json").read_text(encoding="utf-8")
    )
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    updater = (ROOT / "apps" / "desktop" / "update-service.js").read_text(
        encoding="utf-8"
    )
    signature = (ROOT / "apps" / "desktop" / "windows-signature.js").read_text(
        encoding="utf-8"
    )
    terminal = (ROOT / "apps" / "desktop" / "terminal-command.js").read_text(
        encoding="utf-8"
    )
    prepare = (
        ROOT / "scripts" / "release" / "prepare-desktop-runtime.ps1"
    ).read_text(encoding="utf-8")

    assert package["build"]["win"]["target"][0] == {
        "target": "nsis",
        "arch": ["x64", "arm64"],
    }
    assert package["build"]["win"]["requestedExecutionLevel"] == "asInvoker"
    assert package["build"]["win"]["signtoolOptions"] == {
        "signingHashAlgorithms": ["sha256"],
    }
    assert package["build"]["nsis"]["perMachine"] is False
    assert "WINDOWS_CSC_LINK" in workflow
    assert "Get-AuthenticodeSignature" in workflow
    assert "smoke-packaged-runtime.ps1" in workflow
    assert "windows-desktop:" in ci
    assert "runner: windows-2025" in ci
    assert "runner: windows-11-vs2026-arm" in ci
    assert "OpenProgram-${version}-win-${arch}.exe" in updater
    assert 'arch === "x64" || arch === "arm64"' in updater
    assert "verifyArtifact" in updater
    assert "Get-AuthenticodeSignature" in signature
    assert "useConpty: true" in terminal
    assert "install-release.ps1" in prepare
    assert all(
        marker not in prepare
        for marker in ("icacls", "Set-Acl", "chmod", "Add-MpPreference")
    )


def test_windows_environment_advisories_are_read_only(monkeypatch, tmp_path: Path) -> None:
    from openprogram import _compat

    outputs = iter([
        "disabled\n",
        json.dumps({"realTime": True, "exclusions": []}),
    ])
    scripts: list[str] = []

    def fake_powershell(script: str, **_kwargs) -> str:
        scripts.append(script)
        return next(outputs)

    monkeypatch.setattr(_compat._sys, "platform", "win32")
    monkeypatch.setattr(_compat, "_windows_powershell", fake_powershell)

    rows = _compat.platform_environment_advisories(tmp_path)

    assert [row[1] for row in rows] == [
        "windows long paths",
        "windows defender",
    ]
    assert all(row[0] for row in rows)
    assert "disabled" in rows[0][2]
    assert "consider excluding only" in rows[1][2]
    assert not any(
        marker in script
        for script in scripts
        for marker in ("Set-ItemProperty", "Add-MpPreference", "Set-MpPreference")
    )


def test_windows_wsl_sandbox_probe_requires_working_bubblewrap(monkeypatch) -> None:
    from openprogram import _compat

    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        _compat,
        "windows_wsl_exec_prefix",
        lambda: ["wsl.exe", "--distribution", "Ubuntu", "--exec"],
    )
    monkeypatch.setattr(_compat._subprocess, "run", fake_run)
    _compat.windows_wsl_sandbox_reason.cache_clear()

    assert _compat.windows_wsl_sandbox_reason() is None
    assert calls[0][:6] == [
        "wsl.exe", "--distribution", "Ubuntu", "--exec", "sh", "-c",
    ]
    assert "bwrap --new-session" in calls[0][-1]
    _compat.windows_wsl_sandbox_reason.cache_clear()


def test_windows_wsl_path_translation_uses_selected_distribution(monkeypatch) -> None:
    from openprogram import _compat

    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "/mnt/c/work\n", "")

    monkeypatch.setattr(
        _compat,
        "windows_wsl_exec_prefix",
        lambda: ["wsl.exe", "--distribution", "Ubuntu", "--exec"],
    )
    monkeypatch.setattr(_compat._subprocess, "run", fake_run)
    _compat.windows_path_to_wsl.cache_clear()

    assert _compat.windows_path_to_wsl(r"C:\work") == "/mnt/c/work"
    assert calls[0][-4:-1] == ["wslpath", "-a", "-u"]
    _compat.windows_path_to_wsl.cache_clear()


@pytest.mark.skipif(os.name != "nt", reason="PowerShell release installer is Windows-only")
def test_windows_release_installer_rejects_zip_slip(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    assert powershell is not None
    archive = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive, "w") as payload:
        payload.writestr("runtime/runtime-manifest.json", '{"python":"bin/python.cmd"}')
        payload.writestr("runtime/../../escaped.txt", "must not extract")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    state = tmp_path / "state"
    bin_dir = tmp_path / "bin"

    result = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "install-release.ps1"),
        ],
        env={
            **os.environ,
            "OPENPROGRAM_VERSION": "9.9.7",
            "OPENPROGRAM_RUNTIME_ARCHIVE": str(archive),
            "OPENPROGRAM_RUNTIME_SHA256": checksum,
            "OPENPROGRAM_STATE_DIR": str(state),
            "OPENPROGRAM_BIN_DIR": str(bin_dir),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert not (tmp_path / "escaped.txt").exists()
    assert not (bin_dir / "openprogram.ps1").exists()


@pytest.mark.skipif(os.name != "nt", reason="PowerShell release installer is Windows-only")
def test_windows_release_install_upgrade_and_failed_upgrade_keep_active_launcher(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    assert powershell is not None
    runtime = tmp_path / "archive-source" / "runtime"
    _write_fake_runtime(runtime)
    archive = tmp_path / "runtime.zip"
    _archive_builder()(runtime, archive)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    state = tmp_path / "state"
    bin_dir = tmp_path / "bin"
    script = ROOT / "scripts" / "install-release.ps1"

    base_env = {
        **os.environ,
        "OPENPROGRAM_RUNTIME_ARCHIVE": str(archive),
        "OPENPROGRAM_RUNTIME_SHA256": checksum,
        "OPENPROGRAM_STATE_DIR": str(state),
        "OPENPROGRAM_BIN_DIR": str(bin_dir),
        "OPENPROGRAM_REPOSITORY": "Fzkuji/OpenProgram",
    }
    first_version = importlib.metadata.version("openprogram")
    first_env = {**base_env, "OPENPROGRAM_VERSION": first_version}
    first = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        env=first_env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert first.returncode == 0, first.stderr or first.stdout
    launcher = bin_dir / "openprogram.ps1"
    first_launcher = launcher.read_text(encoding="utf-8")
    assert str(state / "runtime" / "cli" / "releases" / first_version) in first_launcher
    assert (bin_dir / "openprogram.cmd").is_file()

    second_version = "9.9.8"
    second = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        env={**base_env, "OPENPROGRAM_VERSION": second_version},
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert second.returncode == 0, second.stderr or second.stdout
    second_launcher = launcher.read_text(encoding="utf-8")
    assert str(state / "runtime" / "cli" / "releases" / second_version) in second_launcher
    assert (bin_dir / "openprogram.previous.ps1").read_text(
        encoding="utf-8"
    ) == first_launcher

    failed = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        env={
            **base_env,
            "OPENPROGRAM_VERSION": "9.9.9",
            "OPENPROGRAM_RUNTIME_SHA256": "0" * 64,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert failed.returncode != 0
    assert launcher.read_text(encoding="utf-8") == second_launcher

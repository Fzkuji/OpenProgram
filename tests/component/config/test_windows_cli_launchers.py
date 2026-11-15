from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from openprogram.operations.project_site import SITE_HOST


ROOT = Path(__file__).resolve().parents[3]
NATIVE_WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="native Windows launchers")
HANDOFF = "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File"


def _ps(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("relative", "target"),
    [
        ("docs/_static_root/install.ps1", "$Temporary"),
        ("scripts/install-release.ps1", "$Installer @args"),
        ("scripts/install.ps1", "$child @forward"),
    ],
)
def test_powershell_handoffs_bypass_the_restricted_policy(relative: str, target: str) -> None:
    source = _read(relative)
    assert f"& {HANDOFF} {target}" in source
    after = source[source.index(HANDOFF) :]
    assert "$LASTEXITCODE" in after.split("\n", 2)[1], "the exit code is read right after the handoff"


def test_command_prompt_bootstrap_hands_off_with_bypass_and_returns_the_exit_code() -> None:
    source = _read("docs/_static_root/install.cmd")
    assert f'{HANDOFF} "%OPENPROGRAM_BOOTSTRAP%"' in source
    assert 'set "OPENPROGRAM_INSTALL_EXIT=%ERRORLEVEL%"' in source
    assert source.rstrip().endswith("exit /b %OPENPROGRAM_INSTALL_EXIT%")
    assert "HTTPS_PROXY" in source
    # Served with LF endings, where CMD cannot find labels reliably.
    assert not [line for line in source.splitlines() if line.startswith(":") or "goto" in line.lower()]


@pytest.mark.parametrize(
    "relative", ["scripts/release/install-release.ps1", "scripts/install.ps1"]
)
def test_installers_keep_only_the_cmd_launcher_and_delete_an_old_powershell_one(relative: str) -> None:
    source = _read(relative)
    assert "Remove-Item" in source and ".ps1" in source
    assert "$LauncherPs1" not in source
    assert "$psLauncher" not in source
    assert "$psContent" not in source
    assert "@args\nexit" not in source.replace("\r\n", "\n")
    assert "open a new terminal".lower() in source.lower()


@pytest.mark.parametrize(
    "relative",
    [
        "docs/_static_root/install.ps1",
        "docs/_static_root/install.cmd",
        "scripts/install-release.ps1",
        "scripts/release/install-release.ps1",
        "scripts/install.ps1",
    ],
)
def test_windows_installer_scripts_are_ascii_so_any_code_page_reads_them(relative: str) -> None:
    (ROOT / relative).read_bytes().decode("ascii")


def test_source_installer_gets_its_tools_from_winget_and_reloads_path() -> None:
    source = _read("scripts/install.ps1")
    clone = source.index("git clone --depth 1")
    assert source.index('Winget-Install "Git.Git"') < clone
    assert 'Winget-Install "Python.Python.3.12"' in source
    update = source[source.index("function Update-SessionPath") : source.index("# When run via")]
    assert '[Environment]::GetEnvironmentVariable("Path", "Machine")' in update
    assert '[Environment]::GetEnvironmentVariable("Path", "User")' in update
    assert "Update-SessionPath\n" in update[update.index("function Winget-Install") :].replace("\r\n", "\n")
    assert "open a new PowerShell and run this installer again" not in source


def test_windows_downloads_resume_check_disk_and_name_the_proxy() -> None:
    source = _read("scripts/release/install-release.ps1")
    assert "--continue-at -" in source
    assert "--retry 3" in source
    assert "--progress-bar" in source
    assert "($ArchiveBytes * ($script:ArchiveCopiesOnDisk + 4)) + 536870912" in source
    assert "four times" in source
    assert "HTTPS_PROXY" in source
    assert source.index("Assert-FreeDiskSpace $Size $Directory") < source.index("--continue-at -")


@pytest.mark.parametrize(
    "relative", ["docs/_static_root/install.ps1", "scripts/release/install-release.ps1"]
)
def test_windows_error_links_use_the_github_docs(relative: str) -> None:
    source = _read(relative)
    assert f"{SITE_HOST}/" not in source
    assert "github.com/FuzzyKuji/OpenProgram/blob/main/docs/install/install.html" in source


def test_windows_installer_python_has_no_double_quotes_for_powershell_5() -> None:
    # 5.1 passes them unescaped, which splits the code across several arguments.
    source = _read("scripts/release/install-release.ps1").replace("\r\n", "\n")
    blocks = re.findall(r"-c @'\n(.*?)\n'@", source, re.S)
    assert blocks, "the health probe passes Python inline"
    for block in blocks:
        assert '"' not in block


def test_windows_bootstrap_leaves_nothing_behind_in_the_persons_window() -> None:
    source = _read("docs/_static_root/install.ps1").replace("\r\n", "\n")
    code = [line for line in source.splitlines() if line and not line.startswith("#")]
    assert code[0] == "& {", "the settings below stay inside this block"
    assert source.index("& {") < source.index("$ErrorActionPreference") < source.index("Set-StrictMode")
    cleanup = source[source.index("} finally {") :]
    assert "$env:OPENPROGRAM_VERSION = $CallerVersion" in cleanup
    assert "$env:OPENPROGRAM_REPOSITORY = $CallerRepository" in cleanup


def test_windows_seed_base_moves_into_the_store_only_when_complete() -> None:
    source = _read("scripts/release/install-release.ps1")
    install = source[source.index("function Install-SeedBase") : source.index("function Install-FromSeed")]
    assert install.index("Expand-TarArchive $Archive $BaseWork") < install.index("[IO.Directory]::Move($Unpacked, $Destination)")
    assert "-C $Store" not in source[source.index("function Install-FromSeed") :]


def test_windows_bootstrap_names_a_missing_platform_from_the_manifest() -> None:
    source = _read("docs/_static_root/install.ps1")
    manifest = source.index("release-manifest.json")
    assert manifest < source.index("scripts/install-release.ps1")
    assert "has no command-line package for Windows on $Arch" in source
    assert "$ReleasesPage/tag/v$Version" in source


def test_windows_seed_update_reports_only_what_the_store_did() -> None:
    source = _read("scripts/release/install-release.ps1")
    update = source[source.index("function Update-ExistingStore") : source.index("function Install-FromSeed")]
    assert 'upgrade\n' in update.replace("\r\n", "\n")
    assert "installed." not in update.replace("was not installed", "")
    health = source.index("function Remove-PlainRuntimeAfterHealthCheck")
    probe = source.index("Invoke-WorkerProbe $StorePython", health)
    removal = source.index("Remove-Item -LiteralPath $PlainRuntime", probe)
    assert health < probe < removal


def test_release_wrappers_have_no_stale_default_version() -> None:
    for relative in ("scripts/install-release.sh", "scripts/install-release.ps1"):
        source = _read(relative)
        assert "0.8.1" not in source
        assert "No OpenProgram version was given" in source


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
        "'playwright': os.environ.get('PLAYWRIGHT_BROWSERS_PATH')}))\n"
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
        start = source.index("$LauncherCmd =")
        body = source[start : source.index("if (-not $env:OPENPROGRAM_BIN_DIR)", start)]
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
    assert not (bin_dir / "openprogram.ps1").exists()
    return bin_dir / "openprogram.cmd"


@NATIVE_WINDOWS
@pytest.mark.parametrize("flavor", ["release_cmd", "source_cmd"])
@pytest.mark.parametrize("codepage", [437, 936, 65001])
def test_native_launchers_preserve_unicode_paths_exit_and_console(
    tmp_path: Path, launcher_runtime: tuple[Path, Path], flavor: str, codepage: int,
) -> None:
    runtime, python = launcher_runtime
    launcher = _generate_launchers(tmp_path, runtime, python, flavor)
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
    assert str(codepage).encode("ascii") in result.stdout.splitlines()[-1]


@NATIVE_WINDOWS
@pytest.mark.parametrize("flavor", ["release_cmd", "source_cmd"])
def test_powershell_invocation_preserves_unicode_arguments(
    tmp_path: Path, launcher_runtime: tuple[Path, Path], flavor: str,
) -> None:
    runtime, python = launcher_runtime
    launcher = _generate_launchers(tmp_path, runtime, python, flavor)
    unicode_bin = tmp_path / "启动目录 with spaces"
    launcher.parent.rename(unicode_bin)
    launcher = unicode_bin / launcher.name
    arguments = ["你好 with spaces", "trailing\\", "amp&bang!"]
    # Explicit .cmd invocation needs CMD quoting at the PowerShell boundary.
    invocation_args = [arguments[0], arguments[1], '"' + arguments[2] + '"']
    driver = tmp_path / "invoke.ps1"
    command = _ps(launcher)
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


def test_windows_on_arm_falls_back_to_the_x64_package_and_says_so() -> None:
    installer = _read("scripts/release/install-release.ps1").replace("\r\n", "\n")
    select = installer[installer.index("function Select-PackageArchitecture"):installer.index("$SeedName =")]
    # Only Windows 11 on Arm emulates x64, and its own package wins when the release has one.
    assert 'if ($Native -ne "arm64" -or $WindowsBuild -lt 22000) {\n        return $Native' in select
    assert 'foreach ($Candidate in @("arm64", "x86_64"))' in select
    assert '"seed-windows-$Candidate.tar.gz", "runtime-windows-$Candidate.zip"' in select
    assert "has no Windows on Arm package. Installing the x64 one" in select
    assert select.index("Write-Host") < select.index("$Arch = $Selected")
    # A package handed over on disk is installed as it is.
    assert "if (-not $env:OPENPROGRAM_SEED_ARCHIVE -and -not $env:OPENPROGRAM_RUNTIME_ARCHIVE) {\n    $Selected =" in select
    assert installer.index("$Arch = $Selected") < installer.index('$SeedName = "OpenProgram-$Version-seed-windows-$Arch.tar.gz"')
    assert installer.index("$Arch = $Selected") < installer.index('$ArchiveName = "OpenProgram-$Version-runtime-windows-$Arch.zip"')

    bootstrap = _read("docs/_static_root/install.ps1")
    refusal = bootstrap[bootstrap.index("$Listed ="):bootstrap.index("has no command-line package")]
    assert '$Emulated = $Arch -eq "arm64" -and [Environment]::OSVersion.Version.Build -ge 22000 -and (' in refusal
    assert '$Manifest.Contains("OpenProgram-$Version-seed-windows-x86_64.tar.gz"))' in refusal
    assert "if (-not $Listed -and -not $Emulated) {" in refusal
    # The installer is the release's own. One published before it could pick
    # the x64 package would stop on the missing Arm one, so that release is
    # refused here, before anything is downloaded for it.
    bootstrap = bootstrap.replace("\r\n", "\n")
    tagged = bootstrap[bootstrap.index("if (-not $Listed -and $Emulated) {"):bootstrap.index("if (-not $Listed -and -not $Emulated) {")]
    assert '"https://raw.githubusercontent.com/$Repository/v$Version/scripts/release/install-release.ps1"' in tagged
    assert '$Emulated = $LASTEXITCODE -eq 0 -and $Tagged.Contains("function Select-PackageArchitecture")' in tagged
    assert "\nfunction Select-PackageArchitecture {" in installer
    # The wrapper the bootstrap starts hands over to that same tagged file.
    assert '"https://raw.githubusercontent.com/$Repository/v$Version/scripts/release/install-release.ps1"' in _read("scripts/install-release.ps1")


@NATIVE_WINDOWS
@pytest.mark.parametrize(("native", "build", "published", "expected"), [
    ("arm64", 26100, ["seed-windows-x86_64.tar.gz"], "x86_64"),
    ("arm64", 26100, ["runtime-windows-x86_64.zip"], "x86_64"),
    ("arm64", 26100, ["runtime-windows-x86_64.zip", "seed-windows-arm64.tar.gz"], "arm64"),
    ("arm64", 26100, [], "arm64"),
    ("arm64", 19045, ["seed-windows-x86_64.tar.gz"], "arm64"),  # Windows 10 on Arm has no x64 emulation.
    ("x86_64", 26100, ["seed-windows-arm64.tar.gz"], "x86_64"),
])
def test_package_architecture_selection(tmp_path: Path, native: str, build: int, published: list[str], expected: str) -> None:
    source = _read("scripts/release/install-release.ps1")
    start = source.index("function Select-PackageArchitecture")
    function = source[start : source.index("if (-not $env:OPENPROGRAM_SEED_ARCHIVE", start)]
    names = ",".join(_ps(f"OpenProgram-1.2.3-{name}") for name in published) or "''"
    driver = tmp_path / "select.ps1"
    driver.write_text(
        "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n" + function +
        f"\n$Published = @({names})\n"
        f"Select-PackageArchitecture {_ps(native)} {build} '1.2.3' {{ param($Name) $Published -contains $Name }}\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(driver)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout.strip() == expected


# Every batch launcher reads its paths only after this, in the same words.
CMD_PREAMBLE = [
    "@echo off",
    "setlocal DisableDelayedExpansion",
    'set "_OPENPROGRAM_CODEPAGE=%_OPENPROGRAM_CALLER_CODEPAGE%"',
    'set "_OPENPROGRAM_CALLER_CODEPAGE="',
    'if not defined _OPENPROGRAM_CODEPAGE for /f "tokens=2 delims=:." %%P in (\'chcp\') do set "_OPENPROGRAM_CODEPAGE=%%P"',
    "chcp 65001 >nul",
]


def _code_lines(text: str) -> list[str]:
    return [line.strip() for line in text.replace("\r\n", "\n").splitlines()
            if line.strip() and line.strip().lower().split(" ", 1)[0] != "rem"]


def test_every_batch_launcher_switches_the_code_page_before_it_reads_a_path() -> None:
    installer = _read("scripts/release/install-release.ps1").replace("\r\n", "\n")
    writer = installer[installer.index("function Write-CmdLauncher") : installer.index("function Get-Sha256")]
    written = re.findall(r"^        (?:'(.*)'|\"(.*)\"),?$", writer[: writer.index("if ($HandOffTo)")], re.M)
    assert [single.replace("''", "'") or double for single, double in written] == CMD_PREAMBLE
    # The installer writes a .cmd only through it: the launcher on PATH and the
    # runtime's launcher both, and no other line switches a code page.
    assert installer.count("Write-CmdLauncher ") == 1 + 2  # the definition and its two callers
    assert 'Write-CmdLauncher (Join-Path $Bin "openprogram.cmd") -HandOffTo' in installer
    assert "Write-CmdLauncher $CmdTemporary -Run" in installer
    assert installer.count("chcp") == writer.count("chcp")
    assert installer.count("Write-Utf8NoBom ") == 1 + writer.count("Write-Utf8NoBom ")
    # The launcher it hands over to is the store's, which restores the caller's code page.
    assert "call " not in writer
    store = _code_lines(_read("scripts/release/bootstrap/bin-openprogram.cmd"))
    assert store[: len(CMD_PREAMBLE)] == CMD_PREAMBLE
    assert store[-2:] == ["chcp %_OPENPROGRAM_CODEPAGE% >nul", "exit /b %_OPENPROGRAM_EXIT%"]
    source = _code_lines(_read("scripts/install.ps1"))
    start = source.index("@echo off")
    assert source[start : start + len(CMD_PREAMBLE)] == CMD_PREAMBLE


@pytest.fixture
def seed_store(tmp_path: Path) -> tuple[Path, Path]:
    """A version store under a folder no legacy code page can spell, as a seed install leaves it."""
    store = tmp_path / "José 李 50% ^&!" / ".openprogram" / "runtime"
    venv = store / "versions" / "9.9.9" / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                   check=True, capture_output=True, timeout=60)
    package = venv / "Lib" / "site-packages" / "openprogram"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json, os, sys\n"
        "print(json.dumps({'args': sys.argv[1:], 'python': sys.executable,"
        " 'handed': os.environ.get('_OPENPROGRAM_CALLER_CODEPAGE')}))\n"
        "raise SystemExit(17)\n",
        encoding="utf-8",
    )
    (store / "current").write_text("version=9.9.9\nbase=none\n", encoding="ascii")
    (store / "bin").mkdir()
    sys.path.insert(0, str(ROOT / "scripts" / "release" / "bootstrap"))
    try:
        from install_seed import write_batch_file
    finally:
        sys.path.pop(0)
    write_batch_file(ROOT / "scripts/release/bootstrap/bin-openprogram.cmd", store / "bin" / "openprogram.cmd")
    return store, venv / "Scripts" / "python.exe"


def _publish_store_launcher(tmp_path: Path, store: Path, bin_dir: Path) -> Path:
    source = _read("scripts/release/install-release.ps1")
    functions = source[source.index("function Write-Utf8NoBom") : source.index("$Version =")]
    start = source.index("function Publish-StoreLauncher")
    publish = source[start : source.index("# An existing store updates", start)]
    bin_dir.mkdir(parents=True, exist_ok=True)
    driver = tmp_path / "publish.ps1"
    driver.write_text(
        "$ErrorActionPreference='Stop'\n$env:OPENPROGRAM_BIN_DIR='set'\n" + functions + "\n" + publish
        + f"\nPublish-StoreLauncher {_ps(store)} {_ps(bin_dir)}\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(driver)],
        capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    return bin_dir / "openprogram.cmd"


@NATIVE_WINDOWS
@pytest.mark.parametrize("codepage", [437, 936, 65001])
def test_the_launcher_on_path_reaches_a_store_under_a_unicode_profile(
    tmp_path: Path, seed_store: tuple[Path, Path], codepage: int,
) -> None:
    store, python = seed_store
    launcher = _publish_store_launcher(tmp_path, store, tmp_path / "bin")
    wrapper = tmp_path / "invoke.cmd"
    # The wrapper stays ASCII, as a person's own script in any code page; the
    # non-ASCII argument reaches it through the environment.
    wrapper.write_bytes(
        (f'@echo off\r\nchcp {codepage}>nul\r\ncall "{launcher}" "argument with spaces" %OP_ARGUMENT%\r\n'
         'set "LAUNCH_EXIT=%ERRORLEVEL%"\r\nchcp\r\nset _OPENPROGRAM\r\nexit /b %LAUNCH_EXIT%\r\n').encode("ascii")
    )
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(wrapper)], cwd=tmp_path,
        env={**os.environ, "OP_ARGUMENT": "李é"},
        capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 17, (result.stdout, result.stderr)
    lines = result.stdout.splitlines()
    data = json.loads(next(line for line in lines if line.startswith(b"{")))
    assert data == {"args": ["argument with spaces", "李é"], "python": str(python), "handed": None}
    # The caller gets its own code page back and none of the launcher's variables.
    assert str(codepage).encode("ascii") in lines[-1]
    assert b"not defined" in result.stderr


@NATIVE_WINDOWS
def test_the_launcher_on_path_passes_every_argument_unchanged_from_powershell(
    tmp_path: Path, seed_store: tuple[Path, Path],
) -> None:
    store, python = seed_store
    launcher = _publish_store_launcher(tmp_path, store, tmp_path / "启动 bin")
    arguments = ["caret^inside quotes", "你好 é", "trailing\\", "amp&bang!"]
    invocation_args = [*arguments[:3], '"' + arguments[3] + '"']
    driver = tmp_path / "invoke.ps1"
    driver.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"& {_ps(launcher)} " + " ".join(map(_ps, invocation_args)) + "\nexit $LASTEXITCODE\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(driver)],
        cwd=tmp_path, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 17, (result.stdout, result.stderr)
    data = json.loads(result.stdout)
    assert data == {"args": arguments, "python": str(python), "handed": None}

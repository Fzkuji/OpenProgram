"""The base build installs its packages with the pinned uv's real command line.

These run the package step of build-base.sh (and of build-base.ps1 on
Windows) against the uv that product-runtime.json pins, into a throwaway
virtual environment, offline, from two wheels made here. A string match on
the scripts let `uv pip sync --requirements FILE` through, which that uv
refuses, so every build that reused an old base failed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[4]
RELEASE = ROOT / "scripts" / "release"
UV_PIN = json.loads((RELEASE / "product-runtime.json").read_text(encoding="utf-8"))["uv"]


def _pinned_uv() -> str | None:
    found = os.environ.get("OPENPROGRAM_UV_BIN") or shutil.which("uv")
    if not found:
        return None
    version = subprocess.run([found, "--version"], capture_output=True, text=True).stdout.split()
    return found if len(version) > 1 and version[1] == UV_PIN else None


UV = _pinned_uv()
# CI installs exactly this uv (ci.yml), so there a missing one is a failure.
needs_pinned_uv = pytest.mark.skipif(UV is None and not os.environ.get("CI"), reason=f"uv {UV_PIN} is not on PATH")


def test_ci_runs_these_tests_with_the_pinned_uv() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert workflow.count(f'version: "{UV_PIN}"') >= 2
    assert "test_distribution_release/test_build_base_packages.py" in workflow


def _wheel(directory: Path, name: str) -> Path:
    """A minimal pure-Python wheel, so nothing is downloaded."""
    distribution = f"{name}-1.0"
    files = {
        f"{name}/__init__.py": b"",
        f"{distribution}.dist-info/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n".encode(),
        f"{distribution}.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = "".join(
        f"{path},sha256={base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()},{len(data)}\n"
        for path, data in files.items()
    ) + f"{distribution}.dist-info/RECORD,,\n"
    path = directory / f"{distribution}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for member, data in files.items():
            archive.writestr(member, data)
        archive.writestr(f"{distribution}.dist-info/RECORD", record)
    return path


@pytest.fixture
def project(tmp_path: Path) -> dict:
    """A checkout whose lock pins one local wheel, with a venv holding a stray one."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    dependency = _wheel(wheels, "fixture_dep")
    stray = _wheel(wheels, "stray_pkg")
    # A simple index on disk whose links carry the hash, so the lock (and the
    # requirements exported from it) have one, as PyPI's do.
    index = tmp_path / "simple"
    page = index / "fixture-dep" / "index.html"
    page.parent.mkdir(parents=True)
    digest = hashlib.sha256(dependency.read_bytes()).hexdigest()
    page.write_text(f'<a href="{dependency.as_uri()}#sha256={digest}">{dependency.name}</a>\n', encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / "scripts" / "release").mkdir(parents=True)
    for relative in ("scripts/release/runtime-wheels.py", "scripts/release/product-runtime.json",
                     "scripts/release/trim-runtime.py",
                     "openprogram/__init__.py", "openprogram/host/__init__.py",
                     "openprogram/host/_platform/__init__.py", "openprogram/host/_platform/architecture.py"):
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, repo / relative)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0"\nrequires-python = ">=3.10"\n'
        'dependencies = ["fixture_dep"]\n'
        '[project.optional-dependencies]\nall = []\nsearch = []\n'
        '[[tool.uv.index]]\nname = "local"\nurl = "' + index.as_uri() + '"\ndefault = true\n',
        encoding="utf-8",
    )
    environment = {
        **os.environ, "UV_OFFLINE": "1", "UV_NO_CONFIG": "1", "UV_DEFAULT_INDEX": index.as_uri(),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_PYTHON_DOWNLOADS": "never", "OPENPROGRAM_UV_BIN": UV or "",
    }
    subprocess.run([UV, "lock", "--project", str(repo)], check=True, env=environment, capture_output=True)
    venv = tmp_path / "venv"
    subprocess.run([UV, "venv", "--python", sys.executable, str(venv)], check=True, env=environment,
                   capture_output=True)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([UV, "pip", "install", "--python", str(python), str(stray)], check=True,
                   env=environment, capture_output=True)
    return {"repo": repo, "python": python, "environment": environment, "requirements": tmp_path / "req.txt"}


def _installed(python: Path) -> set[str]:
    listed = subprocess.run([str(python), "-I", "-c",
                             "import importlib.metadata as m;print('\\n'.join(d.metadata['Name'] for d in m.distributions()))"],
                            check=True, capture_output=True, text=True).stdout
    return {line.strip().replace("-", "_").lower() for line in listed.splitlines() if line.strip()}


def _shell_package_step() -> str:
    """read_pin and install_packages exactly as build-base.sh defines them."""
    script = (RELEASE / "build-base.sh").read_text(encoding="utf-8")
    read_pin = next(line for line in script.splitlines() if line.startswith("read_pin()"))
    start = script.index("install_packages() {")
    return read_pin + "\n" + script[start : script.index("\n}\n", start) + 3]


@needs_pinned_uv
@pytest.mark.skipif(sys.platform == "win32", reason="build-base.sh is the macOS and Linux build")
@pytest.mark.parametrize("verb", ["sync", "install"])
def test_the_shell_build_installs_the_lock_with_the_pinned_uv(project: dict, verb: str) -> None:
    driver = (
        "set -euo pipefail\n"
        'repo_root="$1"; uv_bin="$OPENPROGRAM_UV_BIN"\n'
        'product_config="$repo_root/scripts/release/product-runtime.json"\n'
        + _shell_package_step()
        + 'install_packages "$2" "$3" "$4"\n'
    )
    result = subprocess.run(
        ["bash", "-c", driver, "build-base", str(project["repo"]), verb, str(project["python"]),
         str(project["requirements"])],
        env=project["environment"], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    installed = _installed(project["python"])
    assert "fixture_dep" in installed
    # sync makes the environment the lock; install only adds to it.
    assert ("stray_pkg" in installed) == (verb == "install")


@needs_pinned_uv
@pytest.mark.skipif(sys.platform != "win32", reason="build-base.ps1 is the Windows build")
@pytest.mark.parametrize("verb", ["sync", "install"])
def test_the_windows_build_installs_the_lock_with_the_pinned_uv(project: dict, verb: str, tmp_path: Path) -> None:
    script = (RELEASE / "build-base.ps1").read_text(encoding="utf-8")
    start = script.index("function Install-Packages")
    function = script[start : script.index("function Install-Browsers", start)]
    head = script[script.index("function Invoke-Native") : script.index("$RepoRoot =")]

    def quote(value: Path | str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    driver = tmp_path / "install.ps1"
    driver.write_text(
        "$ErrorActionPreference = 'Stop'\nSet-StrictMode -Version Latest\n" + head
        + f"$RepoRoot = {quote(project['repo'])}\n$Uv = $env:OPENPROGRAM_UV_BIN\n" + function
        + f"Install-Packages {verb} {quote(project['python'])} {quote(project['requirements'])}\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(driver)],
        env=project["environment"], capture_output=True, text=True,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    installed = _installed(project["python"])
    assert "fixture_dep" in installed
    assert ("stray_pkg" in installed) == (verb == "install")


def test_the_bundled_git_follows_the_packaged_python() -> None:
    # An x64 base built on Windows on Arm (or under Rosetta) ships to x64 machines.
    shell = (RELEASE / "build-base.sh").read_text(encoding="utf-8")
    install_git = shell[shell.index("install_git() {") : shell.index("\n}\n", shell.index("install_git() {"))]
    assert 'runtime-wheels.py" target' in install_git and "uname" not in install_git
    assert 'install_git "$dest/git" "$base_python"' in shell
    assert 'install_git "$embedded_runtime/git" "$python_bin"' in shell
    windows = (RELEASE / "build-base.ps1").read_text(encoding="utf-8")
    install = windows[windows.index("function Install-Git") : windows.index("if ($EmbeddedRuntime)")]
    assert 'runtime-wheels.py") target' in install and "OSArchitecture" not in install
    assert 'Install-Git (Join-Path $Dest "git") $BasePython' in windows
    assert 'Install-Git (Join-Path $EmbeddedRuntime "git") $PythonBin' in windows

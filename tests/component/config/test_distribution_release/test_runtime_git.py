"""The complete runtime carries its own Git and selects it first."""

from __future__ import annotations

import os
from pathlib import Path
import plistlib
import runpy
import stat
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
VERIFIER = ROOT / "scripts/release/verify-product-runtime.py"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fixture Git is a POSIX script")


def write_git(path: Path, *, version: str = "git version 2.56.0") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f'  --version) echo "{version}" ;;\n'
        '  --exec-path) echo "$GIT_EXEC_PATH" ;;\n'
        '  init) /bin/mkdir -p "$3/.git" && echo ref > "$3/.git/HEAD" ;;\n'
        "esac\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def probe():
    return runpy.run_path(str(VERIFIER))["_probe_git"]


@pytest.fixture
def runtime(tmp_path):
    root = (tmp_path / "runtime").resolve()
    write_git(root / "git/bin/git")
    return root


def test_bundled_git_passes_ahead_of_the_system_git(probe, runtime, tmp_path, monkeypatch):
    write_git(tmp_path / "system/git")
    monkeypatch.setenv("PATH", str(tmp_path / "system"))
    monkeypatch.delenv("GIT_EXEC_PATH", raising=False)
    probe(runtime)


def test_runtime_without_git_fails(probe, tmp_path):
    root = (tmp_path / "runtime").resolve()
    root.mkdir()
    with pytest.raises(RuntimeError, match="bundled Git is missing"):
        probe(root)


def test_git_that_cannot_run_fails_as_not_first(probe, runtime, tmp_path, monkeypatch):
    (runtime / "git/bin/git").chmod(0o644)
    write_git(tmp_path / "system/git")
    monkeypatch.setenv("PATH", str(tmp_path / "system"))
    with pytest.raises(RuntimeError, match="not first on PATH"):
        probe(runtime)


def test_launcher_that_puts_another_git_first_fails(probe, runtime, tmp_path, monkeypatch):
    write_git(tmp_path / "system/git")
    monkeypatch.setenv("PATH", os.pathsep.join([str(tmp_path / "system"), str(runtime / "git/bin")]))
    with pytest.raises(RuntimeError, match="not first on PATH"):
        probe(runtime)


def test_git_using_helpers_outside_the_runtime_fails(probe, runtime, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_EXEC_PATH", str(tmp_path / "elsewhere"))
    with pytest.raises(RuntimeError, match="outside the runtime"):
        probe(runtime)


def test_compiled_import_probe_runs_through_this_python():
    verifier = runpy.run_path(str(VERIFIER))
    pytest.importorskip("_cffi_backend")
    verifier["_probe_compiled_import"]()
    assert sys.modules["_cffi_backend"].__file__


@pytest.mark.skipif(sys.platform != "darwin", reason="library validation is a macOS check")
def test_python_that_refuses_a_locally_built_module_fails(monkeypatch):
    verifier = runpy.run_path(str(VERIFIER))
    pytest.importorskip("_cffi_backend")
    real_run = subprocess.run

    def refuse(command, **options):
        if command[0] == sys.executable:
            return subprocess.CompletedProcess(command, 1, "", "code signature not valid for use in process")
        return real_run(command, **options)

    monkeypatch.setattr(subprocess, "run", refuse)
    with pytest.raises(RuntimeError, match="refuses a module built on this machine"):
        verifier["_probe_compiled_import"]()


def test_windows_base_asks_the_python_it_packages_for_its_architecture():
    # An x64 base built on Windows on Arm ships to x64 PCs; neither the
    # machine nor the environment the build inherits decides its Git.
    recipe = (ROOT / "scripts/release/build-base.ps1").read_text(encoding="utf-8")
    assert 'runtime-wheels.py") target' in recipe
    assert "OSArchitecture" not in recipe and "PROCESSOR_ARCHITECTURE" not in recipe


def test_both_runtime_recipes_install_and_require_git():
    shell = (ROOT / "scripts/release/build-base.sh").read_text(encoding="utf-8")
    powershell = (ROOT / "scripts/release/build-base.ps1").read_text(encoding="utf-8")
    assert 'install_git "$embedded_runtime/git"' in shell
    assert 'Install-Git (Join-Path $EmbeddedRuntime "git")' in powershell
    for builder in ("build-product-runtime.sh", "build-product-runtime.ps1"):
        assert "--require-git" in (ROOT / "scripts/release" / builder).read_text(encoding="utf-8"), builder


def test_base_python_may_load_modules_built_on_the_users_machine():
    entitlements = plistlib.loads((ROOT / "apps/desktop/build/entitlements.mac.inherit.plist").read_bytes())
    assert entitlements["com.apple.security.cs.disable-library-validation"] is True

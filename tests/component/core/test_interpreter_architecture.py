"""Packages are picked for the Python that loads them, not for the machine under it."""
from __future__ import annotations

from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig

import pytest

from openprogram.host._platform import architecture
from openprogram.host._platform.architecture import interpreter_architecture

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("system", "build", "machine", "multiarch", "expected"),
    [
        # x64 Python on Windows on Arm: the machine says ARM64, the build says amd64.
        ("win32", "win-amd64", "ARM64", None, "x86_64"),
        ("win32", "win-arm64", "ARM64", None, "arm64"),
        ("win32", "win-amd64", "AMD64", None, "x86_64"),
        # A universal2 build runs as one slice; under Rosetta macOS answers x86_64.
        ("darwin", "macosx-10.13-universal2", "x86_64", None, "x86_64"),
        ("darwin", "macosx-10.13-universal2", "arm64", None, "arm64"),
        ("darwin", "macosx-11.0-arm64", "arm64", None, "arm64"),
        ("darwin", "macosx-10.13-x86_64", "x86_64", None, "x86_64"),
        ("linux", "linux-x86_64", "x86_64", "x86_64-linux-gnu", "x86_64"),
        ("linux", "linux-aarch64", "aarch64", "aarch64-linux-gnu", "arm64"),
    ],
)
def test_the_interpreter_build_decides(monkeypatch, system, build, machine, multiarch, expected) -> None:
    monkeypatch.setattr(architecture.sys, "platform", system)
    monkeypatch.setattr(architecture.sysconfig, "get_platform", lambda: build)
    monkeypatch.setattr(architecture.platform, "machine", lambda: machine)
    monkeypatch.setattr(architecture.sysconfig, "get_config_var",
                        lambda name: multiarch if name == "MULTIARCH" else None)
    assert interpreter_architecture() == expected


def test_a_32_bit_python_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(architecture.sys, "platform", "win32")
    monkeypatch.setattr(architecture.sysconfig, "get_platform", lambda: "win32")
    with pytest.raises(RuntimeError, match="not a supported architecture"):
        interpreter_architecture()


def test_this_python_matches_its_own_build() -> None:
    answer = interpreter_architecture()
    build = sysconfig.get_platform()
    if "universal" not in build:
        assert {"amd64": "x86_64", "aarch64": "arm64"}.get(build.rsplit("-", 1)[-1].lower(),
                                                           build.rsplit("-", 1)[-1].lower()) == answer


ROSETTA_PYTHON = Path("/Library/Developer/CommandLineTools/usr/bin/python3")


@pytest.mark.skipif(
    sys.platform != "darwin" or platform.machine() != "arm64" or not ROSETTA_PYTHON.exists()
    or not shutil.which("arch"),
    reason="needs Apple silicon with the universal Command Line Tools Python",
)
@pytest.mark.parametrize("slice_", ["x86_64", "arm64"])
def test_a_universal_python_under_rosetta_is_x86_64(slice_: str) -> None:
    # A real translated process, not a patched answer.
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from openprogram.host._platform.architecture import interpreter_architecture as a; print(a())")
    result = subprocess.run(["arch", f"-{slice_}", str(ROSETTA_PYTHON), "-I", "-c", code, str(ROOT)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode != 0 and "Bad CPU type" in result.stderr:
        pytest.skip("Rosetta is not installed")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == slice_


def test_the_build_and_the_updater_ask_the_interpreter(monkeypatch) -> None:
    from openprogram.host._platform import environment
    from openprogram.operations.self_update.official.repository import current_platform

    # The machine lies the way Windows on Arm does to an x64 Python.
    monkeypatch.setattr(platform, "machine", lambda: "ARM64" if interpreter_architecture() == "x86_64" else "AMD64")
    assert environment.managed_release_target()[1] == interpreter_architecture()
    assert current_platform().endswith("-" + interpreter_architecture())
    target = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/release/runtime-wheels.py"), "target"],
                            capture_output=True, text=True, check=True).stdout.strip()
    assert target.endswith("-" + interpreter_architecture())


@pytest.mark.parametrize("relative", [
    "scripts/release/runtime-wheels.py",
    "scripts/release/verify-product-runtime.py",
    "openprogram/operations/self_update/official/repository.py",
    "openprogram/operations/self_update/delivery/controller_bundle.py",
])
def test_nothing_that_picks_a_package_reads_the_machine(relative: str) -> None:
    assert "machine()" not in (ROOT / relative).read_text(encoding="utf-8")

#!/usr/bin/env python3
"""Compile the launcher a release ships.

macOS gets the named helper bundle whose one path and signature carry the
privacy grants; Windows and Linux get one executable.

    build-launcher.py --python BIN --output DIR [--icon FILE]
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

NAME = "OpenProgram"
IDENTIFIER = "ai.openprogram.runtime"
SOURCE = Path(__file__).with_name("runtime-launcher.c")


def _include_directory(python: Path) -> str:
    return subprocess.check_output(
        [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_paths()['include'])"], text=True,
    ).strip()


def _privacy() -> dict[str, str]:
    from openprogram.host.system_access import package_usage_declarations

    return package_usage_declarations()


def build_macos(python: Path, output: Path, icon: Path | None) -> Path:
    bundle = output / f"{NAME}.app"
    shutil.rmtree(bundle, ignore_errors=True)
    executable = bundle / "Contents" / "MacOS" / NAME
    executable.parent.mkdir(parents=True)
    resources = bundle / "Contents" / "Resources"
    resources.mkdir()
    properties = {
        "CFBundleIdentifier": IDENTIFIER, "CFBundleName": NAME, "CFBundleDisplayName": NAME,
        "CFBundleExecutable": NAME, "CFBundlePackageType": "APPL", "CFBundleVersion": "1",
        "LSUIElement": True, "NSHighResolutionCapable": True, **_privacy(),
    }
    if icon is not None:
        shutil.copyfile(icon, resources / "icon.icns")
        properties["CFBundleIconFile"] = "icon.icns"
    with (bundle / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump(properties, stream, sort_keys=True)
    subprocess.run(
        ["clang", "-O2", f"-I{_include_directory(python)}", "-mmacosx-version-min=11.0",
         str(SOURCE), "-o", str(executable)],
        check=True,
    )
    subprocess.run(
        ["codesign", "--force", "--sign", "-", "--identifier", IDENTIFIER, "--timestamp=none", str(bundle)],
        check=True,
    )
    return bundle


def windows_compile_script(vcvars: Path | None, architecture: str, source: Path, target: Path) -> str:
    """A batch file that compiles, loading Visual Studio's environment if asked."""
    lines = ["@echo off"]
    if vcvars is not None:
        lines.append(f'call "{vcvars}" {architecture} >nul || exit /b 1')
    # cl links the C runtime statically by default, so a clean PC needs nothing.
    lines.append(f'cl /nologo /O2 "{source}" /link "/out:{target}" || exit /b 1')
    return "\r\n".join(lines) + "\r\n"


def _find_vcvars() -> Path:
    installer = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    found = subprocess.run(
        [str(installer / "Microsoft Visual Studio/Installer/vswhere.exe"), "-latest", "-products", "*",
         "-find", r"VC\Auxiliary\Build\vcvarsall.bat"],
        capture_output=True, text=True,
    ).stdout.strip().splitlines()
    if not found:
        raise SystemExit("Visual Studio's C++ tools are required to build the Windows launcher; install them and run this again.")
    return Path(found[0])


def _build_windows(output: Path) -> Path:
    target = output / "openprogram-launcher.exe"
    machine = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64")
    vcvars = None if shutil.which("cl") else _find_vcvars()
    script = output / "compile-launcher.bat"
    script.write_text(windows_compile_script(vcvars, "arm64" if machine.upper() == "ARM64" else "x64", SOURCE, target), encoding="ascii")
    subprocess.run(["cmd", "/d", "/c", str(script)], check=True, cwd=output)
    script.unlink()
    return target


def build_executable(output: Path) -> Path:
    if os.name == "nt":
        return _build_windows(output)
    target = output / "openprogram-launcher"
    subprocess.run(["cc", "-O2", str(SOURCE), "-o", str(target), "-ldl"], check=True)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python", type=Path, required=True, help="The base's Python, which fixes the minor version.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--icon", type=Path)
    arguments = parser.parse_args(argv)
    arguments.output.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        print(build_macos(arguments.python.resolve(), arguments.output.resolve(), arguments.icon))
    else:
        print(build_executable(arguments.output.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

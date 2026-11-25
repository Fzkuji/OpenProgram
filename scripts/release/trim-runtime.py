#!/usr/bin/env python3
"""Removes what a shipped runtime carries but never runs.

    trim-runtime.py packages PYTHON   test suites and debug symbols the
                                      installed packages bring along
    trim-runtime.py git DIRECTORY     Git Credential Manager from a dugite
                                      Git (macOS and Linux)

In the 1.5.1 macOS App these came to about 130 MB of 1.4 GB: package test
suites 11 MB, PyObjC's own test package 16 MB, debug symbols 24 MB and Git
Credential Manager with its .NET runtime 76 MB. Nothing in OpenProgram imports
a package's tests, and the dugite Git names no credential helper, so Git never
starts the manager; HTTPS credentials come from the helper a user configures.
The Windows MinGit keeps its manager: there it is the configured helper.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# Top-level packages that are test suites only.
_TEST_PACKAGES = ("PyObjCTest",)
# Directories inside a package that hold its test suite.
_TEST_DIRECTORIES = ("tests", "test")

# Git Credential Manager in dugite-native's libexec/git-core: the program, the
# .NET runtime and UI libraries it runs on, and its translations.
_MANAGER_PREFIXES = ("git-credential-manager",)
_MANAGER_SUFFIXES = (".dll", ".dylib", ".so", ".pdb")
_MANAGER_FILES = ("createdump", "NOTICE", "uninstall.sh")


def _size(path: Path) -> int:
    if path.is_file() or path.is_symlink():
        return path.lstat().st_size
    return sum(p.lstat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def _remove(path: Path) -> int:
    size = _size(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    return size


def site_packages(python: str) -> list[Path]:
    output = subprocess.run(
        [python, "-I", "-c", "import site, sys; print('\\n'.join(site.getsitepackages()))"],
        check=True, capture_output=True, text=True,
    ).stdout
    # On Windows the list also names the interpreter's own prefix.
    return [Path(line) for line in output.splitlines()
            if line and Path(line).name == "site-packages" and Path(line).is_dir()]


def trim_packages(root: Path) -> int:
    removed = 0
    for name in _TEST_PACKAGES:
        if (root / name).is_dir():
            removed += _remove(root / name)
    for package in sorted(p for p in root.iterdir() if p.is_dir()):
        if package.name.endswith((".dist-info", ".data")):
            continue
        for directory in sorted(package.rglob("*"), key=lambda p: len(p.parts)):
            if not directory.is_dir() or directory.is_symlink() or not directory.exists():
                continue
            if directory.name in _TEST_DIRECTORIES or directory.suffix == ".dSYM":
                removed += _remove(directory)
    return removed


def trim_git(git: Path) -> int:
    core = git / "libexec" / "git-core"
    if not (core / "git-credential-manager").exists():
        return 0
    removed = 0
    for entry in sorted(core.iterdir()):
        if entry.name.startswith(_MANAGER_PREFIXES) or entry.name in _MANAGER_FILES:
            removed += _remove(entry)
        elif entry.is_file() and entry.suffix in _MANAGER_SUFFIXES:
            removed += _remove(entry)
        elif entry.is_dir() and not entry.is_symlink() and entry.name != "mergetools":
            # Translation folders (de, zh-Hans, ...) hold only the manager's
            # resource DLLs.
            if all(p.suffix == ".dll" for p in entry.rglob("*") if p.is_file()):
                removed += _remove(entry)
    if not (core / "git").exists() and not (git / "bin" / "git").exists():
        raise SystemExit(f"trimming removed Git itself from {git}")
    return removed


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("packages", "git"):
        print(__doc__, file=sys.stderr)
        return 2
    if argv[0] == "packages":
        removed = sum(trim_packages(root) for root in site_packages(argv[1]))
    else:
        removed = trim_git(Path(argv[1]))
    print(f"trim-runtime: removed {removed / 1048576:.1f} MB of {argv[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
